"""Bounded multi-region probes, incident lifecycle and opt-in digest jobs.
Never executes remote shell commands. DB and scoring work run off asyncio loop.
"""
from __future__ import annotations
import asyncio
import logging
import os
import re
import hashlib
from datetime import datetime,timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pro_store import ProStore
from pro_scoring import likely_cause,score_config
from tunnel_logic import tunnel_manager
from settings import SUPER_ADMIN_ID

log=logging.getLogger(__name__)
store=ProStore()
_RUNNING=set()


def _snapshot():
    """Durable keyset cursor: never starve owners beyond the first SQL page.

    A bounded batch is allocated per job invocation; job queue runs one worker.
    """
    batch=max(1,min(100,int(os.getenv('SONAR_PROBE_OWNER_BATCH','10'))))
    with store.db.get_connection() as (conn,cur):
        cur.execute("""SELECT s.id,s.ip,s.ws_port,s.password,s.name,s.location_type,s.owner_id
                       FROM servers s WHERE s.is_monitor_node=1 AND s.is_active=1
                       ORDER BY s.id""")
        monitors=[dict(r) for r in cur.fetchall()]
        cur.execute("""INSERT INTO pro_job_cursors(name,last_id) VALUES('multi_probe',0)
                    ON CONFLICT DO NOTHING""")
        cur.execute("SELECT last_id FROM pro_job_cursors WHERE name='multi_probe' FOR UPDATE")
        last_id=int(cur.fetchone()['last_id'])
        cur.execute("""SELECT p.owner_id FROM pro_preferences p JOIN users u ON u.user_id=p.owner_id
            WHERE p.multi_probe=TRUE AND u.is_banned=0 AND p.owner_id>%s
            ORDER BY p.owner_id LIMIT %s""",(last_id,batch))
        users=[int(r['owner_id']) for r in cur.fetchall()]
        if len(users)<batch:
            cur.execute("""SELECT p.owner_id FROM pro_preferences p JOIN users u ON u.user_id=p.owner_id
                WHERE p.multi_probe=TRUE AND u.is_banned=0 AND p.owner_id<=%s
                ORDER BY p.owner_id LIMIT %s""",(last_id,batch-len(users)))
            users.extend(int(r['owner_id']) for r in cur.fetchall())
        if users:
            cur.execute("UPDATE pro_job_cursors SET last_id=%s,updated_at=NOW() WHERE name='multi_probe'",(users[-1],))
        conn.commit()
        return monitors,users


def _rotate_monitors(uid, monitors, limit=2):
    """Persist per-owner monitor rotation so restarts cannot starve later nodes."""
    if not monitors:return []
    monitors=sorted(monitors,key=lambda row:row['id'])
    key=f'multi_mon:{int(uid)}'
    with store.db.get_connection() as (conn,cur):
        cur.execute("""INSERT INTO pro_job_cursors(name,last_id) VALUES(%s,0)
            ON CONFLICT DO NOTHING""",(key,))
        cur.execute('SELECT last_id FROM pro_job_cursors WHERE name=%s FOR UPDATE',(key,))
        last_id=int(cur.fetchone()['last_id'])
        after=[item for item in monitors if item['id']>last_id]
        before=[item for item in monitors if item['id']<=last_id]
        chosen=(after+before)[:min(limit,len(monitors))]
        cur.execute('UPDATE pro_job_cursors SET last_id=%s,updated_at=NOW() WHERE name=%s',
                    (chosen[-1]['id'],key))
        conn.commit()
    return chosen


def _select_configs(uid):
    """Durable, bounded config keyset selection; safe across bot restarts."""
    key=f'multi_cfg:{int(uid)}'
    with store.db.get_connection() as (conn,cur):
        cur.execute("INSERT INTO pro_job_cursors(name,last_id) VALUES(%s,0) ON CONFLICT DO NOTHING",(key,))
        cur.execute('SELECT last_id FROM pro_job_cursors WHERE name=%s FOR UPDATE',(key,))
        last=int(cur.fetchone()['last_id'])
        cur.execute("""SELECT id,link FROM tunnel_configs
            WHERE owner_id=%s AND type IN ('single','sub_item','json') AND id>%s
            ORDER BY id LIMIT 8""",(uid,last))
        rows=[dict(r) for r in cur.fetchall()]
        if len(rows)<8:
            cur.execute("""SELECT id,link FROM tunnel_configs
                WHERE owner_id=%s AND type IN ('single','sub_item','json') AND id<=%s
                ORDER BY id LIMIT %s""",(uid,last,8-len(rows)))
            rows.extend(dict(r) for r in cur.fetchall())
        if rows:
            cur.execute('UPDATE pro_job_cursors SET last_id=%s,updated_at=NOW() WHERE name=%s',
                        (rows[-1]['id'],key))
        conn.commit()
        return rows


def _parse_speed(v):
    try:
        if isinstance(v,(int,float)):return max(0,float(v))
        m=re.match(r'^\s*(\d+(?:\.\d+)?)\s*(k|m|g)?bps',str(v),re.I)
        if not m:return None
        return float(m.group(1))*{'k':.001,'m':1,'g':1000,None:1}[m.group(2).lower() if m.group(2) else None]
    except (ValueError,OverflowError):return None


