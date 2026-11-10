import asyncio
import time
from types import SimpleNamespace

import pytest
import notifications
from telegram.error import RetryAfter


@pytest.mark.asyncio
async def test_alerts_share_chat_limit_across_topics(monkeypatch):
    monkeypatch.setattr(notifications,'_SEND_INTERVAL',.02)
    times=[]
    async def send(**kwargs):times.append(time.monotonic())
    bot=SimpleNamespace(send_message=send)
    await asyncio.gather(*(notifications.send_limited(bot,chat_id=901,message_thread_id=i,text='fixture') for i in range(4)))
    assert len(times)==4
    assert all(b-a>=.018 for a,b in zip(times,times[1:]))


@pytest.mark.asyncio
async def test_alert_retry_honors_telegram_retry_after(monkeypatch):
    calls=[];delays=[]
    async def send(**kwargs):
        calls.append(kwargs)
        if len(calls)==1:raise RetryAfter(2)
        return 'sent'
    async def sleep(value):delays.append(value)
    monkeypatch.setattr(notifications.asyncio,'sleep',sleep)
    assert await notifications.send_limited(SimpleNamespace(send_message=send),chat_id=902,text='fixture')=='sent'
    assert len(calls)==2 and delays==[2.0]
