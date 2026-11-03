"""Deliver durable monitor notifications; no state is lost during quiet hours.

The outbox is at-least-once: a process crash *after* Telegram accepts a message
but before acknowledging SQL may lead to a duplicate. The event ID is included
for de-duplication and operational traceability.
"""
from __future__ import annotations
import asyncio
import logging
from service_monitor import ServiceStore
from service_probes import check, ProbeResult
from pro_store import ProStore
from pro_jobs import quiet_now
from notifications import send_notification

log=logging.getLogger(__name__)
_running=False


def claim_notifications(db, limit=30):
    """Short lease lets interrupted workers be retried after restart."""
    with db.get_connection() as (conn,cur):
        cur.execute("""SELECT id,owner_id,event_type,message,attempts FROM pro_notification_outbox
           WHERE sent_at IS NULL AND skipped_at IS NULL AND dead_at IS NULL
             AND next_attempt_at<=NOW() AND (claimed_until IS NULL OR claimed_until<NOW())
           ORDER BY id FOR UPDATE SKIP LOCKED LIMIT %s""",(max(1,min(50,int(limit))),))
        rows=[dict(r) for r in cur.fetchall()]
        for r in rows:
            cur.execute("""UPDATE pro_notification_outbox SET claimed_until=NOW()+INTERVAL '120 seconds'
                WHERE id=%s""",(r['id'],))
        conn.commit()
        return rows


def mark_notification(db,ident,result):
    """No logging of raw provider exception (may include chat identifiers/tokens)."""
    if result=='sent':
        db.execute("""UPDATE pro_notification_outbox SET sent_at=NOW(),claimed_until=NULL,last_error=NULL
            WHERE id=%s AND sent_at IS NULL""",(ident,))
    elif result=='skipped':
        db.execute("""UPDATE pro_notification_outbox SET skipped_at=NOW(),claimed_until=NULL
            WHERE id=%s AND sent_at IS NULL""",(ident,))
    elif result=='quiet':
        db.execute("""UPDATE pro_notification_outbox SET claimed_until=NULL,
            next_attempt_at=NOW()+INTERVAL '60 seconds' WHERE id=%s""",(ident,))
    else:
        db.execute("""UPDATE pro_notification_outbox SET attempts=attempts+1,claimed_until=NULL,
            next_attempt_at=NOW()+((LEAST(3600,30*POWER(2,LEAST(attempts,7))))::INTEGER * INTERVAL '1 second'),
            dead_at=CASE WHEN attempts>=7 THEN NOW() ELSE NULL END,
            last_error='TELEGRAM_SEND_FAILED' WHERE id=%s AND sent_at IS NULL""",(ident,))


async def flush_notifications(context, pro=None):
    pro=pro or ProStore()
    batch=await asyncio.to_thread(claim_notifications,pro.db)
    for row in batch:
        ident=row['id'];uid=row['owner_id']
        try:
            allowed,_=await asyncio.to_thread(pro.db.check_access,uid)
            if not allowed:
                await asyncio.to_thread(mark_notification,pro.db,ident,'skipped');continue
            prefs=await asyncio.to_thread(pro.preferences,uid)
            if quiet_now(prefs):
                await asyncio.to_thread(mark_notification,pro.db,ident,'quiet');continue
            if not await asyncio.to_thread(pro.should_notify,uid,row['event_type']):
                await asyncio.to_thread(mark_notification,pro.db,ident,'skipped');continue
            delivered=await send_notification(context,uid,f'{row["message"]}\nرویداد #{ident}',
                                              'down',db=pro.db)
            if not delivered:
                raise RuntimeError('NO_TELEGRAM_DESTINATION_DELIVERED')
        except Exception:
            log.warning('Delivery deferred: event_id=%s',ident)
            await asyncio.to_thread(mark_notification,pro.db,ident,'failed')
        else:
            await asyncio.to_thread(mark_notification,pro.db,ident,'sent')


async def service_check_job(context):
    global _running
    if _running:return
    _running=True
    try:
        store=ServiceStore(ProStore())
        due=await asyncio.to_thread(store.claim,20)
        semaphore=asyncio.Semaphore(5)
        async def process(item):
            async with semaphore:
                owner=item['owner_id']
                try:
                    allowed,_=await asyncio.to_thread(store.db.check_access,owner)
                    if not allowed:return
                    try:
                        result=await asyncio.wait_for(
                            asyncio.to_thread(check,item['kind'],item['target'],item['timeout_seconds'],item['expected_status']),
                            timeout=item['timeout_seconds']+8)
                    except asyncio.TimeoutError:
                        result=ProbeResult(False,None,'Probe timeout')
                    await asyncio.to_thread(store.record,item,result)
                except Exception:
                    log.exception('Uptime job failed id=%s',item.get('id'))
        await asyncio.gather(*(process(row) for row in due),return_exceptions=True)
        # Flush independently of transitions; due may be empty after quiet hours.
        await flush_notifications(context,store.pro)
    except Exception:log.exception('Service monitor cycle failed')
    finally:_running=False
