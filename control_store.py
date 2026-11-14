"""Account sessions, consent-based team invitations and bounded terminal leases."""
from __future__ import annotations
import hashlib
import json
import secrets
from psycopg2.extras import Json
from billing import _audit
from user_errors import UserError

ROLES=('manager','technical','monitor','server','viewer','auditor')
SCHEMA='''
ALTER TABLE agent_nodes ADD COLUMN IF NOT EXISTS token_rotated_at TIMESTAMPTZ;
ALTER TABLE web_agent_install_jobs ADD COLUMN IF NOT EXISTS rotate_token BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS registered_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_active_at TIMESTAMPTZ;
ALTER TABLE sonar_web_sessions ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE sonar_web_sessions ADD COLUMN IF NOT EXISTS client_label VARCHAR(200) NOT NULL DEFAULT '';
ALTER TABLE sonar_web_sessions ADD COLUMN IF NOT EXISTS ip_hash CHAR(64);
ALTER TABLE sonar_web_sessions ADD COLUMN IF NOT EXISTS elevated_at TIMESTAMPTZ;
CREATE TABLE IF NOT EXISTS sonar_team_invites (
 id BIGSERIAL PRIMARY KEY,owner_id BIGINT NOT NULL,member_id BIGINT NOT NULL,
 role TEXT NOT NULL,expires_at TIMESTAMPTZ NOT NULL DEFAULT NOW()+INTERVAL '48 hours',
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','accepted','declined','revoked')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),CHECK(owner_id<>member_id));
CREATE UNIQUE INDEX IF NOT EXISTS sonar_team_pending ON sonar_team_invites(owner_id,member_id) WHERE status='pending';
ALTER TABLE pro_team DROP CONSTRAINT IF EXISTS pro_team_role_check;
ALTER TABLE pro_team ALTER COLUMN role TYPE VARCHAR(20);
ALTER TABLE pro_team ADD CONSTRAINT pro_team_role_check CHECK(role IN ('manager','technical','monitor','server','viewer','auditor'));
CREATE TABLE IF NOT EXISTS sonar_terminal_leases (
 id UUID PRIMARY KEY,user_id BIGINT NOT NULL,server_id BIGINT NOT NULL,
 started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 ended_at TIMESTAMPTZ,bytes_in BIGINT NOT NULL DEFAULT 0,bytes_out BIGINT NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS sonar_terminal_live ON sonar_terminal_leases(user_id,heartbeat_at) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS sonar_action_limits (
 actor_id BIGINT NOT NULL,action VARCHAR(40) NOT NULL,window_started TIMESTAMPTZ NOT NULL DEFAULT NOW(),
 attempts INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(actor_id,action));
ALTER TABLE servers ADD COLUMN IF NOT EXISTS telemetry JSONB NOT NULL DEFAULT '{}';
ALTER TABLE servers ADD COLUMN IF NOT EXISTS telemetry_at TIMESTAMPTZ;
ALTER TABLE servers ADD COLUMN IF NOT EXISTS next_observation_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE servers ADD COLUMN IF NOT EXISTS maintenance_until TIMESTAMPTZ;
ALTER TABLE servers ADD COLUMN IF NOT EXISTS maintenance_reason VARCHAR(500) NOT NULL DEFAULT '';
'''


def install_schema(db):
    with db.get_connection() as (conn,cur):cur.execute(SCHEMA);conn.commit()
    install_access_guard(db)
    install_typed_expiry(db)
    from regional_monitor import install_schema as install_regions
    install_regions(db)
    from notification_api import install_schema as install_notifications
    install_notifications(db)
    from hosting_agents import install_schema as install_hosting
    install_hosting(db)
    from billing import install_staff
    install_staff(db)
    from config_probe_jobs import install_schema as install_probes
    install_probes(db)
    from billing_delivery import install_schema as install_billing_messages
    install_billing_messages(db)


