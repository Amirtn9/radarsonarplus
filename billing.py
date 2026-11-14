"""Shared monitoring subscriptions, immutable purchase snapshots and atomic money.
Amounts are integer IRR. Crypto/card destinations collect manually reviewed evidence;
no browser redirect, receipt or transaction hash is proof of settled payment.
"""
from __future__ import annotations
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from psycopg2.extras import Json
from user_errors import UserError

RESOURCES = ('servers','configs','websites','agents','team','destinations')
DEFAULT_LIMITS = dict(zip(RESOURCES,(2,5,1,1,1,1)))
SCHEMA = '''
CREATE TABLE IF NOT EXISTS sonar_plans (
 id BIGSERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL, description TEXT NOT NULL DEFAULT '',
 price BIGINT NOT NULL CHECK(price>=0), days INTEGER NOT NULL CHECK(days BETWEEN 1 AND 3660),
 limits JSONB NOT NULL, policy JSONB NOT NULL DEFAULT '{"min_interval_seconds":60,"retention_days":30}', features JSONB NOT NULL DEFAULT '{}', enabled BOOLEAN NOT NULL DEFAULT TRUE,
 priority INTEGER NOT NULL DEFAULT 0, change_policy TEXT NOT NULL DEFAULT 'new' CHECK(change_policy IN ('new','current','renewal')), created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS sonar_subscriptions (
 user_id BIGINT PRIMARY KEY REFERENCES users(user_id), plan_id BIGINT REFERENCES sonar_plans(id),
 snapshot JSONB NOT NULL, overrides JSONB NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','suspended','cancelled')),
 starts_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), expires_at TIMESTAMPTZ,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE TABLE IF NOT EXISTS sonar_billing_audit (
 id BIGSERIAL PRIMARY KEY, actor_id BIGINT NOT NULL, user_id BIGINT, event TEXT NOT NULL,
 before_data JSONB, after_data JSONB, reason TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE INDEX IF NOT EXISTS sonar_billing_audit_user ON sonar_billing_audit(user_id,id DESC);
CREATE TABLE IF NOT EXISTS sonar_payment_destinations (
 id BIGSERIAL PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('card','crypto')),
 name VARCHAR(100) NOT NULL, network VARCHAR(80) NOT NULL DEFAULT '',
 address VARCHAR(250) NOT NULL, holder VARCHAR(100) NOT NULL DEFAULT '',
 instructions VARCHAR(1000) NOT NULL DEFAULT '', enabled BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE IF NOT EXISTS sonar_discounts (
 code VARCHAR(40) PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('percent','fixed')),
 value BIGINT NOT NULL CHECK(value>0), starts_at TIMESTAMPTZ NOT NULL,
 ends_at TIMESTAMPTZ NOT NULL, max_uses INTEGER NOT NULL CHECK(max_uses>0),
 uses INTEGER NOT NULL DEFAULT 0 CHECK(uses>=0), reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved>=0), user_id BIGINT, plan_id BIGINT REFERENCES sonar_plans(id),
 first_only BOOLEAN NOT NULL DEFAULT FALSE, renewal_only BOOLEAN NOT NULL DEFAULT FALSE, enabled BOOLEAN NOT NULL DEFAULT TRUE);
CREATE TABLE IF NOT EXISTS sonar_orders (
 id UUID PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(user_id),
 plan_id BIGINT NOT NULL REFERENCES sonar_plans(id), snapshot JSONB NOT NULL,
 amount BIGINT NOT NULL CHECK(amount>=0), discount_code VARCHAR(40),
 method TEXT NOT NULL CHECK(method IN ('wallet','manual','grant')),
 destination_id BIGINT REFERENCES sonar_payment_destinations(id), destination_snapshot JSONB,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','paid','rejected','refunded')),
 evidence VARCHAR(1000) NOT NULL DEFAULT '', idempotency_key VARCHAR(100) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), decided_at TIMESTAMPTZ,
 UNIQUE(user_id,idempotency_key));
CREATE INDEX IF NOT EXISTS sonar_orders_status ON sonar_orders(status,created_at);
CREATE TABLE IF NOT EXISTS sonar_wallet_ledger (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(user_id),
 actor_id BIGINT NOT NULL, amount BIGINT NOT NULL CHECK(amount<>0), balance BIGINT NOT NULL CHECK(balance>=0),
 idempotency_key VARCHAR(150) NOT NULL UNIQUE, reason VARCHAR(1000) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
CREATE OR REPLACE FUNCTION sonar_immutable_record() RETURNS TRIGGER AS $$
BEGIN RAISE EXCEPTION 'Append-only financial and audit records cannot be changed'; END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS sonar_wallet_immutable ON sonar_wallet_ledger;
CREATE TRIGGER sonar_wallet_immutable BEFORE UPDATE OR DELETE ON sonar_wallet_ledger
 FOR EACH ROW EXECUTE FUNCTION sonar_immutable_record();
DROP TRIGGER IF EXISTS sonar_audit_immutable ON sonar_billing_audit;
CREATE TRIGGER sonar_audit_immutable BEFORE UPDATE OR DELETE ON sonar_billing_audit
 FOR EACH ROW EXECUTE FUNCTION sonar_immutable_record();
'''


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute(SCHEMA)
        cur.execute('SELECT 1 FROM sonar_plans LIMIT 1')
        if not cur.fetchone():
            cur.execute('''INSERT INTO sonar_plans(name,description,price,days,limits)
                VALUES(%s,%s,0,7,%s)''',('آزمایشی','پایش زیرساخت؛ بدون فروش کانفیگ',Json(DEFAULT_LIMITS)))
        conn.commit()


