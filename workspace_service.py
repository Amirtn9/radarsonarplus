"""Opt-in, reciprocal, atomic shared workspace for technical resources.

Financial user data never enters this workspace. The existing agent and WebSocket
implementation are not modified. PostgreSQL transactions protect merge decisions.
"""
from __future__ import annotations
from datetime import datetime
import ipaddress
import re

SCHEMA = '''
CREATE TABLE IF NOT EXISTS sonar_workspaces (
    member_id BIGINT PRIMARY KEY,
    owner_id BIGINT NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK(member_id > 0 AND owner_id > 0)
);
CREATE INDEX IF NOT EXISTS sonar_workspaces_owner_idx ON sonar_workspaces(owner_id);
CREATE TABLE IF NOT EXISTS sonar_workspace_requests (
    id BIGSERIAL PRIMARY KEY,
    requester BIGINT NOT NULL,
    target BIGINT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','accepted','declined','expired')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '48 hours'),
    decided_at TIMESTAMPTZ,
    CHECK(requester <> target)
);
CREATE INDEX IF NOT EXISTS sonar_workspace_pending_idx
    ON sonar_workspace_requests(target, state);
'''

class WorkspaceError(ValueError):
    pass


def validate_ip(value):
    """Only a literal IP is accepted, no hostname/URL/shell tokens."""
    try:
        return str(ipaddress.ip_address(value.strip()))
    except (ValueError, AttributeError):
        raise WorkspaceError('یک IPv4 یا IPv6 معتبر وارد کنید؛ مانند 1.2.3.4.')


