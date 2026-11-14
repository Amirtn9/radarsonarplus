"""Multiple verified Telegram destinations, category routing and delivery retry."""
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from typing import Literal
import asyncio

CATEGORIES=('all','servers','configs','monitoring','agents','alerts','team','system','report')
class Destination(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    chat_id:int
    topic_id:int=Field(default=0,ge=0)
    categories:list[Literal['all','servers','configs','monitoring','agents','alerts','team','system','report']]=Field(min_length=1,max_length=9)
    enabled:bool=True


def install_schema(db):
    with db.get_connection() as (conn,cur):
        cur.execute('''ALTER TABLE channels ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;
            ALTER TABLE channels ADD COLUMN IF NOT EXISTS categories TEXT[];
            UPDATE channels SET categories=ARRAY[usage_type] WHERE categories IS NULL;
            CREATE TABLE IF NOT EXISTS sonar_notification_deliveries (
                owner_id BIGINT NOT NULL,event_key TEXT NOT NULL,chat_id BIGINT NOT NULL,topic_id BIGINT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,sent_at TIMESTAMPTZ,last_error VARCHAR(80),
                lease_until TIMESTAMPTZ,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY(owner_id,event_key,chat_id,topic_id));
            CREATE TABLE IF NOT EXISTS sonar_server_sample_leases (
                server_id BIGINT PRIMARY KEY REFERENCES servers(id) ON DELETE CASCADE,
                expires_at TIMESTAMPTZ NOT NULL);
            ''')
        conn.commit()


async def verify_destination(token,chat_id):
    if not token:raise HTTPException(status_code=503,detail='توکن ربات تنظیم نشده است')
    from telegram import Bot
    try:
        async with Bot(token) as bot:
            me=await asyncio.wait_for(bot.get_me(),timeout=10)
            chat=await asyncio.wait_for(bot.get_chat(chat_id),timeout=10)
            member=await asyncio.wait_for(bot.get_chat_member(chat_id,me.id),timeout=10)
            if member.status not in ('administrator','creator','member'):raise ValueError('membership')
            if chat.type=='channel' and (member.status!='administrator' or not getattr(member,'can_post_messages',False)):
                raise ValueError('post permission')
            if chat.type in ('group','supergroup') and member.status=='member' and chat.permissions and not chat.permissions.can_send_messages:
                raise ValueError('post permission')
    except Exception as error:
        if isinstance(error,HTTPException):raise
        raise HTTPException(status_code=400,detail='دسترسی ارسال ربات به این مقصد تأیید نشد؛ عضویت و مجوزها را بررسی کنید') from None


def router(store,actor,guarded,get_token):
    api=APIRouter(prefix='/api/notifications')
    @api.get('/destinations')
    async def destinations(uid:int=Depends(actor)):
        return await guarded(store.db.get_user_channels,uid)
    async def save(uid,body,ident=None):
        owner=await guarded(store.workspace,uid)
        await guarded(store.require,uid,owner,True)
        if body.enabled:await verify_destination(get_token(),body.chat_id)
        def write():
            with store.db.get_connection() as (conn,cur):
                if ident:
                    cur.execute('''UPDATE channels SET name=%s,chat_id=%s,topic_id=%s,categories=%s,enabled=%s
                        WHERE id=%s AND owner_id=%s RETURNING id''',(body.name,body.chat_id,body.topic_id,body.categories,body.enabled,ident,owner))
                else:
                    cur.execute('''INSERT INTO channels(owner_id,name,chat_id,topic_id,categories,usage_type,enabled)
                        VALUES(%s,%s,%s,%s,%s,'all',%s) RETURNING id''',(owner,body.name,body.chat_id,body.topic_id,body.categories,body.enabled))
                row=cur.fetchone()
                if not row:raise HTTPException(status_code=404,detail='مقصد یافت نشد')
                conn.commit();return {'id':row['id']}
        result=await guarded(write);await guarded(store.audit,owner,uid,'notification.destination',str(result['id']));return result
    @api.post('/destinations')
    async def create(body:Destination,uid:int=Depends(actor)):return await save(uid,body)
    @api.put('/destinations/{ident}')
    async def update(ident:int,body:Destination,uid:int=Depends(actor)):return await save(uid,body,ident)
    @api.delete('/destinations/{ident}')
    async def delete(ident:int,uid:int=Depends(actor)):
        owner=await guarded(store.workspace,uid);await guarded(store.require,uid,owner,True)
        await guarded(store.db.delete_channel,ident,owner);return {'ok':True}
    @api.post('/destinations/{ident}/test')
    async def test(ident:int,uid:int=Depends(actor)):
        owner=await guarded(store.workspace,uid);await guarded(store.require,uid,owner,True)
        row=await guarded(store.db.execute,'SELECT chat_id,topic_id FROM channels WHERE id=%s AND owner_id=%s',(ident,owner),'one')
        if not row:raise HTTPException(status_code=404,detail='مقصد یافت نشد')
        await verify_destination(get_token(),row['chat_id'])
        from telegram import Bot
        async with Bot(get_token()) as bot:
            await asyncio.wait_for(bot.send_message(row['chat_id'],'پیام آزمایشی اطلاع‌رسانی رادار سونار',message_thread_id=row['topic_id'] or None),timeout=15)
        return {'ok':True}
    @api.get('/history')
    async def history(uid:int=Depends(actor)):
        owner=await guarded(store.workspace,uid)
        return await guarded(store.db.execute,'SELECT event_key,chat_id,topic_id,attempts,sent_at,last_error,updated_at FROM sonar_notification_deliveries WHERE owner_id=%s ORDER BY updated_at DESC LIMIT 100',(owner,),'all')
    @api.post('/outbox/{ident}/retry')
    async def retry(ident:int,uid:int=Depends(actor)):
        owner=await guarded(store.workspace,uid);await guarded(store.require,uid,owner,True)
        row=await guarded(store.db.execute,'''UPDATE pro_notification_outbox SET attempts=0,dead_at=NULL,claimed_until=NULL,next_attempt_at=NOW(),last_error=NULL
          WHERE id=%s AND owner_id=%s AND sent_at IS NULL AND skipped_at IS NULL RETURNING id''',(ident,owner),'one')
        if not row:raise HTTPException(status_code=404,detail='اعلان قابل تلاش مجدد یافت نشد')
        return {'ok':True}
    @api.get('/outbox')
    async def outbox(uid:int=Depends(actor)):
        owner=await guarded(store.workspace,uid)
        return await guarded(store.db.execute,'SELECT id,event_type,attempts,sent_at,dead_at,last_error FROM pro_notification_outbox WHERE owner_id=%s ORDER BY id DESC LIMIT 100',(owner,),'all')
    return api


def claim_sample(db,sid):
    with db.get_connection() as (conn,cur):
        cur.execute('SELECT id FROM servers WHERE id=%s AND next_observation_at<=NOW() FOR UPDATE',(sid,))
        if not cur.fetchone():conn.commit();return False
        cur.execute('''INSERT INTO sonar_server_sample_leases(server_id,expires_at)
          VALUES(%s,NOW()+INTERVAL '90 seconds') ON CONFLICT(server_id) DO UPDATE SET expires_at=EXCLUDED.expires_at
          WHERE sonar_server_sample_leases.expires_at<NOW() RETURNING server_id''',(sid,))
        if not cur.fetchone():conn.commit();return False
        cur.execute('''UPDATE servers SET next_observation_at=NOW()+(GREATEST(sonar_min_interval(servers.owner_id),COALESCE((SELECT CASE WHEN value ~ '^[0-9]+$'
            THEN GREATEST(60,LEAST(3600,value::integer)) ELSE 120 END FROM settings WHERE owner_id=servers.owner_id
            AND key='observation_interval_seconds'),120))*INTERVAL '1 second') WHERE id=%s''',(sid,))
        conn.commit();return True
