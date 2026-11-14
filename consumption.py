"""Optional plan consumption limits shared by Bot, Web and workers.
Missing limits preserve existing technical guards; zero disables the operation.
Daily counters use Tehran calendar days. Attempts count once they begin.
"""
import uuid
from billing import _lock
from user_errors import UserError

LABELS={'config_tests_daily':'تست کانفیگ','api_daily':'درخواست API','reports_daily':'تولید گزارش','notifications_daily':'اعلان فنی','operations_daily':'عملیات اجرایی'}
KINDS=('config_tests_daily','config_tests_concurrent','api_daily','reports_daily','notifications_daily','operations_daily')


def validate(limits):
    if set(limits)-set(KINDS) or any(type(v) is not int or not 0<=v<=10**9 for v in limits.values()):
        raise UserError('سقف مصرف باید عدد صحیح نامنفی برای نوع مجاز باشد')
    if limits.get('config_tests_concurrent',0)>100:raise UserError('سقف تست هم‌زمان حداکثر ۱۰۰ است')
    return limits


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''ALTER TABLE sonar_plans ADD COLUMN IF NOT EXISTS consumption JSONB NOT NULL DEFAULT '{}';
          CREATE TABLE IF NOT EXISTS sonar_daily_usage (
            owner_id BIGINT NOT NULL,kind VARCHAR(40) NOT NULL,period DATE NOT NULL,used BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY(owner_id,kind,period));
          CREATE TABLE IF NOT EXISTS sonar_consumption_events (
            owner_id BIGINT NOT NULL,kind VARCHAR(40) NOT NULL,period DATE NOT NULL,event_key VARCHAR(200) NOT NULL,
            PRIMARY KEY(owner_id,kind,period,event_key));
          CREATE TABLE IF NOT EXISTS sonar_probe_slots (
            id UUID PRIMARY KEY,owner_id BIGINT NOT NULL,expires_at TIMESTAMPTZ NOT NULL);
          CREATE INDEX IF NOT EXISTS sonar_consumption_event_period ON sonar_consumption_events(period);
          CREATE INDEX IF NOT EXISTS sonar_daily_usage_period ON sonar_daily_usage(period);
          CREATE INDEX IF NOT EXISTS sonar_probe_slots_owner ON sonar_probe_slots(owner_id,expires_at);''')
        conn.commit()


def _limits(cur,owner):
    cur.execute('SELECT snapshot,status,expires_at FROM sonar_subscriptions WHERE user_id=%s',(owner,))
    sub=cur.fetchone()
    return sub['snapshot'].get('consumption',{}) if sub else {}


def _consume(cur,owner,kind,limit,key=None):
    cur.execute("SELECT (NOW() AT TIME ZONE 'Asia/Tehran')::date period")
    day=cur.fetchone()['period']
    if key:
        cur.execute('SELECT 1 FROM sonar_consumption_events WHERE owner_id=%s AND kind=%s AND period=%s AND event_key=%s',(owner,kind,day,key))
        if cur.fetchone():return
    cur.execute('SELECT used FROM sonar_daily_usage WHERE owner_id=%s AND kind=%s AND period=%s FOR UPDATE',(owner,kind,day));row=cur.fetchone()
    used=row['used'] if row else 0
    if used>=limit:raise UserError('سقف روزانه '+LABELS.get(kind,kind)+' تکمیل شده است')
    cur.execute('''INSERT INTO sonar_daily_usage(owner_id,kind,period,used) VALUES(%s,%s,%s,1)
      ON CONFLICT(owner_id,kind,period) DO UPDATE SET used=sonar_daily_usage.used+1''',(owner,kind,day))
    if key:cur.execute('INSERT INTO sonar_consumption_events(owner_id,kind,period,event_key) VALUES(%s,%s,%s,%s)',(owner,kind,day,key[:200]))


def consume(db,owner,kind,key=None):
    if kind not in KINDS or kind=='config_tests_concurrent':raise ValueError('Unknown daily consumption kind')
    if key and len(key)>200:
        import hashlib
        key='sha256:'+hashlib.sha256(key.encode()).hexdigest()
    with db.get_connection() as (conn,cur):
        _lock(cur,owner);limits=_limits(cur,owner)
        if kind in limits:_consume(cur,owner,kind,limits[kind],key)
        conn.commit()


def start_probe(db,owner):
    with db.get_connection() as (conn,cur):
        _lock(cur,owner);limits=_limits(cur,owner)
        ident=None
        if 'config_tests_concurrent' in limits:
            cur.execute('DELETE FROM sonar_probe_slots WHERE owner_id=%s AND expires_at<=NOW()',(owner,))
            cur.execute('SELECT COUNT(*) n FROM sonar_probe_slots WHERE owner_id=%s',(owner,))
            if cur.fetchone()['n']>=limits['config_tests_concurrent']:raise UserError('سقف تست هم‌زمان پلن تکمیل شده است؛ بعداً تلاش کنید')
            ident=str(uuid.uuid4())
            cur.execute("INSERT INTO sonar_probe_slots(id,owner_id,expires_at) VALUES(%s,%s,NOW()+INTERVAL '5 minutes')",(ident,owner))
        if 'config_tests_daily' in limits:_consume(cur,owner,'config_tests_daily',limits['config_tests_daily'])
        conn.commit();return ident


def finish_probe(db,ident):
    if ident:db.execute('DELETE FROM sonar_probe_slots WHERE id=%s',(ident,))


def usage(db,owner):
    rows=db.execute("SELECT kind,used FROM sonar_daily_usage WHERE owner_id=%s AND period=(NOW() AT TIME ZONE 'Asia/Tehran')::date",(owner,),'all')
    active=db.execute("SELECT 'config_tests_concurrent' kind,COUNT(*) used FROM sonar_probe_slots WHERE owner_id=%s AND expires_at>NOW() HAVING COUNT(*)>0",(owner,),'all')
    return [*rows,*active]
