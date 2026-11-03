"""WebSocket pool timeout and shutdown behavior."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from ws_client import WebSocketPool, _Conn


class HangingSendSocket:
    def __init__(self):
        self.state = SimpleNamespace(name="OPEN")
        self.closed = False
        self.send_started = asyncio.Event()

    async def send(self, payload):
        self.send_started.set()
        await asyncio.Future()

    async def recv(self):
        return "{}"

    async def close(self):
        self.closed = True
        self.state = SimpleNamespace(name="CLOSED")


class IdleSocket:
    def __init__(self):
        self.state = SimpleNamespace(name="OPEN")
        self.closed = False

    async def close(self):
        self.closed = True
        self.state = SimpleNamespace(name="CLOSED")


@pytest.mark.asyncio
async def test_request_timeout_bounds_websocket_send_and_disposes_connection(monkeypatch):
    pool = WebSocketPool()
    ws = HangingSendSocket()
    key = ("203.0.113.10", 8443, "token", "cert")
    conn = _Conn(ws=ws, key=key, created_at=time.time(), last_used_at=time.time(), lock=asyncio.Lock())

    async def acquire(_key):
        return conn

    monkeypatch.setattr(pool, "_acquire", acquire)
    result = await pool.request("203.0.113.10", 8443, "token", {"action": "mutate"},
                                timeout=0.05, retries=0, tls_certificate="cert")
    assert result == {"error": "timeout"}
    assert ws.send_started.is_set()
    assert ws.closed


@pytest.mark.asyncio
async def test_close_all_closes_checked_out_websocket_connections():
    pool = WebSocketPool()
    ws = IdleSocket()
    key = ("203.0.113.11", 8443, "token", "cert")
    conn = _Conn(ws=ws, key=key, created_at=time.time(), last_used_at=time.time(), lock=asyncio.Lock())
    pool._all[id(conn)] = conn
    await pool.close_all()
    assert ws.closed
    assert not pool._all
