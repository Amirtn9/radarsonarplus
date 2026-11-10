"""Owner-specific channel/topic routing with a private-chat fallback."""
import logging
import asyncio
import time
import weakref
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


async def send_notification(context, owner, text, usage, *, parse_mode=None, db=None):
    if db is None:
        from database import Database
        db = Database()
    channels = await run_sync(db.get_user_channels, int(owner))
    # Both report schedulers share the user's single «گزارش‌ها» destination.
    usage = 'report' if usage in ('config_report','server_report') else usage
    specific = [row for row in channels if row.get('usage_type') == usage]
    selected = specific or [row for row in channels if row.get('usage_type') == 'all']
    targets = {(int(row['chat_id']), int(row.get('topic_id') or 0)) for row in selected}
    if not targets:
        targets = {(int(owner), 0)}
    delivered = False
    for chat, topic in targets:
        for start in range(0, max(1, len(text)), 3800):
            kwargs = {'chat_id': chat, 'text': text[start:start + 3800], 'parse_mode': parse_mode}
            if topic:
                kwargs['message_thread_id'] = topic
            try:
                await send_limited(context.bot,**kwargs)
                delivered = True
            except Exception as exc:
                log.warning('Notification failed owner=%s destination=%s type=%s', owner, chat, type(exc).__name__)
    if not delivered and (int(owner), 0) not in targets:
        try:
            await send_limited(context.bot,chat_id=int(owner), text=str(text)[:3800])
            delivered = True
        except Exception:
            log.warning('Private notification fallback failed owner=%s', owner)
    return delivered
