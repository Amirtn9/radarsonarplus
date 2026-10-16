"""Owner-specific channel/topic routing with a private-chat fallback."""
import logging

from runtime import run_sync

log = logging.getLogger(__name__)


async def send_notification(context, owner, text, usage, *, parse_mode=None, db=None):
    if db is None:
        from database import Database
        db = Database()
    channels = await run_sync(db.get_user_channels, int(owner))
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
                await context.bot.send_message(**kwargs)
                delivered = True
            except Exception as exc:
                log.warning('Notification failed owner=%s destination=%s type=%s', owner, chat, type(exc).__name__)
    if not delivered and (int(owner), 0) not in targets:
        try:
            await context.bot.send_message(chat_id=int(owner), text=str(text)[:3800])
            delivered = True
        except Exception:
            log.warning('Private notification fallback failed owner=%s', owner)
    return delivered