def _record(uid,cid,region,data):
    ok=isinstance(data,dict) and data.get('status')=='OK'
    try:ping=float(data.get('ping') or 0) if ok else None
    except (ValueError,TypeError):ping=None
    try:jitter=float(data.get('jitter') or 0) if ok else None
    except (ValueError,TypeError):jitter=None
    store.record_metric(uid,cid,region,ok,ping,jitter,
                        _parse_speed(data.get('down')) if ok else None,
                        _parse_speed(data.get('up')) if ok else None)
    with store.db.get_connection() as(conn,cur):
        cur.execute('''SELECT ok FROM pro_metrics WHERE owner_id=%s AND config_id=%s AND region=%s
                       ORDER BY id DESC LIMIT 2''',(uid,cid,region))
        latest=cur.fetchall()
    # Two consecutive failures required to raise; first success resolves.
    incident_key='probe_'+hashlib.sha256(region.encode()).hexdigest()[:12]
    if ok:
        return store.incident(uid,'config',cid,incident_key,f'اختلال کانفیگ #{cid}',active=False)
    if len(latest)>=2 and not latest[0]['ok'] and not latest[1]['ok']:
        return store.incident(uid,'config',cid,incident_key,f'اختلال کانفیگ #{cid} از {region}',severity='warning')
    return None


def quiet_now(prefs):
    a,b=prefs.get('quiet_start'),prefs.get('quiet_end')
    if a is None or b is None or a==b:return False
    try:
        tz=ZoneInfo(prefs.get('timezone') or 'UTC')
    except (ZoneInfoNotFoundError,ValueError,TypeError):
        tz=timezone.utc
    hour=datetime.now(tz).hour
    return a<=hour<b if a<b else hour>=a or hour<b

async def _do_probe(uid,cfg,monitor,sem,user_sem,context=None):
    async with user_sem:
        async with sem:
            try:
                ok,data=await asyncio.wait_for(tunnel_manager._test_via_ws(monitor,cfg['link'],size=.25,timeout=25),timeout=32)
                result=data if ok and isinstance(data,dict) else {'status':'Fail'}
            except Exception:
                result={'status':'Fail'}
            region=f"{monitor.get('location_type') or 'unknown'}:{monitor.get('name') or monitor['id']}:{monitor['id']}"[:80]
            try:
                incident=await asyncio.to_thread(_record,uid,cfg['id'],region,result)
                if context and incident and (incident['occurrences']==1 or result.get('status')=='OK'):
                    event='recovery' if result.get('status')=='OK' else 'probe_failure'
                    # Enqueue even during quiet hours; the persistent dispatcher
                    # makes the delivery decision when the restriction has ended.
                    message=(f'✅ بازیابی کانفیگ #{cfg["id"]} در {region} تأیید شد.'
                             if result.get('status')=='OK' else
                             f'🚨 هشدار سونار: کانفیگ #{cfg["id"]} دو بار متوالی از {region} ناموفق بود.')
                    key=f'probe:{incident["id"]}:{event}'
                    await asyncio.to_thread(store.db.execute,
                        '''INSERT INTO pro_notification_outbox(owner_id,event_key,event_type,message)
                           VALUES(%s,%s,%s,%s) ON CONFLICT(event_key) DO NOTHING''',
                        (uid,key,event,message))
            except Exception:log.exception('Probe persistence error user=%s config=%s',uid,cfg['id'])


async def multi_probe_job(context):
    if 'probe' in _RUNNING:return
    _RUNNING.add('probe')
    try:
        monitors,users=await asyncio.to_thread(_snapshot)
        if not monitors or not users:return
        # _snapshot already claims a bounded and fair persisted owner batch.
        global_sem=asyncio.Semaphore(6)
        # Round robin users: don't let one owner's batch block every other owner.
        user_jobs=[]
        for uid in users:
            allowed,_=await asyncio.to_thread(store.db.check_access,uid)
            if not allowed:continue
            configs=await asyncio.to_thread(_select_configs,uid)
            if not configs:continue
            user_sem=asyncio.Semaphore(2)
            permitted=await asyncio.to_thread(_rotate_monitors,uid,[m for m in monitors if m['owner_id'] in (uid,SUPER_ADMIN_ID)])
            if not permitted:continue
            user_jobs.append((uid,configs,user_sem,permitted))
        # Limit to 2 regions for bounded cost; labels originate from configured monitor nodes.
        workers=[]
        for j in range(max((len(configs) for _,configs,_,_ in user_jobs),default=0)):
            for uid,configs,user_sem,permitted in user_jobs:
                if j<len(configs):
                    for monitor in permitted:
                        workers.append(asyncio.create_task(_do_probe(uid,configs[j],monitor,global_sem,user_sem,context)))
        if workers:await asyncio.gather(*workers,return_exceptions=True)
    except Exception:log.exception('Multi region probe job failed')
    finally:_RUNNING.remove('probe')


async def digest_job(context):
    if 'digest' in _RUNNING:return
    _RUNNING.add('digest')
    try:
        rows=await asyncio.to_thread(store.digest_due)
        for r in rows:
            uid=r['owner_id']
            try:
                allowed,_=await asyncio.to_thread(store.db.check_access,uid)
                if not allowed:continue
                pref=await asyncio.to_thread(store.preferences,uid)
                if quiet_now(pref):continue
                data=await asyncio.to_thread(store.summary,uid)
                c=data['configs'];s=data['servers']
                msg=(f'📊 گزارش روزانه سونار\n🖥 سرور: {s["active"]}/{s["total"]} فعال\n'
                     f'🧩 کانفیگ: {c["healthy"]}/{c["total"]} سالم\n'
                     f'🚨 رخداد باز: {data["incidents"]}')
                await context.bot.send_message(uid,msg)
                await asyncio.to_thread(store.mark_digest,uid)
            except Exception:log.exception('Daily digest failed for %s',uid)
    finally:_RUNNING.remove('digest')

async def cleanup_job(context):
    try:
        await asyncio.to_thread(store.cleanup)
        from service_monitor import ServiceStore
        await asyncio.to_thread(ServiceStore(store).cleanup)
    except Exception:log.exception('Pro retention cleanup failed')