class WorkspaceService:
    def __init__(self, db):
        self.db = db

    def owner(self, user_id):
        uid=int(user_id)
        row=self.db.execute('SELECT owner_id FROM sonar_workspaces WHERE member_id=%s',(uid,),fetch='one')
        return int(row['owner_id']) if row else uid

    def members(self, user_id):
        owner=self.owner(user_id)
        rows=self.db.execute('SELECT member_id FROM sonar_workspaces WHERE owner_id=%s ORDER BY member_id',(owner,),fetch='all')
        return [int(r['member_id']) for r in rows] if rows else [owner]

    def request(self, actor, target):
        actor,target=int(actor),int(target)
        if actor<=0 or target<=0 or actor==target:
            raise WorkspaceError('آیدی عددی معتبر و متفاوت وارد کنید.')
        with self.db.get_connection() as (conn, cur):
            cur.execute('SELECT pg_advisory_xact_lock(641400)')
            cur.execute('SELECT 1 FROM users WHERE user_id=%s',(target,))
            if not cur.fetchone():
                raise WorkspaceError('این کاربر ابتدا باید ربات را /start کند.')
            cur.execute('SELECT owner_id FROM sonar_workspaces WHERE member_id=%s',(actor,))
            row=cur.fetchone()
            if row and int(row['owner_id'])!=actor:
                raise WorkspaceError('فقط مدیر فضای کاری می‌تواند درخواست هم‌گروهی بفرستد.')
            cur.execute('SELECT owner_id FROM sonar_workspaces WHERE member_id=%s',(target,))
            if cur.fetchone():
                raise WorkspaceError('کاربر مقصد هم‌اکنون عضو یک فضای کاری است.')
            cur.execute('''SELECT id FROM sonar_workspace_requests
                WHERE requester=%s AND target=%s AND state='pending' AND expires_at>NOW()
                ORDER BY id DESC LIMIT 1''',(actor,target))
            if cur.fetchone():
                raise WorkspaceError('درخواست قبلی هنوز در انتظار پاسخ است.')
            cur.execute('INSERT INTO sonar_workspace_requests(requester,target) VALUES(%s,%s) RETURNING id',(actor,target))
            req=int(cur.fetchone()['id'])
            conn.commit()
            return req

    def cancel_request(self, actor, request_id):
        with self.db.get_connection() as (conn,cur):
            cur.execute("UPDATE sonar_workspace_requests SET state='expired',decided_at=NOW() "
                        "WHERE id=%s AND requester=%s AND state='pending'",
                        (int(request_id),int(actor)))
            conn.commit()

    def decide(self, actor, request_id, approve):
        """Only recipient may accept. Merge all resources in a single SQL transaction."""
        actor=int(actor); request_id=int(request_id)
        with self.db.get_connection() as (conn, cur):
            cur.execute('SELECT pg_advisory_xact_lock(641400)')
            cur.execute('''SELECT * FROM sonar_workspace_requests WHERE id=%s FOR UPDATE''',(request_id,))
            req=cur.fetchone()
            if not req or int(req['target'])!=actor or req['state']!='pending' or req['expires_at']<=datetime.now(req['expires_at'].tzinfo):
                raise WorkspaceError('درخواست معتبر یا فعالی برای شما پیدا نشد.')
            root=int(req['requester'])
            if not approve:
                cur.execute("UPDATE sonar_workspace_requests SET state='declined',decided_at=NOW() WHERE id=%s",(request_id,))
                conn.commit()
                return root,False
            cur.execute('SELECT owner_id FROM sonar_workspaces WHERE member_id=%s',(actor,))
            if cur.fetchone():
                raise WorkspaceError('شما از قبل عضو یک فضای کاری هستید.')
            cur.execute('SELECT owner_id FROM sonar_workspaces WHERE member_id=%s',(root,))
            existing=cur.fetchone()
            if existing and int(existing['owner_id'])!=root:
                raise WorkspaceError('فرستنده دیگر مدیر این فضا نیست.')
            # Reject exact subscription collisions instead of silently dropping configs.
            cur.execute('''SELECT 1 FROM tunnel_configs a JOIN tunnel_configs b
                ON a.type=b.type AND a.link=b.link AND COALESCE(a.parent_sub_id,0)=COALESCE(b.parent_sub_id,0)
                WHERE a.owner_id=%s AND b.owner_id=%s LIMIT 1''',(root,actor))
            if cur.fetchone():
                raise WorkspaceError('کانفیگ یکسان در هر دو حساب وجود دارد. پیش از ادغام، موارد تکراری را مدیریت کنید.')
            # Rename clashing names to preserve both physical servers and groups.
            for table in ('groups','servers'):
                cur.execute(f'SELECT id,name FROM {table} WHERE owner_id=%s ORDER BY id FOR UPDATE',(actor,))
                for item in cur.fetchall():
                    base=str(item['name'] or 'بدون نام')
                    candidate=base
                    suffix=0
                    while True:
                        cur.execute(f'SELECT 1 FROM {table} WHERE owner_id IN (%s,%s) AND name=%s AND id<>%s',(root,actor,candidate,item['id']))
                        if not cur.fetchone(): break
                        suffix+=1
                        candidate=f'{base} (عضو {actor}' + (f'-{suffix}' if suffix>1 else '') + ')'
                    if candidate!=base:
                        cur.execute(f'UPDATE {table} SET name=%s WHERE id=%s',(candidate,item['id']))
                cur.execute(f'UPDATE {table} SET owner_id=%s WHERE owner_id=%s',(root,actor))
            cur.execute('UPDATE tunnel_configs SET owner_id=%s WHERE owner_id=%s',(root,actor))
            # Technical settings prefer the established workspace owner's value.
            cur.execute('''INSERT INTO settings(owner_id,key,value)
                SELECT %s,key,value FROM settings WHERE owner_id=%s
                ON CONFLICT(owner_id,key) DO NOTHING''',(root,actor))
            cur.execute('DELETE FROM settings WHERE owner_id=%s',(actor,))
            cur.execute('UPDATE channels SET owner_id=%s WHERE owner_id=%s',(root,actor))
            # Include related Pro and uptime resources, if the schemas exist.
            # User profiles, payment records and balances remain private.
            for table in ('pro_metrics','pro_audit','pro_rules','pro_server_monitor_state','pro_notification_outbox','pro_services',
                          'pro_service_history'):
                cur.execute('SELECT to_regclass(%s) AS relation',(f'public.{table}',))
                if cur.fetchone()['relation']:
                    cur.execute(f'UPDATE {table} SET owner_id=%s WHERE owner_id=%s',(root,actor))
            for table,fields in (
                ('pro_favorites','resource_type,resource_id,created_at'),
                ('pro_tags','resource_type,resource_id,tag,created_at')):
                cur.execute('SELECT to_regclass(%s) AS relation',(f'public.{table}',))
                if cur.fetchone()['relation']:
                    cur.execute(f'INSERT INTO {table}(owner_id,{fields}) '
                        f'SELECT %s,{fields} FROM {table} WHERE owner_id=%s ON CONFLICT DO NOTHING',(root,actor))
                    cur.execute(f'DELETE FROM {table} WHERE owner_id=%s',(actor,))
            cur.execute("SELECT to_regclass('public.pro_preferences') AS relation")
            if cur.fetchone()['relation']:
                cur.execute('INSERT INTO pro_preferences (owner_id,quiet_start,quiet_end,daily_digest,multi_probe,last_digest_date,timezone) '
                    'SELECT %s,quiet_start,quiet_end,daily_digest,multi_probe,last_digest_date,timezone '
                    'FROM pro_preferences WHERE owner_id=%s ON CONFLICT(owner_id) DO NOTHING',(root,actor))
                cur.execute('DELETE FROM pro_preferences WHERE owner_id=%s',(actor,))
            cur.execute("SELECT to_regclass('public.pro_incidents') AS relation")
            if cur.fetchone()['relation']:
                cur.execute('UPDATE pro_incidents SET owner_id=%s WHERE owner_id=%s',(root,actor))
            cur.execute('INSERT INTO sonar_workspaces(member_id,owner_id) VALUES(%s,%s) ON CONFLICT(member_id) DO NOTHING',(root,root))
            cur.execute('INSERT INTO sonar_workspaces(member_id,owner_id) VALUES(%s,%s)',(actor,root))
            cur.execute("UPDATE sonar_workspace_requests SET state='accepted',decided_at=NOW() WHERE id=%s",(request_id,))
            # Old outgoing requests from newly joined user cannot remain actionable.
            cur.execute("UPDATE sonar_workspace_requests SET state='expired' WHERE state='pending' AND (requester=%s OR target=%s)",(actor,actor))
            conn.commit()
            return root,True

    def change_server_ip(self, actor, sid, address):
        ip=validate_ip(address)
        owner=self.owner(actor)
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT id,name,ip FROM servers WHERE id=%s AND owner_id=%s FOR UPDATE',(int(sid),owner))
            server=cur.fetchone()
            if not server:
                raise WorkspaceError('سرور یافت نشد یا دسترسی ندارید.')
            cur.execute('UPDATE servers SET ip=%s WHERE id=%s',(ip,int(sid)))
            conn.commit()
            return server['name'],server['ip'],ip

