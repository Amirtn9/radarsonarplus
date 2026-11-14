"""Owner-specific channel/topic routing with a private-chat fallback."""
import logging
import asyncio
import time
import weakref
import uuid
from telegram.error import RetryAfter

from runtime import run_sync

log = logging.getLogger(__name__)


_SEND_INTERVAL=1.05
_LIMITS=weakref.WeakKeyDictionary()


async def send_limited(bot,**kwargs):
    """Serialize alert messages per chat, including topics and fallback sends."""
    loop=asyncio.get_running_loop()
    entries=_LIMITS.setdefault(loop,{})
    now=time.monotonic()
    for key,state in list(entries.items()):
        if not state[0].locked() and now-state[1]>600:entries.pop(key,None)
    chat=int(kwargs['chat_id'])
    state=entries.setdefault(chat,[asyncio.Lock(),0.0])
    async with state[0]:
        delay=_SEND_INTERVAL-(time.monotonic()-state[1])
        if delay>0:await asyncio.sleep(delay)
        for attempt in range(2):
            state[1]=time.monotonic()
            try:return await bot.send_message(**kwargs)
            except RetryAfter as exc:
                seconds=exc.retry_after
                if hasattr(seconds,'total_seconds'):seconds=seconds.total_seconds()
                seconds=max(float(seconds),_SEND_INTERVAL)
                if attempt or seconds>120:raise
                await asyncio.sleep(seconds)


async def send_notification(context, owner, text, usage, *, parse_mode=None, db=None, event_key=None):
    if db is None:
        from database import Database
        db = Database()
    channels = await run_sync(db.get_user_channels, int(owner))
    # Both report schedulers share the user's single «گزارش‌ها» destination.
    usage = 'report' if usage in ('config_report','server_report') else usage
    category={'down':'alerts','resource':'servers','expiry':'servers','config_report':'configs','server_report':'servers','service':'monitoring'}.get(usage,usage)
    channels=[row for row in channels if row.get('enabled',True)]
    selected=[row for row in channels if any(value in ('all',usage,category) for value in (row.get('categories') or [row.get('usage_type')]))]
    targets = {(int(row['chat_id']), int(row.get('topic_id') or 0)) for row in selected}
    if not targets:
        targets = {(int(owner), 0)}
    delivered = False
    failed = False
    durable=callable(getattr(db,'get_connection',None))
    event_key=event_key or ('direct:'+uuid.uuid4().hex)
    for chat, topic in sorted(targets):
        for start in range(0, max(1, len(text)), 3800):
            kwargs = {'chat_id': chat, 'text': text[start:start + 3800], 'parse_mode': parse_mode}
            if topic:
                kwargs['message_thread_id'] = topic
            chunk_key=event_key+':'+str(start)
            try:
                if durable:
                    claim=await run_sync(_claim_delivery,db,owner,chunk_key,chat,topic)
                    if claim=='sent':delivered=True;continue
                    if claim!='claimed':failed=True;continue
                await send_limited(context.bot,**kwargs)
                if durable:await run_sync(_mark_delivery,db,owner,chunk_key,chat,topic,True,None)
                delivered = True
            except Exception as exc:
                failed=True
                if durable:await run_sync(_mark_delivery,db,owner,chunk_key,chat,topic,False,type(exc).__name__)
                log.warning('Notification failed owner=%s destination=%s type=%s', owner, chat, type(exc).__name__)
    if not delivered and (int(owner), 0) not in targets:
        try:
            await send_limited(context.bot,chat_id=int(owner), text=str(text)[:3800])
            delivered = True
            failed = False
        except Exception:
            log.warning('Private notification fallback failed owner=%s', owner)
    return delivered and not failed


def _claim_delivery(db,owner,key,chat,topic):
    with db.get_connection() as (conn,cur):
        cur.execute('INSERT INTO sonar_notification_deliveries(owner_id,event_key,chat_id,topic_id) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(owner,key,chat,topic))
        cur.execute('SELECT sent_at,lease_until FROM sonar_notification_deliveries WHERE owner_id=%s AND event_key=%s AND chat_id=%s AND topic_id=%s FOR UPDATE',(owner,key,chat,topic));row=cur.fetchone()
        if row['sent_at']:conn.commit();return 'sent'
        cur.execute("UPDATE sonar_notification_deliveries SET attempts=attempts+1,lease_until=NOW()+INTERVAL '180 seconds',updated_at=NOW() WHERE owner_id=%s AND event_key=%s AND chat_id=%s AND topic_id=%s AND (lease_until IS NULL OR lease_until<NOW()) RETURNING attempts",(owner,key,chat,topic))
        claimed=bool(cur.fetchone());conn.commit();return 'claimed' if claimed else 'busy'


def _mark_delivery(db,owner,key,chat,topic,sent,error):
    db.execute('UPDATE sonar_notification_deliveries SET sent_at=CASE WHEN %s THEN NOW() ELSE NULL END,last_error=%s,lease_until=NULL,updated_at=NOW() WHERE owner_id=%s AND event_key=%s AND chat_id=%s AND topic_id=%s',(sent,error,owner,key,chat,topic))
