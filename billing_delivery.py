"""Durable private billing messages; never route financial data to a shared team."""
import asyncio
import logging
log=logging.getLogger(__name__)


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''CREATE TABLE IF NOT EXISTS sonar_billing_messages (
          id BIGSERIAL PRIMARY KEY,user_id BIGINT NOT NULL,actor_id BIGINT NOT NULL,
          source_key VARCHAR(150) NOT NULL UNIQUE,body_cipher TEXT NOT NULL,body_hash CHAR(64) NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
          next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),lease_until TIMESTAMPTZ,
          error_code VARCHAR(80),created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),sent_at TIMESTAMPTZ)''')
        conn.commit()


def enqueue(cur,uid,actor,source,text):
    import hashlib
    from core import sec
    from user_errors import UserError
    digest=hashlib.sha256(text.encode()).hexdigest()
    cur.execute('SELECT user_id,body_hash,id FROM sonar_billing_messages WHERE source_key=%s',(source,));old=cur.fetchone()
    if old:
        if old['user_id']!=uid or old['body_hash']!=digest:raise UserError('شناسه پیام با اطلاعات متفاوت تکرار شده است')
        return old['id']
    cur.execute('INSERT INTO sonar_billing_messages(user_id,actor_id,source_key,body_cipher,body_hash) VALUES(%s,%s,%s,%s,%s) RETURNING id',(uid,actor,source,sec.encrypt(text),digest))
    return cur.fetchone()['id']


def claim(db):
    with db.get_connection() as (conn,cur):
        cur.execute("SELECT * FROM sonar_billing_messages WHERE state='pending' AND next_attempt_at<=NOW() AND (lease_until IS NULL OR lease_until<NOW()) ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 5")
        rows=[dict(row) for row in cur.fetchall()]
        for row in rows:cur.execute("UPDATE sonar_billing_messages SET lease_until=NOW()+INTERVAL '180 seconds',attempts=attempts+1 WHERE id=%s",(row['id'],))
        conn.commit();return rows


async def deliver(db,bot):
    from core import sec
    from notifications import send_limited
    for message in await asyncio.to_thread(claim,db):
        try:
            await asyncio.wait_for(send_limited(bot,chat_id=message['user_id'],text=sec.decrypt(message['body_cipher'])),timeout=20)
        except Exception as error:
            await asyncio.to_thread(db.execute,"UPDATE sonar_billing_messages SET state=%s,error_code=%s,lease_until=NULL,next_attempt_at=NOW()+(%s*INTERVAL '1 minute') WHERE id=%s",('failed' if message['attempts']+1>=5 else 'pending',type(error).__name__,min(60,2**message['attempts']),message['id']))
        else:
            await asyncio.to_thread(db.execute,"UPDATE sonar_billing_messages SET state='sent',sent_at=NOW(),lease_until=NULL,error_code=NULL WHERE id=%s",(message['id'],))


async def worker(db,stop,get_token):
    from telegram import Bot
    while not stop.is_set():
        try:
            await asyncio.to_thread(expire_unpaid,db)
            token=get_token()
            pending=await asyncio.to_thread(db.execute,"SELECT 1 FROM sonar_billing_messages WHERE state='pending' AND next_attempt_at<=NOW() LIMIT 1",None,'one')
            if token and pending:
                async with Bot(token) as bot:await deliver(db,bot)
        except asyncio.CancelledError:raise
        except Exception:log.warning('Private billing message delivery unavailable')
        try:await asyncio.wait_for(stop.wait(),timeout=15)
        except asyncio.TimeoutError:pass


async def bot_job(context):
    from database import Database
    db=Database()
    await asyncio.to_thread(expire_unpaid,db)
    await deliver(db,context.bot)


def expire_unpaid(db):
    from billing import _lock,_audit
    rows=db.execute("SELECT id,user_id FROM sonar_orders WHERE status='pending' AND evidence='' AND created_at<NOW()-INTERVAL '24 hours' ORDER BY created_at LIMIT 50",fetch='all')
    for item in rows:
        with db.get_connection() as (conn,cur):
            _lock(cur,item['user_id'])
            cur.execute("SELECT * FROM sonar_orders WHERE id=%s AND status='pending' AND evidence='' AND created_at<NOW()-INTERVAL '24 hours' FOR UPDATE",(item['id'],));order=cur.fetchone()
            if order:
                if order['discount_code']:cur.execute('UPDATE sonar_discounts SET reserved=GREATEST(0,reserved-1) WHERE code=%s',(order['discount_code'],))
                cur.execute("UPDATE sonar_orders SET status='rejected',decided_at=NOW() WHERE id=%s",(order['id'],))
                _audit(cur,0,item['user_id'],'order.expired',{'status':'pending'},{'order':str(order['id'])},'انقضای سفارش بدون رسید پس از ۲۴ ساعت')
            conn.commit()
