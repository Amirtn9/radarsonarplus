"""Role-scoped product workflows over the existing billing ledger and workspaces."""
from datetime import datetime, timedelta, timezone
import csv
import io
from billing import Billing, _audit, _lock
from user_errors import UserError


def install_schema(db):
    with db.get_connection() as (conn, cur):
        cur.execute('''ALTER TABLE sonar_plans ADD COLUMN IF NOT EXISTS publish_at TIMESTAMPTZ;
          ALTER TABLE sonar_plans ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
          ALTER TABLE sonar_plans ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
          CREATE INDEX IF NOT EXISTS sonar_orders_reporting ON sonar_orders(created_at,plan_id,user_id);
          CREATE TABLE IF NOT EXISTS sonar_message_batches (
            actor_id BIGINT NOT NULL,request_key VARCHAR(100) NOT NULL,user_ids BIGINT[] NOT NULL,
            body_hash CHAR(64) NOT NULL,message_ids BIGINT[] NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),PRIMARY KEY(actor_id,request_key));''')
        conn.commit()
    from consumption import install_schema as install_consumption
    install_consumption(db)


def window(start=None,end=None):
    end=end or datetime.now(timezone.utc)
    start=start or end-timedelta(days=30)
    if not start.tzinfo or not end.tzinfo or start>=end or end-start>timedelta(days=366):
        raise UserError('بازه دارای منطقه زمانی، حداکثر ۳۶۶ روز و پایان پس از شروع لازم است')
    return start,end