def limits_valid(limits):
    if set(limits)-set(RESOURCES):raise UserError('نام سهمیه معتبر نیست')
    if any(type(v) is not int or not 0<=v<=1000000 for v in limits.values()):
        raise UserError('سهمیه باید عدد صحیح بین صفر و یک میلیون باشد')
    return limits


def _audit(cur,actor,uid,event,before,after,reason):
    if not isinstance(reason,str) or not reason.strip():raise UserError('دلیل عملیات الزامی است')
    cur.execute('''INSERT INTO sonar_billing_audit(actor_id,user_id,event,before_data,after_data,reason)
        VALUES(%s,%s,%s,%s,%s,%s)''',(actor,uid,event,Json(before) if before is not None else None,
                                    Json(after) if after is not None else None,reason[:1000]))


def _lock(cur,uid):
    # Same lock serializes purchases, wallet adjustments and quota allocations.
    cur.execute('SELECT pg_advisory_xact_lock(535320,hashtext(%s))',(str(int(uid)),))


class Billing:
    def __init__(self,db,admin_id=None):
        self.db=db
        if admin_id is None:
            from settings import SUPER_ADMIN_ID
            admin_id=SUPER_ADMIN_ID
        self.admin_id=int(admin_id or 0)

    def permissions(self,actor):
        if self.admin_id and int(actor)==self.admin_id:return list(CAPABILITIES)
        row=self.db.execute('SELECT capabilities FROM sonar_billing_staff WHERE user_id=%s AND enabled=TRUE',(actor,),fetch='one')
        return list(row['capabilities']) if row else []

    def admin(self,actor,capability=None):
        if self.admin_id and int(actor)==self.admin_id:return
        permissions=self.permissions(actor)
        if not permissions or capability and capability not in permissions:raise PermissionError('مجوز این عملیات مدیریتی را ندارید')

    def staff(self,actor,uid,label,capabilities,enabled,reason):
        if not self.admin_id or int(actor)!=self.admin_id:raise PermissionError('فقط مدیر اصلی می‌تواند مجوز مدیریتی بدهد')
        if set(capabilities)-set(CAPABILITIES):raise UserError('مجوز مدیریتی معتبر نیست')
        if int(uid)==self.admin_id:raise UserError('مجوز مدیر اصلی قابل تغییر نیست')
        if not self.db.get_user(uid):raise UserError('کاربر باید ابتدا ثبت‌نام کند')
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT * FROM sonar_billing_staff WHERE user_id=%s FOR UPDATE',(uid,));before=cur.fetchone()
            cur.execute('''INSERT INTO sonar_billing_staff(user_id,label,capabilities,enabled) VALUES(%s,%s,%s,%s)
                ON CONFLICT(user_id) DO UPDATE SET label=EXCLUDED.label,capabilities=EXCLUDED.capabilities,
                enabled=EXCLUDED.enabled,updated_at=NOW()''',(uid,label,capabilities,enabled))
            _audit(cur,actor,uid,'staff.permissions',_safe(before),{'label':label,'capabilities':capabilities,'enabled':enabled},reason);conn.commit()

    def enroll(self,uid):
        """Only new accounts opt into the commercial plan; never rewrite legacy data."""
        with self.db.get_connection() as (conn,cur):
            _lock(cur,uid)
            cur.execute('SELECT * FROM sonar_plans WHERE enabled=TRUE AND price=0 ORDER BY priority,id LIMIT 1')
            plan=cur.fetchone()
            if plan:
                cur.execute('''INSERT INTO sonar_subscriptions(user_id,plan_id,snapshot,expires_at)
                   VALUES(%s,%s,%s,NOW()+(%s*INTERVAL '1 day')) ON CONFLICT(user_id) DO NOTHING''',
                   (uid,plan['id'],Json(dict(plan),dumps=_json),plan['days']))
                cur.execute("UPDATE users SET expiry_date=to_char((SELECT expires_at FROM sonar_subscriptions WHERE user_id=%s) AT TIME ZONE 'Asia/Tehran','YYYY-MM-DD HH24:MI:SS'),server_limit=%s WHERE user_id=%s",(uid,plan['limits']['servers'],uid))
            conn.commit()

    def plans(self,actor=None,all_plans=False):
        if all_plans:self.admin(actor)
        rows=[dict(r) for r in self.db.execute('SELECT * FROM sonar_plans '+('' if all_plans else 'WHERE enabled=TRUE ')+'ORDER BY priority,id',fetch='all')]
        subscription=self.subscription(actor) if actor and not all_plans else None
        if subscription:
            for row in rows:
                if row['id']==subscription['plan_id'] and row['change_policy']=='new':
                    for key in ('name','description','price','days','limits','features','policy'):row[key]=subscription['snapshot'][key]
                    row['legacy_terms']=True
        return rows

    def save_plan(self,actor,data,ident=None,apply='new',reason=''):
        self.admin(actor,'plans')
        data.setdefault('policy',{'min_interval_seconds':60,'retention_days':30})
        if set(data['policy'])!={'min_interval_seconds','retention_days'} or type(data['policy']['min_interval_seconds']) is not int or not 60<=data['policy']['min_interval_seconds']<=3600 or type(data['policy']['retention_days']) is not int or not 1<=data['policy']['retention_days']<=30:raise UserError('حداقل فاصله پایش ۶۰ تا ۳۶۰۰ ثانیه و نگهداری تاریخچه ۱ تا ۳۰ روز است')
        limits_valid(data['limits'])
        if set(data['features'])-{'advanced_reports','api','automation','backups','agents'}:raise UserError('قابلیت پلن معتبر نیست')
        if set(data['limits'])!=set(RESOURCES):raise UserError('تمام سهمیه‌ها را مستقل تعیین کنید')
        if apply not in ('new','current','renewal'):raise UserError('روش اعمال تغییرات معتبر نیست')
        with self.db.get_connection() as (conn,cur):
            before=None
            if ident:
                cur.execute('SELECT * FROM sonar_plans WHERE id=%s FOR UPDATE',(ident,));before=cur.fetchone()
                if not before:raise UserError('پلن یافت نشد')
                cur.execute('''UPDATE sonar_plans SET name=%s,description=%s,price=%s,days=%s,limits=%s,
                    features=%s,enabled=%s,priority=%s,change_policy=%s,policy=%s WHERE id=%s''',
                    (data['name'],data['description'],data['price'],data['days'],Json(data['limits']),
                     Json(data['features']),data['enabled'],data['priority'],apply,Json(data['policy']),ident))
            else:
                cur.execute('''INSERT INTO sonar_plans(name,description,price,days,limits,features,enabled,priority,change_policy,policy)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                    (data['name'],data['description'],data['price'],data['days'],Json(data['limits']),
                     Json(data['features']),data['enabled'],data['priority'],apply,Json(data['policy'])))
                ident=cur.fetchone()['id']
            if apply=='current':
                # Existing purchased price/duration stay immutable; only capacities/features change.
                cur.execute('''UPDATE sonar_subscriptions SET snapshot=jsonb_set(jsonb_set(jsonb_set(snapshot,
                    '{limits}',%s::jsonb),'{features}',%s::jsonb),'{policy}',%s::jsonb),updated_at=NOW() WHERE plan_id=%s''',
                    (Json(data['limits']),Json(data['features']),Json(data['policy']),ident))
            _audit(cur,actor,None,'plan.save',_safe(before),{'id':ident,**data,'apply':apply},reason)
            conn.commit();return ident

    def delete_plan(self,actor,ident,reason):
        self.admin(actor,'plans')
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT 1 FROM sonar_subscriptions WHERE plan_id=%s UNION ALL SELECT 1 FROM sonar_orders WHERE plan_id=%s LIMIT 1',(ident,ident))
            if cur.fetchone():raise UserError('پلن دارای سابقه یا اشتراک است؛ آن را غیرفعال کنید')
            cur.execute('DELETE FROM sonar_plans WHERE id=%s RETURNING name',(ident,));row=cur.fetchone()
            if not row:raise UserError('پلن یافت نشد')
            _audit(cur,actor,None,'plan.delete',dict(row),{'id':ident},reason);conn.commit()

    def subscription(self,uid):
        row=self.db.execute('SELECT * FROM sonar_subscriptions WHERE user_id=%s',(uid,),fetch='one')
        if not row:return None
        result=dict(row);result['limits']={**row['snapshot']['limits'],**row['overrides']}
        result['effective_status']=('expired' if row['status']=='active' and row['expires_at'] and row['expires_at']<=datetime.now(timezone.utc) else row['status'])
        return result

    def can_monitor(self,uid):
        row=self.subscription(uid)
        return not row or (row['effective_status']=='active')

    def usage(self,uid):
        return self.db.execute('''SELECT
          (SELECT COUNT(*) FROM servers WHERE owner_id=%s) servers,
          (SELECT COUNT(*) FROM tunnel_configs WHERE owner_id=%s AND type<>'sub_source') configs,
          (SELECT COUNT(*) FROM pro_services WHERE owner_id=%s) websites,
          ((SELECT COUNT(*) FROM servers WHERE owner_id=%s AND agent_id IS NOT NULL)+(SELECT COUNT(*) FROM sonar_hosting_agents WHERE owner_id=%s AND revoked_at IS NULL)) agents,
          (SELECT COUNT(*) FROM pro_team WHERE owner_id=%s) team,
          (SELECT COUNT(*) FROM channels WHERE owner_id=%s) destinations''',tuple([uid]*7),fetch='one')

    def account(self,uid):
        user=self.db.get_user(uid)
        if not user:raise UserError('کاربر یافت نشد')
        return {'user':{k:user.get(k) for k in ('user_id','full_name','added_date','registered_at','last_active_at','is_banned','wallet_balance')},
                'subscription':self.subscription(uid),'usage':self.usage(uid),
                'orders':self.db.execute('SELECT id,plan_id,amount,method,status,created_at FROM sonar_orders WHERE user_id=%s ORDER BY created_at DESC LIMIT 100',(uid,),fetch='all'),
                'wallet':self.db.execute('SELECT id,amount,balance,reason,created_at FROM sonar_wallet_ledger WHERE user_id=%s ORDER BY id DESC LIMIT 100',(uid,),fetch='all')}

    def _wallet(self,cur,uid,actor,amount,key,reason):
        _lock(cur,uid)
        cur.execute('SELECT * FROM sonar_wallet_ledger WHERE idempotency_key=%s',(key,));old=cur.fetchone()
        if old:
            if int(old['user_id'])!=int(uid) or old['amount']!=amount:raise UserError('شناسه تکراری با عملیات متفاوت')
            return old['balance']
        cur.execute('SELECT wallet_balance FROM users WHERE user_id=%s FOR UPDATE',(uid,));row=cur.fetchone()
        if not row:raise UserError('کاربر یافت نشد')
        before=int(row['wallet_balance'] or 0);balance=before+amount
        if balance<0:raise UserError('موجودی کیف پول کافی نیست')
        if not amount or abs(amount)>10**15:raise UserError('مبلغ معتبر نیست')
        cur.execute('UPDATE users SET wallet_balance=%s WHERE user_id=%s',(balance,uid))
        cur.execute('''INSERT INTO sonar_wallet_ledger(user_id,actor_id,amount,balance,idempotency_key,reason)
                    VALUES(%s,%s,%s,%s,%s,%s)''',(uid,actor,amount,balance,key,reason))
        _audit(cur,actor,uid,'wallet.change',{'balance':before},{'balance':balance,'amount':amount,'key':key},reason)
        return balance

    def adjust_wallet(self,actor,uid,amount,key,reason):
        self.admin(actor,'wallet')
        with self.db.get_connection() as (conn,cur):
            balance=self._wallet(cur,uid,actor,amount,'admin:'+key,reason);conn.commit();return balance

    def _activate(self,cur,order,actor,reason):
        uid=int(order['user_id']);_lock(cur,uid)
        snapshot=order['snapshot']
        cur.execute('SELECT expires_at FROM sonar_subscriptions WHERE user_id=%s FOR UPDATE',(uid,));old=cur.fetchone()
        now=datetime.now(timezone.utc)
        previous=old['expires_at'] if old and old['expires_at'] else None
        if not old:
            cur.execute('SELECT subscription_expires_at FROM users WHERE user_id=%s',(uid,))
            legacy=cur.fetchone();previous=legacy['subscription_expires_at'] if legacy else None
        expiry=max(now,previous or now)+timedelta(days=int(snapshot['days']))
        cur.execute('''INSERT INTO sonar_subscriptions(user_id,plan_id,snapshot,status,expires_at)
            VALUES(%s,%s,%s,'active',%s) ON CONFLICT(user_id) DO UPDATE SET
            plan_id=EXCLUDED.plan_id,snapshot=EXCLUDED.snapshot,status='active',expires_at=EXCLUDED.expires_at,updated_at=NOW()''',
            (uid,order['plan_id'],Json(snapshot),expiry))
        # Preserve compatibility with all legacy Bot access checks and displays.
        from zoneinfo import ZoneInfo
        cur.execute('UPDATE users SET expiry_date=%s,server_limit=%s,plan_type=1 WHERE user_id=%s',
                    (expiry.astimezone(ZoneInfo('Asia/Tehran')).strftime('%Y-%m-%d %H:%M:%S'),snapshot['limits']['servers'],uid))
        if order.get('discount_code'):
            # A price accepted at checkout is immutable. Reserve the voucher
            # there, then consume that reservation even if the campaign ends.
            cur.execute('UPDATE sonar_discounts SET reserved=reserved-1,uses=uses+1 WHERE code=%s AND reserved>0 RETURNING code',(order['discount_code'],))
            if not cur.fetchone():raise UserError('رزرو تخفیف معتبر نیست؛ سفارش باید بررسی شود')
        cur.execute("UPDATE sonar_orders SET status='paid',decided_at=NOW() WHERE id=%s",(order['id'],))
        from billing_delivery import enqueue
        enqueue(cur,uid,actor,'order:'+str(order['id']),'اشتراک خدمات پایش '+str(snapshot['name'])+' فعال شد. تاریخ اعتبار: '+expiry.strftime('%Y-%m-%d %H:%M UTC'))
        _audit(cur,actor,uid,'subscription.activate',_safe(old),{'order':str(order['id']),'expires_at':expiry.isoformat()},reason)

    def checkout(self,uid,plan_id,method,key,destination=None,discount_code=None):
        if method not in ('wallet','manual'):raise UserError('روش پرداخت معتبر نیست')
        with self.db.get_connection() as (conn,cur):
            _lock(cur,uid)
            cur.execute('SELECT * FROM sonar_orders WHERE user_id=%s AND idempotency_key=%s',(uid,key));old=cur.fetchone()
            if old:
                if int(old['plan_id'])!=int(plan_id) or old['method']!=method or old['destination_id']!=destination or old['discount_code']!=(discount_code.upper() if discount_code else None):
                    raise UserError('شناسه سفارش با اطلاعات متفاوت تکرار شده است')
                return dict(old)
            cur.execute('SELECT is_banned FROM users WHERE user_id=%s FOR UPDATE',(uid,));user=cur.fetchone()
            if not user or user['is_banned']:raise PermissionError('حساب غیرفعال است')
            cur.execute('SELECT * FROM sonar_plans WHERE id=%s AND enabled=TRUE FOR SHARE',(plan_id,));plan=cur.fetchone()
            if not plan:raise UserError('پلن فعال یافت نشد')
            cur.execute('SELECT snapshot,plan_id FROM sonar_subscriptions WHERE user_id=%s',(uid,));current=cur.fetchone()
            if current and current['plan_id']==plan_id and plan['change_policy']=='new':
                plan=dict(plan)
                for field in ('name','description','price','days','limits','features','policy'):plan[field]=current['snapshot'][field]
            amount=int(plan['price']);code=discount_code.upper() if discount_code else None
            if code:
                cur.execute('SELECT * FROM sonar_discounts WHERE code=%s FOR UPDATE',(code,));discount=cur.fetchone();now=datetime.now(timezone.utc)
                if not discount or not discount['enabled'] or not discount['starts_at']<=now<discount['ends_at'] or discount['uses']+discount['reserved']>=discount['max_uses'] or (discount['user_id'] and discount['user_id']!=uid) or (discount['plan_id'] and discount['plan_id']!=plan_id):raise UserError('کد تخفیف معتبر نیست')
                if discount['first_only']:
                    cur.execute("SELECT 1 FROM sonar_orders WHERE user_id=%s AND (status IN ('paid','refunded') OR (status='pending' AND discount_code=%s)) LIMIT 1",(uid,code))
                    if cur.fetchone():raise UserError('این تخفیف فقط برای خرید اول است')
                if discount.get('renewal_only'):
                    cur.execute("SELECT 1 FROM sonar_orders WHERE user_id=%s AND status IN ('paid','refunded') LIMIT 1",(uid,))
                    if not cur.fetchone():raise UserError('این تخفیف فقط برای تمدید است')
                amount=max(0,amount-(amount*min(discount['value'],100)//100 if discount['kind']=='percent' else discount['value']))
                cur.execute('UPDATE sonar_discounts SET reserved=reserved+1 WHERE code=%s',(code,))
            dest=None
            if method=='manual':
                cur.execute('SELECT * FROM sonar_payment_destinations WHERE id=%s AND enabled=TRUE',(destination,));dest=cur.fetchone()
                if not dest:raise UserError('مقصد پرداخت فعال را انتخاب کنید')
            ident=str(uuid.uuid4());snap=_safe(dict(plan))
            cur.execute('''INSERT INTO sonar_orders(id,user_id,plan_id,snapshot,amount,discount_code,method,destination_id,destination_snapshot,idempotency_key)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *''',
                (ident,uid,plan_id,Json(snap),amount,code,method,destination,Json(_safe(dest)) if dest else None,key))
            order=dict(cur.fetchone())
            if method=='wallet' or amount==0:
                if amount:self._wallet(cur,uid,uid,-amount,'purchase:'+ident,'خرید اشتراک '+plan['name'])
                self._activate(cur,order,uid,'خرید اشتراک');order['status']='paid'
            _audit(cur,uid,uid,'order.create',None,{'id':ident,'amount':amount,'method':method},'ثبت سفارش')
            conn.commit();return order

    def evidence(self,uid,ident,text):
        # Evidence must never contain private keys; it is not a payment verifier.
        with self.db.get_connection() as (conn,cur):
            cur.execute("UPDATE sonar_orders SET evidence=%s WHERE id=%s AND user_id=%s AND status='pending' RETURNING id",(text,ident,uid))
            if not cur.fetchone():raise UserError('سفارش در انتظار پرداخت یافت نشد')
            _audit(cur,uid,uid,'order.evidence',None,{'order':str(ident)},'ثبت مشخصات رسید برای بررسی مدیر');conn.commit()

    def decide(self,actor,ident,approve,reason):
        self.admin(actor,'payments')
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT user_id FROM sonar_orders WHERE id=%s',(ident,));row=cur.fetchone()
            if not row:raise UserError('سفارش یافت نشد')
            _lock(cur,row['user_id'])
            cur.execute('SELECT * FROM sonar_orders WHERE id=%s FOR UPDATE',(ident,));order=dict(cur.fetchone())
            if order['status']!='pending':
                if order['status']==('paid' if approve else 'rejected'):return {'status':order['status']}
                raise UserError('وضعیت سفارش اجازه این عملیات را نمی‌دهد')
            if approve:
                if order['method']!='manual' or not order['evidence']:raise UserError('ابتدا مشخصات رسید لازم است')
                self._activate(cur,order,actor,reason)
            else:
                if order.get('discount_code'):cur.execute('UPDATE sonar_discounts SET reserved=GREATEST(0,reserved-1) WHERE code=%s',(order['discount_code'],))
                cur.execute("UPDATE sonar_orders SET status='rejected',decided_at=NOW() WHERE id=%s",(ident,))
                _audit(cur,actor,order['user_id'],'order.reject',{'status':'pending'},{'order':str(ident),'status':'rejected'},reason)
            conn.commit();return {'status':'paid' if approve else 'rejected'}

    def cancel(self,uid,ident):
        with self.db.get_connection() as (conn,cur):
            _lock(cur,uid)
            cur.execute('SELECT * FROM sonar_orders WHERE id=%s AND user_id=%s FOR UPDATE',(ident,uid));order=cur.fetchone()
            if not order or order['status']!='pending':raise UserError('سفارش در انتظار پرداخت یافت نشد')
            if order['evidence']:raise UserError('برای سفارشی که رسید دارد، ابتدا با مدیر هماهنگ کنید')
            if order['discount_code']:cur.execute('UPDATE sonar_discounts SET reserved=GREATEST(0,reserved-1) WHERE code=%s',(order['discount_code'],))
            cur.execute("UPDATE sonar_orders SET status='rejected',decided_at=NOW() WHERE id=%s",(ident,))
            _audit(cur,uid,uid,'order.cancel',{'status':'pending'},{'order':str(ident),'status':'rejected'},'لغو سفارش پیش از ثبت رسید به درخواست خریدار');conn.commit()

    def refund(self,actor,ident,reason):
        self.admin(actor,'payments')
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT user_id FROM sonar_orders WHERE id=%s',(ident,));row=cur.fetchone()
            if not row:raise UserError('سفارش یافت نشد')
            _lock(cur,row['user_id'])
            cur.execute('SELECT * FROM sonar_orders WHERE id=%s FOR UPDATE',(ident,));order=cur.fetchone()
            if order['status']=='refunded':return
            if order['status']!='paid':raise UserError('فقط سفارش پرداخت‌شده قابل برگشت است')
            if order['amount']:self._wallet(cur,order['user_id'],actor,order['amount'],'refund:'+str(ident),reason)
            cur.execute("UPDATE sonar_orders SET status='refunded',decided_at=NOW() WHERE id=%s",(ident,))
            # Refund is explicit and suspends access; data remains available to read.
            cur.execute("UPDATE sonar_subscriptions SET status='suspended',updated_at=NOW() WHERE user_id=%s",(order['user_id'],))
            _audit(cur,actor,order['user_id'],'order.refund',{'status':'paid'},{'order':str(ident),'status':'refunded'},reason);conn.commit()

    def manage(self,actor,uid,status=None,days=0,overrides=None,reason='',plan_id=None):
        self.admin(actor,'subscriptions')
        if status is not None and status not in ('active','suspended','cancelled'):raise UserError('وضعیت معتبر نیست')
        if overrides is not None:limits_valid(overrides)
        with self.db.get_connection() as (conn,cur):
            _lock(cur,uid)
            cur.execute('SELECT * FROM sonar_subscriptions WHERE user_id=%s FOR UPDATE',(uid,));before=cur.fetchone()
            if plan_id:
                cur.execute('SELECT * FROM sonar_plans WHERE id=%s',(plan_id,));plan=cur.fetchone()
                if not plan:raise UserError('پلن یافت نشد')
                cur.execute('''INSERT INTO sonar_subscriptions(user_id,plan_id,snapshot,expires_at) VALUES(%s,%s,%s,NOW())
                    ON CONFLICT(user_id) DO UPDATE SET plan_id=EXCLUDED.plan_id,snapshot=EXCLUDED.snapshot''',
                    (uid,plan_id,Json(_safe(plan))))
            elif not before:raise UserError('ابتدا پلن کاربر را تعیین کنید')
            cur.execute('''UPDATE sonar_subscriptions SET status=COALESCE(%s,status),
                expires_at=CASE WHEN %s=0 THEN expires_at ELSE GREATEST(COALESCE(expires_at,NOW()),NOW())+(%s*INTERVAL '1 day') END,
                overrides=COALESCE(%s,overrides),updated_at=NOW() WHERE user_id=%s RETURNING *''',
                (status,days,days,Json(overrides) if overrides is not None else None,uid));after=cur.fetchone()
            cur.execute("UPDATE users SET expiry_date=to_char(%s::timestamptz AT TIME ZONE 'Asia/Tehran','YYYY-MM-DD HH24:MI:SS'),server_limit=%s WHERE user_id=%s",(after['expires_at'],{**after['snapshot']['limits'],**after['overrides']}['servers'],uid))
            _audit(cur,actor,uid,'subscription.manage',_safe(before),_safe(after),reason);conn.commit()
        return self.subscription(uid)

    def destinations(self,actor=None,all_items=False):
        if all_items:self.admin(actor)
        return self.db.execute('SELECT * FROM sonar_payment_destinations '+('' if all_items else 'WHERE enabled=TRUE ')+'ORDER BY id',fetch='all')

    def save_destination(self,actor,data,ident=None,reason=''):
        self.admin(actor,'destinations')
        if data['kind']=='crypto' and not data['network'].strip():raise UserError('شبکه ارز دیجیتال الزامی است')
        with self.db.get_connection() as (conn,cur):
            before=None
            if ident:
                cur.execute('SELECT * FROM sonar_payment_destinations WHERE id=%s FOR UPDATE',(ident,));before=cur.fetchone()
                if not before:raise UserError('مقصد یافت نشد')
                cur.execute('''UPDATE sonar_payment_destinations SET kind=%s,name=%s,network=%s,address=%s,holder=%s,instructions=%s,enabled=%s WHERE id=%s''',tuple(data[k] for k in ('kind','name','network','address','holder','instructions','enabled'))+(ident,))
            else:
                cur.execute('''INSERT INTO sonar_payment_destinations(kind,name,network,address,holder,instructions,enabled)
                    VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id''',tuple(data[k] for k in ('kind','name','network','address','holder','instructions','enabled')));ident=cur.fetchone()['id']
            _audit(cur,actor,None,'destination.save',_safe(before),{'id':ident,**data},reason);conn.commit();return ident

    def save_discount(self,actor,data,reason):
        self.admin(actor,'discounts')
        if not re.fullmatch(r'[A-Z0-9_-]{3,40}',data['code']):raise UserError('کد تخفیف باید حروف انگلیسی بزرگ یا عدد باشد')
        if data['ends_at']<=data['starts_at'] or (data['kind']=='percent' and data['value']>100):raise UserError('بازه یا مقدار تخفیف معتبر نیست')
        if data.get('first_only') and data.get('renewal_only'):raise UserError('تخفیف خرید اول و تمدید را هم‌زمان فعال نکنید')
        data.setdefault('renewal_only',False)
        with self.db.get_connection() as (conn,cur):
            cur.execute('SELECT * FROM sonar_discounts WHERE code=%s FOR UPDATE',(data['code'],));before=cur.fetchone()
            fields=('code','kind','value','starts_at','ends_at','max_uses','user_id','plan_id','first_only','renewal_only','enabled')
            cur.execute('''INSERT INTO sonar_discounts(code,kind,value,starts_at,ends_at,max_uses,user_id,plan_id,first_only,renewal_only,enabled)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(code) DO UPDATE SET
               kind=EXCLUDED.kind,value=EXCLUDED.value,starts_at=EXCLUDED.starts_at,ends_at=EXCLUDED.ends_at,
               max_uses=EXCLUDED.max_uses,user_id=EXCLUDED.user_id,plan_id=EXCLUDED.plan_id,first_only=EXCLUDED.first_only,renewal_only=EXCLUDED.renewal_only,enabled=EXCLUDED.enabled''',tuple(data[k] for k in fields))
            _audit(cur,actor,None,'discount.save',_safe(before),_safe(data),reason);conn.commit()

    def users(self,actor,q='',page=1):
        self.admin(actor,'users.read')
        term='%'+q[:80]+'%'
        return self.db.execute('''SELECT u.user_id,u.full_name,u.registered_at,u.last_active_at,u.is_banned,u.wallet_balance,s.status,s.expires_at,s.snapshot->>'name' plan
            FROM users u LEFT JOIN sonar_subscriptions s ON s.user_id=u.user_id
            WHERE u.full_name ILIKE %s OR u.user_id::text ILIKE %s ORDER BY u.user_id LIMIT 50 OFFSET %s''',
            (term,term,(page-1)*50),fetch='all')

    def set_user(self,actor,uid,name,banned,reason):
        self.admin(actor,'users.write')
        if uid==self.admin_id and banned:raise UserError('مدیر اصلی را نمی‌توان مسدود کرد')
        with self.db.get_connection() as (conn,cur):
            _lock(cur,uid)
            cur.execute('SELECT user_id,full_name,is_banned FROM users WHERE user_id=%s FOR UPDATE',(uid,));before=cur.fetchone()
            cur.execute('''INSERT INTO users(user_id,full_name,is_banned) VALUES(%s,%s,%s)
                ON CONFLICT(user_id) DO UPDATE SET full_name=EXCLUDED.full_name,is_banned=EXCLUDED.is_banned''',(uid,name,int(banned)))
            if banned:cur.execute('UPDATE sonar_web_sessions SET revoked_at=NOW() WHERE user_id=%s',(uid,))
            _audit(cur,actor,uid,'user.manage',_safe(before),{'full_name':name,'is_banned':banned},reason);conn.commit()

    def message(self,actor,uid,text,key,reason):
        self.admin(actor,'messages')
        if not self.db.get_user(uid):raise UserError('کاربر یافت نشد')
        from control_store import Control
        from pro_store import ProStore
        from billing_delivery import enqueue
        Control(ProStore(self.db)).rate_limit(actor,'billing_message',10)
        with self.db.get_connection() as (conn,cur):
            _lock(cur,uid)
            ident=enqueue(cur,uid,actor,'admin:'+str(actor)+':'+key,text)
            _audit(cur,actor,uid,'message.queued',None,{'id':ident},reason);conn.commit();return ident

    def finances(self,actor,days=30):
        self.admin(actor,'reports')
        return self.db.execute('''SELECT date_trunc('day',created_at) AS bucket_day,plan_id,method,status,
            COUNT(*) orders,SUM(amount) amount FROM sonar_orders WHERE created_at>=NOW()-(%s*INTERVAL '1 day')
            GROUP BY bucket_day,plan_id,method,status ORDER BY bucket_day DESC''',(days,),fetch='all')


def _json(value):return json.dumps(value,default=str,ensure_ascii=False)
def _safe(value):return json.loads(_json(value)) if value is not None else None

CAPABILITIES=('users.read','users.write','plans','subscriptions','wallet','payments','destinations','discounts','reports','audit','messages')


def install_staff(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS sonar_billing_staff (
          user_id BIGINT PRIMARY KEY REFERENCES users(user_id),label VARCHAR(80) NOT NULL,
          capabilities TEXT[] NOT NULL,enabled BOOLEAN NOT NULL DEFAULT TRUE,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())''')
        conn.commit()