class Control:
    def __init__(self,pro):self.pro=pro;self.db=pro.db
    def sessions(self,uid,current):
        from web_login import digest
        rows=self.db.execute('''SELECT token_hash,created_at,last_seen_at,expires_at,client_label
            FROM sonar_web_sessions WHERE user_id=%s AND revoked_at IS NULL AND expires_at>NOW()
            AND last_seen_at>NOW()-INTERVAL '30 minutes' ORDER BY last_seen_at DESC LIMIT 100''',(uid,),fetch='all')
        return [{'id':str(r['token_hash']), 'current':r['token_hash']==digest(current) if current else False,
                 **{k:r[k] for k in ('created_at','last_seen_at','expires_at','client_label')}} for r in rows]
    def revoke(self,uid,ident):
        self.db.execute('UPDATE sonar_web_sessions SET revoked_at=NOW() WHERE user_id=%s AND token_hash=%s',(uid,ident))
        self.pro.audit(uid,uid,'session.revoked',ident[:12])
    def invite(self,uid,member,role):
        owner=self.pro.workspace(uid)
        if uid!=owner:raise PermissionError('فقط مالک می‌تواند دعوت ارسال کند')
        if role not in ROLES:raise UserError('نقش معتبر نیست')
        if int(member)==owner or not self.db.get_user(member):raise UserError('کاربر باید قبلاً ربات را شروع کرده باشد')
        with self.db.get_connection() as (conn,cur):
            cur.execute("UPDATE sonar_team_invites SET status='revoked' WHERE owner_id=%s AND member_id=%s AND status='pending' AND expires_at<=NOW()",(owner,member))
            cur.execute("SELECT 1 FROM sonar_team_invites WHERE owner_id=%s AND member_id=%s AND status='pending'",(owner,member))
            if cur.fetchone():raise UserError('دعوت فعال قبلی وجود دارد')
            cur.execute('INSERT INTO sonar_team_invites(owner_id,member_id,role) VALUES(%s,%s,%s) RETURNING id',(owner,member,role))
            ident=cur.fetchone()['id'];_audit(cur,uid,member,'team.invite',None,{'owner':owner,'role':role},'دعوت با تأیید گیرنده');conn.commit();return ident
    def invites(self,uid):
        return self.db.execute("SELECT * FROM sonar_team_invites WHERE (member_id=%s OR owner_id=%s) AND status='pending' AND expires_at>NOW() ORDER BY id DESC",(uid,uid),fetch='all')
    def decide(self,uid,ident,accept):
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT * FROM sonar_team_invites WHERE id=%s FOR UPDATE',(ident,));row=cur.fetchone()
            if not row or row['member_id']!=uid:raise PermissionError('دعوت متعلق به شما نیست')
            cur.execute("SELECT 1 WHERE %s>NOW()",(row['expires_at'],))
            if row['status']!='pending' or not cur.fetchone():raise UserError('دعوت معتبر نیست')
            if accept:
                cur.execute('INSERT INTO pro_team(owner_id,member_id,role) VALUES(%s,%s,%s) ON CONFLICT(owner_id,member_id) DO UPDATE SET role=EXCLUDED.role',(row['owner_id'],uid,row['role']))
            cur.execute('UPDATE sonar_team_invites SET status=%s WHERE id=%s',('accepted' if accept else 'declined',ident))
            _audit(cur,uid,uid,'team.decide',None,{'invite':ident,'accepted':accept},'پاسخ گیرنده دعوت');conn.commit()
    def role(self,uid,member,role):
        owner=self.pro.workspace(uid)
        if owner!=uid or uid==member:raise PermissionError('فقط مالک می‌تواند نقش اعضا را تغییر دهد')
        if role not in ROLES+('remove',):raise UserError('نقش معتبر نیست')
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT role FROM pro_team WHERE owner_id=%s AND member_id=%s FOR UPDATE',(owner,member));before=cur.fetchone()
            legacy=False
            if not before:
                cur.execute('SELECT 1 FROM sonar_workspaces WHERE owner_id=%s AND member_id=%s AND member_id<>owner_id',(owner,member))
                legacy=bool(cur.fetchone())
                if not legacy:raise UserError('ابتدا دعوت پذیرفته‌شده لازم است')
                before={'role':'manager'}
            if role=='remove':
                cur.execute('DELETE FROM pro_team WHERE owner_id=%s AND member_id=%s',(owner,member))
                cur.execute('DELETE FROM sonar_workspaces WHERE owner_id=%s AND member_id=%s AND member_id<>owner_id',(owner,member))
                cur.execute("UPDATE sonar_team_invites SET status='revoked' WHERE owner_id=%s AND member_id=%s AND status='pending'",(owner,member))
            elif legacy:cur.execute('INSERT INTO pro_team(owner_id,member_id,role) VALUES(%s,%s,%s)',(owner,member,role))
            else:cur.execute('UPDATE pro_team SET role=%s WHERE owner_id=%s AND member_id=%s',(role,owner,member))
            _audit(cur,uid,member,'team.role',dict(before),{'role':role},'مدیریت دسترسی تیم');conn.commit()
    def lease(self,uid,sid,ident):
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT pg_advisory_xact_lock(535321,hashtext(%s))',(str(uid),))
            cur.execute("SELECT COUNT(*) n FROM sonar_terminal_leases WHERE user_id=%s AND ended_at IS NULL AND heartbeat_at>NOW()-INTERVAL '90 seconds'",(uid,))
            if cur.fetchone()['n']>=3:raise UserError('حداکثر سه ترمینال هم‌زمان مجاز است')
            cur.execute('INSERT INTO sonar_terminal_leases(id,user_id,server_id) VALUES(%s,%s,%s)',(ident,uid,sid))
            _audit(cur,uid,uid,'terminal.open',None,{'server':sid,'session':ident},'اتصال ترمینال مجاز');conn.commit()
    def terminal_touch(self,ident,incoming,outgoing,close=False):
        with self.db.get_connection() as (conn,cur):
            cur.execute('''UPDATE sonar_terminal_leases SET heartbeat_at=NOW(),bytes_in=%s,bytes_out=%s,
                ended_at=CASE WHEN %s THEN NOW() ELSE ended_at END WHERE id=%s RETURNING user_id,server_id''',(incoming,outgoing,close,ident));row=cur.fetchone()
            if close and row:_audit(cur,row['user_id'],row['user_id'],'terminal.close',None,{'server':row['server_id'],'bytes_in':incoming,'bytes_out':outgoing},'پایان اتصال ترمینال')
            conn.commit()
    def rate_limit(self,uid,action,limit=5):
        with self.db.get_connection() as (conn,cur):
            cur.execute("DELETE FROM sonar_action_limits WHERE window_started<NOW()-INTERVAL '1 day'")
            cur.execute('''INSERT INTO sonar_action_limits(actor_id,action) VALUES(%s,%s)
                ON CONFLICT(actor_id,action) DO UPDATE SET
                attempts=CASE WHEN sonar_action_limits.window_started<NOW()-INTERVAL '1 minute' THEN 1 ELSE sonar_action_limits.attempts+1 END,
                window_started=CASE WHEN sonar_action_limits.window_started<NOW()-INTERVAL '1 minute' THEN NOW() ELSE sonar_action_limits.window_started END
                RETURNING attempts''',(uid,action));count=cur.fetchone()['attempts'];conn.commit()
            if count>limit:raise UserError('تعداد درخواست‌ها زیاد است؛ یک دقیقه بعد دوباره تلاش کنید')
    def maintenance(self,uid,sid,minutes,reason):
        owner=self.pro.workspace(uid);self.pro.require(uid,owner,write=True)
        self.db.execute("UPDATE servers SET maintenance_until=CASE WHEN %s=0 THEN NULL ELSE NOW()+(%s*INTERVAL '1 minute') END,maintenance_reason=%s WHERE id=%s AND owner_id=%s",(minutes,minutes,reason,sid,owner))
        self.pro.audit(owner,uid,'server.maintenance',str(sid),reason)