# Authenticated Telegram actor is propagated to synchronous PostgreSQL queries.
# This is used only to identify the author of shared resource modifications.
import contextvars
ACTOR_ID = contextvars.ContextVar('sonar_authenticated_actor', default=0)

AUDIT_SCHEMA = '''
CREATE TABLE IF NOT EXISTS sonar_workspace_events (
  id BIGSERIAL PRIMARY KEY,
  owner_id BIGINT NOT NULL,
  actor_id BIGINT NOT NULL,
  resource TEXT NOT NULL,
  action TEXT NOT NULL,
  detail TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  dispatched_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS sonar_workspace_events_pending
  ON sonar_workspace_events(id) WHERE dispatched_at IS NULL;
CREATE TABLE IF NOT EXISTS sonar_workspace_deliveries (
  event_id BIGINT NOT NULL REFERENCES sonar_workspace_events(id) ON DELETE CASCADE,
  member_id BIGINT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TIMESTAMPTZ,
  sent_at TIMESTAMPTZ,
  PRIMARY KEY(event_id,member_id)
);
CREATE INDEX IF NOT EXISTS sonar_deliveries_pending
 ON sonar_workspace_deliveries(event_id) WHERE sent_at IS NULL;

CREATE OR REPLACE FUNCTION sonar_workspace_track() RETURNS TRIGGER AS $$
DECLARE
  owner BIGINT;
  origin BIGINT;
  label TEXT;
  action_name TEXT;
  detail_text TEXT;
BEGIN
  IF TG_OP = 'UPDATE' THEN
    -- Isolate each row type: PostgreSQL may not short-circuit SQL AND.
    IF TG_TABLE_NAME = 'servers' THEN
      IF (NEW.name,NEW.ip,NEW.group_id,NEW.expiry_date,NEW.is_active,NEW.location_type)
         IS NOT DISTINCT FROM
         (OLD.name,OLD.ip,OLD.group_id,OLD.expiry_date,OLD.is_active,OLD.location_type) THEN
        RETURN NEW;
      END IF;
    ELSIF TG_TABLE_NAME = 'tunnel_configs' THEN
      IF (NEW.name,NEW.link,NEW.type,NEW.parent_sub_id)
         IS NOT DISTINCT FROM (OLD.name,OLD.link,OLD.type,OLD.parent_sub_id) THEN
        RETURN NEW;
      END IF;
    ELSIF TG_TABLE_NAME = 'settings' THEN
      IF NEW.value IS NOT DISTINCT FROM OLD.value THEN RETURN NEW; END IF;
      IF NEW.key LIKE 'last_%' THEN RETURN NEW; END IF;
    ELSIF TG_TABLE_NAME = 'groups' THEN
      IF NEW.name IS NOT DISTINCT FROM OLD.name THEN RETURN NEW; END IF;
    ELSIF TG_TABLE_NAME = 'channels' THEN
      IF (NEW.name,NEW.chat_id,NEW.usage_type,NEW.topic_id)
         IS NOT DISTINCT FROM (OLD.name,OLD.chat_id,OLD.usage_type,OLD.topic_id) THEN
        RETURN NEW;
      END IF;
    END IF;
  END IF;
  -- The settings-only OLD.key/NEW.key must not be evaluated on other tables.
  -- SQL boolean expressions need not short-circuit on all plans.
  IF TG_TABLE_NAME = 'settings' THEN
    IF TG_OP = 'DELETE' THEN
      IF COALESCE(OLD.key,'') LIKE 'last_%' THEN RETURN OLD; END IF;
    ELSE
      IF COALESCE(NEW.key,'') LIKE 'last_%' THEN RETURN NEW; END IF;
    END IF;
  END IF;
  owner := CASE WHEN TG_OP='DELETE' THEN OLD.owner_id ELSE NEW.owner_id END;
  -- Don't generate notifications on owner migration or solo workspaces.
  IF TG_OP='UPDATE' THEN
    IF NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN RETURN NEW; END IF;
  END IF;
  IF (SELECT count(*) FROM sonar_workspaces WHERE owner_id=owner) < 2 THEN
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
  END IF;
  origin := COALESCE(NULLIF(current_setting('sonar.actor',true),'')::BIGINT,0);
  -- Background probes/cron tasks have no authenticated actor; do not impersonate the owner.
  IF origin = 0 THEN
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
  END IF;
  action_name := CASE TG_OP WHEN 'INSERT' THEN 'افزودن' WHEN 'DELETE' THEN 'حذف' ELSE 'ویرایش' END;
  IF TG_TABLE_NAME='servers' THEN
    label:='سرور'; detail_text := COALESCE(CASE WHEN TG_OP='DELETE' THEN OLD.name ELSE NEW.name END,'بدون نام');
    IF TG_OP='UPDATE' AND OLD.ip IS DISTINCT FROM NEW.ip THEN
      detail_text := LEFT(detail_text || ': تغییر IP از ' || OLD.ip || ' به ' || NEW.ip,300);
    END IF;
  ELSIF TG_TABLE_NAME='tunnel_configs' THEN
    label:='کانفیگ'; detail_text:=COALESCE(CASE WHEN TG_OP='DELETE' THEN OLD.name ELSE NEW.name END,'بدون نام');
  ELSIF TG_TABLE_NAME='groups' THEN
    label:='پوشه سرورها'; detail_text:=COALESCE(CASE WHEN TG_OP='DELETE' THEN OLD.name ELSE NEW.name END,'بدون نام');
  ELSIF TG_TABLE_NAME='channels' THEN
    label:='کانال اعلان'; detail_text:=COALESCE(CASE WHEN TG_OP='DELETE' THEN OLD.name ELSE NEW.name END,'بدون نام');
  ELSE
    label:='تنظیمات'; detail_text:=COALESCE(CASE WHEN TG_OP='DELETE' THEN OLD.key ELSE NEW.key END,'');
  END IF;
  INSERT INTO sonar_workspace_events(owner_id,actor_id,resource,action,detail)
      VALUES(owner,origin,label,action_name,LEFT(detail_text,300));
  IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
END;
$$ LANGUAGE plpgsql;
'''
for _table in ('servers','tunnel_configs','groups','channels','settings'):
    AUDIT_SCHEMA += f'''DROP TRIGGER IF EXISTS sonar_share_event ON {_table};
CREATE TRIGGER sonar_share_event AFTER INSERT OR UPDATE OR DELETE ON {_table}
FOR EACH ROW EXECUTE FUNCTION sonar_workspace_track();
'''


