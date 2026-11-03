"""Compatibility helpers for safe Telegram callback acknowledgements.

The server-management module imports ``safe_answer`` from this module. Keep this
thin adapter backed by the project's shared Telegram safety implementation so
expired callback IDs and transient Telegram failures never abort an operation.
"""
from __future__ import annotations

from telegram_safety import safe_callback_answer


async def safe_answer(query, text=None, *, show_alert=False, cache_time=0):
    """Best-effort callback answer; return False rather than raising on stale IDs."""
    kwargs = {"cache_time": max(0, int(cache_time)), "show_alert": bool(show_alert)}
    if text is not None:
        kwargs["text"] = str(text)[:190]
    return await safe_callback_answer(query, **kwargs)
