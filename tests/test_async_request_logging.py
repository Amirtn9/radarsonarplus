"""A slow durable log sink must not monopolize the HTTP event loop."""
import asyncio
import logging
import threading
from types import SimpleNamespace

import pytest
from starlette.requests import Request
from starlette.responses import Response


@pytest.mark.asyncio
async def test_api_request_logging_keeps_event_loop_responsive(monkeypatch):
    import pro_portal
    started=threading.Event();released=threading.Event();finished=threading.Event()
    def slow_log(*args,**kwargs):
        started.set();released.wait(2);finished.set()
    monkeypatch.setattr(logging.getLogger('sonar.api'),'log',slow_log)
    request=Request({'type':'http','method':'POST','path':'/api/fixture','scheme':'https',
                     'headers':[],'query_string':b'','server':('localhost',443),
                     'route':SimpleNamespace(path='/api/fixture')})
    async def next_response(request):return Response(status_code=200)
    task=asyncio.create_task(pro_portal.headers(request,next_response))
    try:
        assert await asyncio.to_thread(started.wait,1)
        # If emit ran synchronously, its timeout elapsed before this coroutine
        # could resume. The durable write itself is still awaited before reply.
        assert not finished.is_set()
        assert not task.done()
    finally:
        released.set()
        response=await task
    assert response.headers['Cache-Control']=='no-store'
