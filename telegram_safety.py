"""Best-effort Telegram callback acknowledgement helpers."""
from __future__ import annotations
import asyncio
import logging
try:
    from telegram.error import BadRequest, NetworkError
except Exception:  # Source-only tests may load handlers without Telegram installed.
    class BadRequest(Exception):
        pass
    class NetworkError(Exception):
        pass

logger = logging.getLogger(__name__)


async def safe_callback_answer(query, *args, **kwargs):
    """Acknowledge callback queries without letting expired IDs abort actions.

    Telegram callback query IDs have a short validity window. A stale callback
    acknowledgement is a UI race; the action handler should still finish safely.
    """
    if query is None:
        return False
    try:
        await query.answer(*args, **kwargs)
        return True
    except BadRequest as exc:
        message = str(exc).lower()
        stale = ('query is too old', 'query id is invalid', 'query is not found',
                 'response timeout expired', 'query too old')
        if any(item in message for item in stale):
            logger.debug('Ignoring expired Telegram callback acknowledgement')
        else:
            logger.warning('Telegram callback acknowledgement rejected: %s', type(exc).__name__)
        return False
    except (NetworkError, asyncio.TimeoutError, TimeoutError) as exc:
        logger.debug('Telegram callback acknowledgement transport failure: %s', type(exc).__name__)
        return False
    except Exception as exc:
        logger.debug('Telegram callback acknowledgement failed: %s', type(exc).__name__)
        return False