class Product:
    def __init__(self,db,admin_id=None):
        self.db=db
        self.billing=Billing(db,admin_id)

    def orders(self,actor,*,status=None,start=None,end=None,plan_id=None,user_id=None,method=None,q='',page=1):
        self.billing.admin(actor,'payments')
        start,end=window(start,end)
        return self.db.execute('''SELECT o.*,COUNT(*) OVER() total_count FROM sonar_orders o
          WHERE created_at>=%s AND created_at<%s AND (%s::text IS NULL OR status=%s)
          AND (%s::bigint IS NULL OR plan_id=%s) AND (%s::bigint IS NULL OR user_id=%s)
          AND (%s::text IS NULL OR method=%s) AND (id::text ILIKE %s OR user_id::text ILIKE %s)
          ORDER BY created_at DESC,id LIMIT 50 OFFSET %s''',
          (start,end,status,status,plan_id,plan_id,user_id,user_id,method,method,'%'+q[:100]+'%','%'+q[:100]+'%',(page-1)*50),'all')

    def finances(self,actor,start=None,end=None,plan_id=None,user_id=None,method=None):
        self.billing.admin(actor,'reports')
        start,end=window(start,end)
        params=(start,end,plan_id,plan_id,user_id,user_id,method,method)
        where='''created_at>=%s AND created_at<%s AND (%s::bigint IS NULL OR plan_id=%s)
          AND (%s::bigint IS NULL OR user_id=%s) AND (%s::text IS NULL OR method=%s)'''
        # A single SQL snapshot keeps totals, chart and campaigns consistent, even
        # when Database.get_connection has already set transaction-local actor data.
        query="""WITH filtered AS MATERIALIZED (
          SELECT sonar_orders.*,EXISTS(SELECT 1 FROM sonar_orders old
            WHERE old.user_id=sonar_orders.user_id AND old.status IN ('paid','refunded')
            AND (old.created_at,old.id)<(sonar_orders.created_at,sonar_orders.id)) renewal
          FROM sonar_orders WHERE """+where+"""), daily AS (
          SELECT date_trunc('day',created_at AT TIME ZONE 'Asia/Tehran') bucket_day,
            plan_id,method,status,COUNT(*) orders,SUM(amount) amount,
            COUNT(*) FILTER(WHERE renewal) renewals FROM filtered
            GROUP BY bucket_day,plan_id,method,status), campaigns AS (
          SELECT discount_code,COUNT(*) orders,SUM(amount) sales FROM filtered
            WHERE status='paid' AND discount_code IS NOT NULL GROUP BY discount_code)
          SELECT (SELECT to_jsonb(s) FROM (SELECT COUNT(*) orders,
            COUNT(*) FILTER(WHERE status='paid') paid_orders,
            COUNT(*) FILTER(WHERE status='pending') pending_orders,
            COUNT(*) FILTER(WHERE status='rejected') rejected_orders,
            COALESCE(SUM(amount) FILTER(WHERE status='paid'),0) net_sales,
            COALESCE(SUM(amount) FILTER(WHERE status IN ('paid','refunded')),0) gross_sales,
            COALESCE(SUM(amount) FILTER(WHERE status='refunded'),0) wallet_refunds,
            COUNT(*) FILTER(WHERE status='paid' AND discount_code IS NOT NULL) discounted_orders,
            COALESCE(SUM(amount) FILTER(WHERE status='paid' AND discount_code IS NOT NULL),0) campaign_sales
            FROM filtered) s) summary,
          COALESCE((SELECT jsonb_agg(to_jsonb(d) ORDER BY bucket_day DESC,plan_id,method,status) FROM daily d),'[]'::jsonb) rows,
          COALESCE((SELECT jsonb_agg(to_jsonb(c) ORDER BY sales DESC) FROM campaigns c),'[]'::jsonb) campaigns"""
        result=self.db.execute(query,params,'one')
        return dict(start=start,end=end,currency='IRR',**result)

    def overview(self,actor):
        caps=self.billing.permissions(actor)
        if not caps:raise PermissionError('Management permission required')
        result={'measured_at':datetime.now(timezone.utc),'permissions':caps}
        if 'users.read' in caps:
            result['users']=self.db.execute('''SELECT COUNT(*) total,
              COUNT(*) FILTER(WHERE is_banned<>0) banned,
              COUNT(*) FILTER(WHERE registered_at>=NOW()-INTERVAL '30 days') new_month FROM users''',fetch='one')
            result['subscriptions']=self.db.execute('''SELECT
              COUNT(*) FILTER(WHERE status='active' AND (expires_at IS NULL OR expires_at>NOW())) active,
              COUNT(*) FILTER(WHERE expires_at<=NOW()) expired,
              COUNT(*) FILTER(WHERE status='active' AND expires_at>NOW() AND expires_at<NOW()+INTERVAL '7 days') expiring
              FROM sonar_subscriptions''',fetch='one')
        if 'reports' in caps:
            from zoneinfo import ZoneInfo
            now=datetime.now(ZoneInfo('Asia/Tehran'))
            result['today']=self.finances(actor,now.replace(hour=0,minute=0,second=0,microsecond=0),now)['summary']
            result['month']=self.finances(actor,now.replace(day=1,hour=0,minute=0,second=0,microsecond=0),now)['summary']
        if 'payments' in caps:
            result['payments']=self.db.execute("SELECT COUNT(*) pending,COUNT(*) FILTER(WHERE evidence<>'') with_receipt FROM sonar_orders WHERE status='pending'",fetch='one')
        if actor==self.billing.admin_id:
            result['servers']=self.db.execute("SELECT COUNT(*) total,COUNT(*) FILTER(WHERE last_status='Online' AND last_checked_at>NOW()-INTERVAL '5 minutes') last_online,COUNT(*) FILTER(WHERE last_checked_at IS NULL OR last_checked_at<=NOW()-INTERVAL '5 minutes') stale,MAX(last_checked_at) last_sample FROM servers",fetch='one')
        return result

    def users(self,actor,q='',filter='all',page=1):
        self.billing.admin(actor,'users.read')
        filters={
          'all':'TRUE','expired':"s.expires_at<=NOW()",'expiring':"s.status='active' AND s.expires_at>NOW() AND s.expires_at<NOW()+INTERVAL '7 days'",
          'zero_wallet':'COALESCE(u.wallet_balance,0)=0','pending':"EXISTS(SELECT 1 FROM sonar_orders o WHERE o.user_id=u.user_id AND o.status='pending')",
          'inactive':"u.last_active_at IS NULL OR u.last_active_at<NOW()-INTERVAL '30 days'",'banned':'u.is_banned<>0'}
        if filter not in filters:raise UserError('فیلتر کاربر معتبر نیست')
        term='%'+q[:80]+'%'
        return self.db.execute('''SELECT u.user_id,u.full_name,u.registered_at,u.last_active_at,u.is_banned,u.wallet_balance,
          s.status,s.expires_at,s.snapshot->>'name' plan,COUNT(*) OVER() total_count
          FROM users u LEFT JOIN sonar_subscriptions s ON s.user_id=u.user_id
          WHERE (u.full_name ILIKE %s OR u.user_id::text ILIKE %s) AND ('''+filters[filter]+''')
          ORDER BY u.user_id LIMIT 50 OFFSET %s''',(term,term,(page-1)*50),'all')

    def dossier(self,actor,uid,page=1):
        self.billing.admin(actor,'users.read')
        account=self.billing.account(uid)
        offset=(page-1)*50
        resources={}
        for key,query in {
          'servers':'SELECT id,name,ip,last_status,last_checked_at FROM servers WHERE owner_id=%s ORDER BY id LIMIT 50 OFFSET %s',
          'configs':'SELECT id,name,type,added_at FROM tunnel_configs WHERE owner_id=%s ORDER BY id LIMIT 50 OFFSET %s',
          'agents':"SELECT id,name,'hosting' kind,last_seen_at FROM sonar_hosting_agents WHERE owner_id=%s ORDER BY id LIMIT 50 OFFSET %s",
          'team':"SELECT member_id id,role name FROM pro_team WHERE owner_id=%s ORDER BY member_id LIMIT 50 OFFSET %s",
          'destinations':"SELECT id,name FROM channels WHERE owner_id=%s ORDER BY id LIMIT 50 OFFSET %s",
          'websites':'SELECT id,name,kind,target,enabled FROM pro_services WHERE owner_id=%s ORDER BY id LIMIT 50 OFFSET %s',
        }.items():resources[key]=self.db.execute(query,(uid,offset),'all')
        resources['vps_agents']=self.db.execute('SELECT id,name,agent_id,last_agent_status,last_checked_at FROM servers WHERE owner_id=%s AND agent_id IS NOT NULL ORDER BY id LIMIT 50 OFFSET %s',(uid,offset),'all')
        account['resources']=resources
        account['orders']=self.db.execute('SELECT id,plan_id,amount,method,status,created_at FROM sonar_orders WHERE user_id=%s ORDER BY created_at DESC,id LIMIT 50 OFFSET %s',(uid,offset),'all')
        account['wallet']=self.db.execute('SELECT id,amount,balance,reason,created_at FROM sonar_wallet_ledger WHERE user_id=%s ORDER BY id DESC LIMIT 50 OFFSET %s',(uid,offset),'all')
        account['sessions']=self.db.execute('''SELECT created_at,last_seen_at,expires_at,revoked_at,client_label
          FROM sonar_web_sessions WHERE user_id=%s ORDER BY created_at DESC LIMIT 50 OFFSET %s''',(uid,offset),'all')
        caps=self.billing.permissions(actor)
        if 'audit' in caps:
            account['activity']=self.db.execute('SELECT * FROM sonar_billing_audit WHERE user_id=%s ORDER BY id DESC LIMIT 50 OFFSET %s',(uid,offset),'all')
        if 'messages' in caps:
            account['messages']=self.db.execute('''SELECT id,state,attempts,error_code,created_at,sent_at
              FROM sonar_billing_messages WHERE user_id=%s ORDER BY id DESC LIMIT 50 OFFSET %s''',(uid,offset),'all')
        account['page']=page
        return account

    def plan_usage(self,actor,ident):
        self.billing.admin(actor,'plans')
        return self.db.execute('''SELECT s.user_id,s.status,s.expires_at,
          s.snapshot->'limits' limits,s.overrides,
          (SELECT COUNT(*) FROM servers WHERE owner_id=s.user_id) servers,
          (SELECT COUNT(*) FROM tunnel_configs WHERE owner_id=s.user_id) configs,
          (SELECT COUNT(*) FROM pro_services WHERE owner_id=s.user_id) websites,
          ((SELECT COUNT(*) FROM servers WHERE owner_id=s.user_id AND agent_id IS NOT NULL)+(SELECT COUNT(*) FROM sonar_hosting_agents WHERE owner_id=s.user_id AND revoked_at IS NULL)) agents,
          (SELECT COUNT(*) FROM pro_team WHERE owner_id=s.user_id) team,
          (SELECT COUNT(*) FROM channels WHERE owner_id=s.user_id) destinations,
          COUNT(*) OVER() total_count FROM sonar_subscriptions s WHERE plan_id=%s ORDER BY user_id LIMIT 100''',(ident,),'all')

    def archive(self,actor,ident,reason):
        self.billing.admin(actor,'plans')
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT * FROM sonar_plans WHERE id=%s FOR UPDATE',(ident,));before=cur.fetchone()
            if not before:raise UserError('پلن یافت نشد')
            cur.execute('UPDATE sonar_plans SET enabled=FALSE,archived_at=NOW(),updated_at=NOW() WHERE id=%s',(ident,))
            from billing import _safe
            _audit(cur,actor,None,'plan.archive',_safe(before),{'id':ident,'enabled':False},reason)
            conn.commit()

    def bulk_message(self,actor,uids,text,key,reason):
        import hashlib
        from billing_delivery import enqueue
        from control_store import Control
        from pro_store import ProStore
        self.billing.admin(actor,'messages')
        ids=sorted(set(uids))
        if not ids or len(ids)>200:raise UserError('بین ۱ تا ۲۰۰ گیرنده انتخاب کنید')
        digest=hashlib.sha256(text.encode()).hexdigest()
        Control(ProStore(self.db)).rate_limit(actor,'billing_bulk_message',5)
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT pg_advisory_xact_lock(535330,hashtext(%s))',(str(actor)+':'+key,))
            cur.execute('SELECT * FROM sonar_message_batches WHERE actor_id=%s AND request_key=%s',(actor,key));old=cur.fetchone()
            if old:
                if old['user_ids']!=ids or old['body_hash']!=digest:raise UserError('شناسه دسته با اطلاعات متفاوت تکرار شده است')
                conn.commit();return old['message_ids']
            cur.execute('SELECT user_id FROM users WHERE user_id=ANY(%s) AND is_banned=0 ORDER BY user_id', (ids,))
            if [r['user_id'] for r in cur.fetchall()]!=ids:raise UserError('تمام گیرندگان باید حساب موجود و غیرمسدود داشته باشند')
            message_ids=[]
            for uid in ids:
                _lock(cur,uid)
                message_ids.append(enqueue(cur,uid,actor,f'bulk:{actor}:{key}:{uid}',text))
            cur.execute('INSERT INTO sonar_message_batches(actor_id,request_key,user_ids,body_hash,message_ids) VALUES(%s,%s,%s,%s,%s)',(actor,key,ids,digest,message_ids))
            _audit(cur,actor,None,'message.batch',None,{'user_ids':ids,'message_ids':message_ids},reason)
            conn.commit();return message_ids


def finance_csv(report):
    buf=io.StringIO(newline='');writer=csv.writer(buf)
    writer.writerow(('day_tehran','plan_id','method','status','orders','amount_IRR','renewals'))
    for row in report['rows']:
        writer.writerow([row[k] for k in ('bucket_day','plan_id','method','status','orders','amount','renewals')])
    return '\ufeff'+buf.getvalue()
