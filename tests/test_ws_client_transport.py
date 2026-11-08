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


@pytest.mark.asyncio
async def test_real_tls_agent_concurrent_requests_and_reconnect(tmp_path):
    import ipaddress
    import json
    import ssl
    from datetime import datetime, timedelta, timezone
    import websockets
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'local-test-agent')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    (tmp_path/'cert.pem').write_bytes(pem)
    (tmp_path/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path/'cert.pem',tmp_path/'key.pem')
    connections = 0
    async def agent(ws):
        nonlocal connections
        connections += 1
        assert await ws.recv() == 'test-token'
        async for message in ws:
            request = json.loads(message)
            await ws.send(json.dumps({'status':'Online','request':request['number']}))
    pool = WebSocketPool(max_per_key=3)
    async with websockets.serve(agent,'127.0.0.1',0,ssl=context) as server:
        port = server.sockets[0].getsockname()[1]
        async def request(number):
            return await pool.request('127.0.0.1',port,'test-token',{'number':number},
                                      tls_certificate=pem.decode(),retries=0)
        results = await asyncio.gather(*(request(i) for i in range(12)))
        assert [r['request'] for r in results] == list(range(12))
        assert connections <= 3
        await pool.close_all()
        before = connections
        assert (await request(99))['request'] == 99
        assert connections == before+1
        await pool.close_all()


@pytest.mark.asyncio
async def test_shutdown_rejects_handshake_started_in_previous_generation(monkeypatch):
    pool = WebSocketPool(max_per_key=1)
    started, finish = asyncio.Event(), asyncio.Event()
    key = ('203.0.113.12', 8443, 'token', 'cert')
    ws = IdleSocket()

    async def connect(key):
        started.set()
        await finish.wait()
        return _Conn(ws, key, time.time(), time.time(), asyncio.Lock())

    monkeypatch.setattr(pool, '_connect', connect)
    pending = asyncio.create_task(pool._acquire(key))
    await started.wait()
    await pool.close_all()
    # A new generation can reserve capacity before the old handshake finishes.
    pool._counts[key] = 1
    finish.set()
    with pytest.raises(ConnectionError):
        await pending
    assert ws.closed
    assert pool._counts[key] == 1
    assert not pool._all