def install_access_guard(db):
    from settings import SUPER_ADMIN_ID
    with db.get_connection() as (conn,cur):
        cur.execute('CREATE TABLE IF NOT EXISTS sonar_installation (id INTEGER PRIMARY KEY CHECK(id=1),admin_id BIGINT NOT NULL)')
        cur.execute('INSERT INTO sonar_installation(id,admin_id) VALUES(1,%s) ON CONFLICT(id) DO UPDATE SET admin_id=EXCLUDED.admin_id',(SUPER_ADMIN_ID,))
        cur.execute('''CREATE OR REPLACE FUNCTION sonar_resource_access_guard() RETURNS TRIGGER AS $$
        DECLARE actor BIGINT; owner BIGINT; role_name TEXT;
        BEGIN
          actor:=COALESCE(NULLIF(current_setting('sonar.actor',TRUE),'')::BIGINT,0);
          IF actor=0 OR actor=(SELECT admin_id FROM sonar_installation WHERE id=1) THEN
             IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
          END IF;
          owner:=CASE WHEN TG_OP='INSERT' THEN NEW.owner_id ELSE OLD.owner_id END;
          IF actor<>owner THEN
            SELECT role INTO role_name FROM pro_team WHERE owner_id=owner AND member_id=actor;
            IF role_name IS NULL AND EXISTS(SELECT 1 FROM sonar_workspaces WHERE owner_id=owner AND member_id=actor) THEN role_name:='manager'; END IF;
            IF role_name IS NULL OR NOT (role_name IN ('manager','technical') OR
               (role_name='server' AND TG_TABLE_NAME IN ('servers','groups')) OR
               (role_name='monitor' AND TG_TABLE_NAME='pro_services')) THEN
              RAISE EXCEPTION 'WORKSPACE_FORBIDDEN' USING ERRCODE='P0001';
            END IF;
            IF TG_OP='UPDATE' THEN
              IF NEW.owner_id<>OLD.owner_id THEN RAISE EXCEPTION 'WORKSPACE_FORBIDDEN' USING ERRCODE='P0001'; END IF;
            END IF;
          END IF;
          IF EXISTS(SELECT 1 FROM users WHERE user_id=actor AND is_banned<>0) THEN
            RAISE EXCEPTION 'WORKSPACE_FORBIDDEN' USING ERRCODE='P0001'; END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END; $$ LANGUAGE plpgsql''')
        for table in ('servers','tunnel_configs','pro_services','channels','groups','settings'):
            cur.execute(f'DROP TRIGGER IF EXISTS sonar_resource_access ON {table}')
            cur.execute(f'CREATE TRIGGER sonar_resource_access BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION sonar_resource_access_guard()')
        conn.commit()


