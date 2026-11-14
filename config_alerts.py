"""Durable configuration alerts and bounded, opt-in scheduled probes.

Every ordinary/manual/subscription/multi-region test feeds pro_metrics. A durable
cursor consumes each sample once; state and notification enqueue commit together.
No raw configuration links or SSH credentials are placed in notification payloads.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pro_scoring import score_config

log = logging.getLogger(__name__)


class AlertSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    notify_down: bool = True
    notify_recovery: bool = True
    notify_score: bool = False
    failure_threshold: int = Field(default=2, ge=1, le=10)
    score_delta: int = Field(default=20, ge=5, le=100)
    cooldown_minutes: int = Field(default=15, ge=1, le=1440)
    scheduled: bool = False
    probe_interval_minutes: int = Field(default=10, ge=2, le=1440)
    report_enabled: bool = False
    report_interval_minutes: int = Field(default=60, ge=15, le=10080)
    quiet_start: int | None = Field(default=None, ge=0, le=23)
    quiet_end: int | None = Field(default=None, ge=0, le=23)
    timezone: str = Field(default='Asia/Tehran', max_length=64)
    muted_config_ids: list[int] = Field(default_factory=list, max_length=500)

    @model_validator(mode='after')
    def complete_quiet_window(self):
        if (self.quiet_start is None)!=(self.quiet_end is None):
            raise ValueError('شروع و پایان ساعت سکوت را با هم تعیین کنید')
        return self

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, KeyError):
            raise ValueError('منطقه زمانی معتبر نیست') from None
        return value

    @field_validator('muted_config_ids')
    @classmethod
    def valid_ids(cls, value):
        if any(item <= 0 for item in value):
            raise ValueError('شناسه کانفیگ باید مثبت باشد')
        return sorted(set(value))


def install_schema(db):
    with db.get_connection() as (conn, cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS config_alert_settings (
          owner_id BIGINT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT TRUE,
          notify_down BOOLEAN NOT NULL DEFAULT TRUE, notify_recovery BOOLEAN NOT NULL DEFAULT TRUE,
          notify_score BOOLEAN NOT NULL DEFAULT FALSE,
          failure_threshold INTEGER NOT NULL DEFAULT 2 CHECK(failure_threshold BETWEEN 1 AND 10),
          score_delta INTEGER NOT NULL DEFAULT 20 CHECK(score_delta BETWEEN 5 AND 100),
          cooldown_minutes INTEGER NOT NULL DEFAULT 15 CHECK(cooldown_minutes BETWEEN 1 AND 1440),
          scheduled BOOLEAN NOT NULL DEFAULT FALSE,
          probe_interval_minutes INTEGER NOT NULL DEFAULT 10 CHECK(probe_interval_minutes BETWEEN 2 AND 1440),
          report_enabled BOOLEAN NOT NULL DEFAULT FALSE,
          report_interval_minutes INTEGER NOT NULL DEFAULT 60 CHECK(report_interval_minutes BETWEEN 15 AND 10080),
          quiet_start SMALLINT, quiet_end SMALLINT, timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Tehran',
          muted_config_ids INTEGER[] NOT NULL DEFAULT '{}',
          next_probe_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), next_report_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
          last_config_id INTEGER NOT NULL DEFAULT 0)''')
        cur.execute('''CREATE TABLE IF NOT EXISTS config_alert_state (
          config_id INTEGER NOT NULL REFERENCES tunnel_configs(id) ON DELETE CASCADE,
          region VARCHAR(80) NOT NULL, last_metric_id BIGINT NOT NULL DEFAULT 0,
          failures INTEGER NOT NULL DEFAULT 0, stable_status VARCHAR(12) NOT NULL DEFAULT 'unknown',
          score_anchor DOUBLE PRECISION, last_score_at TIMESTAMPTZ,
          PRIMARY KEY(config_id,region))''')
        # Do not replay historical samples as new alerts after an upgrade.
        cur.execute("""INSERT INTO pro_job_cursors(name,last_id)
          SELECT 'config_alert_metrics',COALESCE(MAX(id),0) FROM pro_metrics ON CONFLICT DO NOTHING""")
        conn.commit()


def settings(db, owner):
    row = db.execute('SELECT * FROM config_alert_settings WHERE owner_id=%s', (owner,), fetch='one')
    defaults = AlertSettings().model_dump()
    return {key: row[key] if row else value for key, value in defaults.items()}


def save_settings(pro, actor, owner, values):
    pro.require(actor, owner, write=True)
    validated = AlertSettings.model_validate(values).model_dump()
    muted = validated['muted_config_ids']
    if muted:
        found = pro.db.execute('SELECT id FROM tunnel_configs WHERE owner_id=%s AND id=ANY(%s)',
                               (owner, muted), fetch='all')
        if {row['id'] for row in found} != set(muted):
            raise PermissionError('کانفیگ خارج از فضای کاری قابل تنظیم نیست')
    # The column names come exclusively from this model's allowlist.
    keys = list(AlertSettings.model_fields)
    placeholders = ','.join(['%s'] * (len(keys) + 1))
    updates = ','.join(f'{key}=EXCLUDED.{key}' for key in keys)
    pro.db.execute(f'''INSERT INTO config_alert_settings(owner_id,{','.join(keys)}) VALUES({placeholders})
        ON CONFLICT(owner_id) DO UPDATE SET {updates},next_probe_at=NOW(),next_report_at=NOW()''',
        tuple([owner] + [validated[key] for key in keys]))
    pro.audit(owner, actor, 'config_alert_settings')
    return validated


def delivery_policy(db, owner, event, event_key=""):
    """Recheck switches on delivery, including already queued notifications."""
    if not event.startswith('config_'):
        return True, False
    from pro_jobs import quiet_now
    prefs = settings(db, owner)
    flag = {'config_down': 'notify_down', 'config_recovery': 'notify_recovery',
            'config_score': 'notify_score', 'config_report': 'report_enabled'}.get(event)
    parts = event_key.split(':')
    muted = len(parts)>2 and parts[0]=='config' and parts[1].isdigit() and int(parts[1]) in prefs['muted_config_ids']
    return bool(prefs['enabled'] and flag and prefs[flag] and not muted), quiet_now(prefs)


def _enqueue(cur, owner, key, event, message):
    cur.execute('''INSERT INTO pro_notification_outbox(owner_id,event_key,event_type,message)
       VALUES(%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING''', (owner, key, event, message))


def _apply_metric(cur, row, prefs):
    cid, region, metric = row['config_id'], row['region'], row['id']
    cur.execute('INSERT INTO config_alert_state(config_id,region) VALUES(%s,%s) ON CONFLICT DO NOTHING', (cid, region))
    cur.execute('SELECT * FROM config_alert_state WHERE config_id=%s AND region=%s FOR UPDATE', (cid, region))
    previous = cur.fetchone()
    if metric <= previous['last_metric_id']:
        return
    success = bool(row['ok'])
    failures = 0 if success else min(1000000, previous['failures'] + 1)
    stable = previous['stable_status']
    event = None
    if success:
        if stable == 'down':
            event = 'config_recovery'
        stable = 'up'
    elif failures >= prefs['failure_threshold']:
        if stable != 'down':
            event = 'config_down'
        stable = 'down'
    score = score_config(row['ping'], row['jitter'], success)
    anchor = previous['score_anchor']
    score_at = previous['last_score_at']
    now = datetime.now(timezone.utc)
    enabled = prefs['enabled'] and cid not in prefs['muted_config_ids']
    name = str(row['name'] or f'کانفیگ #{cid}')[:100]
    label = f'{name} (#{cid})\nنقطه سنجش: {region}\nامتیاز: {score}/100'
    if event and enabled and prefs['notify_recovery' if success else 'notify_down']:
        message = ('✅ اتصال دوباره تأیید شد' if success else f'🚨 {failures} سنجش ناموفق متوالی') + '\n' + label
        _enqueue(cur, row['owner_id'], f'config:{cid}:{metric}:{event}', event, message)
    # Compare against the last announced score, not every tiny intermediate step.
    if success and anchor is not None and abs(score - anchor) >= prefs['score_delta']:
        elapsed = (now - score_at).total_seconds() if score_at else float('inf')
        if elapsed >= prefs['cooldown_minutes'] * 60:
            if enabled and prefs['notify_score'] and not event:
                _enqueue(cur, row['owner_id'], f'config:{cid}:{metric}:score', 'config_score',
                         f'📊 تغییر کیفیت از {anchor:g} به {score:g}/100\n{label}')
            anchor, score_at = score, now
    if anchor is None or event:
        anchor, score_at = score, now
    cur.execute('''UPDATE config_alert_state SET last_metric_id=%s,failures=%s,stable_status=%s,
        score_anchor=%s,last_score_at=%s WHERE config_id=%s AND region=%s''',
        (metric, failures, stable, anchor, score_at, cid, region))


def consume_metrics(db):
    """Atomic cursor + transitions + outbox, serialized across bot processes."""
    with db.get_connection() as (conn, cur):
        cur.execute("SELECT last_id FROM pro_job_cursors WHERE name='config_alert_metrics' FOR UPDATE")
        cursor = cur.fetchone()
        if cursor is None:
            raise RuntimeError('Configuration alert migration has not run')
        cur.execute('''SELECT m.id,m.owner_id,m.config_id,m.region,m.ok,m.ping,m.jitter,c.name
          FROM pro_metrics m JOIN tunnel_configs c ON c.id=m.config_id AND c.owner_id=m.owner_id
          WHERE m.id>%s ORDER BY m.id LIMIT 500''', (cursor['last_id'],))
        rows = cur.fetchall()
        cached = {}
        for row in rows:
            owner = row['owner_id']
            if owner not in cached:
                cur.execute('SELECT * FROM config_alert_settings WHERE owner_id=%s', (owner,))
                saved = cur.fetchone()
                cached[owner] = dict(saved) if saved else AlertSettings().model_dump()
            _apply_metric(cur, row, cached[owner])
        if rows:
            cur.execute("UPDATE pro_job_cursors SET last_id=%s,updated_at=NOW() WHERE name='config_alert_metrics'", (rows[-1]['id'],))
        conn.commit()
    return len(rows)


def claim_scheduled(db):
    with db.get_connection() as (conn, cur):
        cur.execute('''SELECT owner_id,last_config_id FROM config_alert_settings WHERE enabled AND scheduled
            AND next_probe_at<=NOW() ORDER BY next_probe_at FOR UPDATE SKIP LOCKED LIMIT 5''')
        rows = [dict(row) for row in cur.fetchall()]
        for row in rows:
            cur.execute("""UPDATE config_alert_settings SET next_probe_at=NOW()+(probe_interval_minutes*INTERVAL '1 minute')
                WHERE owner_id=%s""", (row['owner_id'],))
        conn.commit()
    return rows


def report_due(db):
    with db.get_connection() as (conn, cur):
        cur.execute('''SELECT owner_id,next_report_at FROM config_alert_settings WHERE enabled AND report_enabled
           AND next_report_at<=NOW() ORDER BY next_report_at FOR UPDATE SKIP LOCKED LIMIT 10''')
        for row in cur.fetchall():
            cur.execute("""SELECT name,last_status,last_ping,last_jitter FROM tunnel_configs
                WHERE owner_id=%s AND type IN ('single','sub_item','json') ORDER BY id LIMIT 100""", (row['owner_id'],))
            configs = cur.fetchall()
            lines = [f"{'✅' if cfg['last_status']=='OK' else '❌' if cfg['last_status']=='Fail' else '⚪'} {str(cfg['name'])[:60]}: "
                     f"{score_config(cfg['last_ping'],cfg['last_jitter'],cfg['last_status']=='OK')}/100" for cfg in configs[:25]]
            message = '📋 گزارش دوره‌ای کانفیگ‌ها؛ بر اساس آخرین سنجش ثبت‌شده\n' + ('\n'.join(lines) or 'هنوز کانفیگی ثبت نشده است.')
            if len(configs)>25:
                message += '\nبرای مشاهده تمام کانفیگ‌ها پنل را باز کنید.'
            _enqueue(cur, row['owner_id'], f"config-report:{row['owner_id']}:{row['next_report_at'].isoformat()}", 'config_report', message)
            cur.execute("""UPDATE config_alert_settings SET next_report_at=NOW()+(report_interval_minutes*INTERVAL '1 minute')
                WHERE owner_id=%s""", (row['owner_id'],))
        conn.commit()


_running = False
_probing = False


async def config_alert_job(context):
    global _running
    if _running:
        return
    _running = True
    try:
        from pro_store import ProStore
        from service_jobs import flush_notifications
        pro = ProStore()
        # Deliver transitions from manual/cron tests without waiting for a probe.
        await asyncio.to_thread(consume_metrics, pro.db)
        await asyncio.to_thread(report_due, pro.db)
        await flush_notifications(context, pro)
    except Exception:
        log.exception('Configuration alert cycle failed')
    finally:
        _running = False


async def scheduled_config_probe_job(context):
    global _probing
    if _probing:
        return
    _probing = True
    try:
        from pro_store import ProStore
        from tunnel_logic import tunnel_manager
        from monitor_fleet import select_monitor_row
        from settings import SUPER_ADMIN_ID
        from pro_jobs import _record
        pro = ProStore()
        owners = await asyncio.to_thread(claim_scheduled, pro.db)
        sem = asyncio.Semaphore(3)

        async def probe(row, monitor, owner):
            async with sem:
                try:
                    ok, data = await asyncio.wait_for(tunnel_manager._test_via_ws(monitor, row['link'], size=.25, timeout=30), timeout=35)
                    if ok and isinstance(data, dict) and data.get('status') in ('OK', 'Fail'):
                        await asyncio.to_thread(_record, owner, row['id'], f"scheduled:{monitor['id']}", data)
                except Exception:
                    log.warning('Scheduled config test unavailable config=%s', row['id'])

        tasks = []
        for owner_row in owners:
            owner = owner_row['owner_id']
            allowed, _ = await asyncio.to_thread(pro.db.check_access, owner)
            if not allowed:
                continue
            monitor = await asyncio.to_thread(select_monitor_row, pro.db, owner, SUPER_ADMIN_ID)
            if not monitor:
                continue
            configs = await asyncio.to_thread(pro.db.execute, '''SELECT id,link FROM tunnel_configs
                WHERE owner_id=%s AND monitoring_enabled=TRUE AND sonar_monitor_allowed(owner_id,'configs',id) AND type IN ('single','sub_item','json') ORDER BY (id>%s) DESC,id LIMIT 8''',
                (owner, owner_row['last_config_id']), fetch='all')
            if configs:
                await asyncio.to_thread(pro.db.execute, 'UPDATE config_alert_settings SET last_config_id=%s WHERE owner_id=%s', (configs[-1]['id'], owner))
                tasks.extend(probe(cfg, monitor, owner) for cfg in configs)
        if tasks:
            await asyncio.gather(*tasks)
        await asyncio.to_thread(consume_metrics, pro.db)
    except Exception:
        log.exception('Scheduled configuration test cycle failed')
    finally:
        _probing = False
