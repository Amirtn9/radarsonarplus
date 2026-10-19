"""Telegram JobQueue integration for persisted, bounded uptime checks."""
from __future__ import annotations
import asyncio
import logging
from service_monitor import ServiceStore
from service_probes import check, ProbeResult
from pro_store import ProStore
from pro_jobs import quiet_now

log=logging.getLogger(__name__)
_running=False

async def service_check_job(context):
    global _running
    if _running: return
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
                    if not allowed: return
                    try:
                        result=await asyncio.wait_for(
                            asyncio.to_thread(check,item['kind'],item['target'],item['timeout_seconds'],item['expected_status']),
                            timeout=item['timeout_seconds']+8)
                    except asyncio.TimeoutError:
                        result=ProbeResult(False,None,'Probe timeout')
                    event=await asyncio.to_thread(store.record,item,result)
                    if not event:return
                    should=await asyncio.to_thread(store.pro.should_notify,owner,
                                                  'service_failure' if event=='failure' else 'service_recovery')
                    if not should:return
                    pref=await asyncio.to_thread(store.pro.preferences,owner)
                    if quiet_now(pref):return
                    glyph='🔴' if event=='failure' else '🟢'
                    state='قطع شد' if event=='failure' else 'بازیابی شد'
                    # HTML/Markdown parsing deliberately disabled to prevent injected names.
                    await context.bot.send_message(chat_id=owner,text=(
                        f'{glyph} رادار سونار · سرویس {item["name"][:120]} {state}\n'
                        f'شناسه: {item["id"]} | {result.detail}'))
                except Exception:
                    log.exception('Uptime job failed id=%s',item.get('id'))
        await asyncio.gather(*(process(row) for row in due),return_exceptions=True)
    except Exception:log.exception('Service monitor cycle failed')
    finally:_running=False
