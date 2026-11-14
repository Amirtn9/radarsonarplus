"""Bounded cross-user concurrency while serializing every user's conversations."""
import asyncio
import inspect
from telegram.ext import BaseUpdateProcessor


class OrderedUserProcessor(BaseUpdateProcessor):
    def __init__(self,max_concurrent_updates=16):
        super().__init__(max_concurrent_updates)
        self._users={}

    async def initialize(self):pass
    async def shutdown(self):pass

    async def do_process_update(self,update,coroutine):
        user=getattr(update,'effective_user',None)
        chat=getattr(update,'effective_chat',None)
        key=('user',user.id) if user else ('chat',chat.id) if chat else ('other',0)
        entry=self._users.setdefault(key,[asyncio.Lock(),0])
        entry[1]+=1;started=False
        try:
            async with entry[0]:
                started=True
                await coroutine
        finally:
            entry[1]-=1
            if not entry[1]:self._users.pop(key,None)
            if not started and inspect.iscoroutine(coroutine):coroutine.close()