REDIRECT_SCHEMA = '''
CREATE OR REPLACE FUNCTION sonar_workspace_redirect() RETURNS TRIGGER AS $$
DECLARE routed BIGINT;
BEGIN
  SELECT owner_id INTO routed FROM sonar_workspaces WHERE member_id=NEW.owner_id;
  IF routed IS NOT NULL THEN NEW.owner_id:=routed; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
'''
for _table in ('servers','tunnel_configs','groups','channels','settings'):
    REDIRECT_SCHEMA += f'''DROP TRIGGER IF EXISTS sonar_workspace_route ON {_table};
CREATE TRIGGER sonar_workspace_route BEFORE INSERT ON {_table}
FOR EACH ROW EXECUTE FUNCTION sonar_workspace_redirect();
'''

def claim_events(db, limit=50):
    """Persist one delivery per teammate; retries survive restarts and Telegram outages."""
    with db.get_connection() as (conn,cur):
        cur.execute('''SELECT id FROM sonar_workspace_events
            WHERE dispatched_at IS NULL ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED''',(limit,))
        ids=[int(row['id']) for row in cur.fetchall()]
        if ids:
            cur.execute('''INSERT INTO sonar_workspace_deliveries(event_id,member_id)
                SELECT e.id,w.member_id FROM sonar_workspace_events e
                JOIN sonar_workspaces w ON w.owner_id=e.owner_id AND w.member_id<>e.actor_id
                WHERE e.id=ANY(%s) ON CONFLICT DO NOTHING''',(ids,))
            cur.execute('UPDATE sonar_workspace_events SET dispatched_at=NOW() WHERE id=ANY(%s)',(ids,))
        conn.commit()
    with db.get_connection() as (conn,cur):
        # Claim retries atomically so overlapping job instances do not double-send.
        cur.execute('''WITH pending AS (
          SELECT event_id,member_id FROM sonar_workspace_deliveries
          WHERE sent_at IS NULL AND attempts<5
            AND (last_attempt_at IS NULL OR last_attempt_at<NOW()-INTERVAL '90 seconds')
          ORDER BY event_id LIMIT %s FOR UPDATE SKIP LOCKED)
          UPDATE sonar_workspace_deliveries d
          SET attempts=attempts+1,last_attempt_at=NOW()
          FROM pending p,sonar_workspace_events e
          WHERE d.event_id=p.event_id AND d.member_id=p.member_id AND e.id=d.event_id
          RETURNING d.event_id,d.member_id,e.actor_id,e.resource,e.action,e.detail''',(limit,))
        items=cur.fetchall()
        conn.commit()
        return items


def mark_delivered(db, event_id, member_id):
    db.execute('UPDATE sonar_workspace_deliveries SET sent_at=NOW() WHERE event_id=%s AND member_id=%s',
               (int(event_id),int(member_id)))


async def workspace_alert_job(context):
    import asyncio
    from database import Database
    db=Database()
    try:
        rows=await asyncio.to_thread(claim_events,db)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Shared workspace notification queue unavailable')
        return
    for item in rows:
        try:
            await context.bot.send_message(int(item['member_id']),
                f'🔔 فعالیت در فضای کاری مشترک\n\n'
                f'👤 انجام‌دهنده: {item["actor_id"]}\n'
                f'🛠 {item["action"]} {item["resource"]}\n'
                f'📋 {item["detail"]}\n\n'
                'این تغییر برای تمام هم‌گروهی‌ها اعمال شد.')
            await asyncio.to_thread(mark_delivered,db,item['event_id'],item['member_id'])
        except Exception:
            # Delivery stays pending for a bounded retry; e.g. a blocked bot cannot send DMs.
            pass