def install_typed_expiry(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''
        ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_expires_at TIMESTAMPTZ;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_unlimited BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_date_invalid BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE servers ADD COLUMN IF NOT EXISTS expiry_at TIMESTAMPTZ;
        ALTER TABLE servers ADD COLUMN IF NOT EXISTS expiry_date_invalid BOOLEAN NOT NULL DEFAULT FALSE;
        CREATE OR REPLACE FUNCTION sonar_expiry_timestamp(value TEXT) RETURNS TIMESTAMPTZ AS $$
        BEGIN
          IF value IS NULL OR btrim(value)='' THEN RETURN NULL; END IF;
          IF value !~ '^\\d{4}-\\d{2}-\\d{2}( \\d{2}:\\d{2}:\\d{2})?$' THEN RETURN NULL; END IF;
          RETURN value::timestamp AT TIME ZONE 'Asia/Tehran';
        EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN RETURN NULL;
        END; $$ LANGUAGE plpgsql;
        CREATE OR REPLACE FUNCTION sonar_user_expiry_sync() RETURNS TRIGGER AS $$
        BEGIN
          NEW.subscription_expires_at:=sonar_expiry_timestamp(NEW.expiry_date);
          NEW.subscription_unlimited:=(NEW.expiry_date IS NULL OR btrim(NEW.expiry_date)='');
          NEW.subscription_date_invalid:=(NOT NEW.subscription_unlimited AND NEW.subscription_expires_at IS NULL);
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS sonar_user_expiry ON users;
        CREATE TRIGGER sonar_user_expiry BEFORE INSERT OR UPDATE OF expiry_date ON users
           FOR EACH ROW EXECUTE FUNCTION sonar_user_expiry_sync();
        CREATE OR REPLACE FUNCTION sonar_server_expiry_sync() RETURNS TRIGGER AS $$
        BEGIN
          NEW.expiry_at:=sonar_expiry_timestamp(NEW.expiry_date);
          NEW.expiry_date_invalid:=NEW.expiry_date IS NOT NULL AND btrim(NEW.expiry_date)<>'' AND NEW.expiry_at IS NULL;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        DROP TRIGGER IF EXISTS sonar_server_expiry ON servers;
        CREATE TRIGGER sonar_server_expiry BEFORE INSERT OR UPDATE OF expiry_date ON servers
           FOR EACH ROW EXECUTE FUNCTION sonar_server_expiry_sync();
        UPDATE users SET registered_at=sonar_expiry_timestamp(added_date) WHERE registered_at IS NULL;
        ALTER TABLE users ALTER COLUMN registered_at SET DEFAULT NOW();
        UPDATE users SET expiry_date=expiry_date;
        UPDATE servers SET expiry_date=expiry_date;
        CREATE INDEX IF NOT EXISTS sonar_user_expiry_idx ON users(subscription_expires_at);
        CREATE INDEX IF NOT EXISTS sonar_server_expiry_idx ON servers(expiry_at);
        ''')
        conn.commit()
