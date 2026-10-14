"""Behavioral offline regression checks for 5.2 transport, SSRF and logging boundaries."""
import asyncio
import hashlib
import logging
import os
import socket
import ssl
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_agent_wss_fails_closed_without_cert(monkeypatch):
    import ws_client
    pool = ws_client.WebSocketPool(max_per_key=1)
    async def check():
        result = await pool.request('127.0.0.1', 8080, 'safe-token', {'action': 'get_stats'}, retries=0)
        assert 'error' in result
        assert 'tls' in result['error'].lower() or 'cert' in result['error'].lower()
        await pool.close_all()
    asyncio.run(check())


def test_workspace_owner_ban_blocks_team_member():
    # Load the class without importing production Postgres dependencies.
    import ast
    import types
    tree = ast.parse((ROOT/'pro_store.py').read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ProStore')
    scope = {'Database': lambda: None}
    exec(compile(ast.Module(body=[cls], type_ignores=[]), 'pro_store.py', 'exec'), scope)
    ProStore = scope['ProStore']
    class FakeDb:
        def execute(self, *_args, **_kw): return {'role': 'viewer'}
        def check_access(self, who): return (who != 123, 'blocked')
    store = ProStore(FakeDb())
    with pytest.raises(PermissionError, match='inactive'):
        store.workspace(456, 123)


def test_logs_redacts_token_and_password():
    from logging_config import JSONFormatter, redact
    token = '123456789:' + 'a' * 35
    record = logging.LogRecord('sonar', logging.ERROR, __file__, 99,
                               'url %s password=secret SONAR_AGENT_TOKEN=abc', (token,), None)
    result = JSONFormatter().format(record)
    assert token not in result
    assert 'password=secret' not in result
    assert 'SONAR_AGENT_TOKEN=abc' not in result


def test_no_automatic_xray_without_independently_pinned_sha(monkeypatch, tmp_path):
    import monitor_agent
    monkeypatch.setattr(monitor_agent, 'XRAY_BIN', str(tmp_path / 'xray'))
    monkeypatch.setattr(monitor_agent, 'WORK_DIR', str(tmp_path))
    monkeypatch.delenv('SONAR_XRAY_URL', raising=False)
    monkeypatch.delenv('SONAR_XRAY_SHA256', raising=False)
    with patch.object(monitor_agent.subprocess, 'run', side_effect=AssertionError('Must not download')):
        assert monitor_agent.install_xray() is False


def test_no_dns_rebinding_in_https_client(monkeypatch):
    import monitor_agent
    target = 'subscription.example'
    monkeypatch.setattr(monitor_agent.socket, 'getaddrinfo',
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])
    destinations = []
    class StopAtSocket(Exception): pass
    def capture_socket(destination, timeout):
        destinations.append(destination)
        raise StopAtSocket
    monkeypatch.setattr(monitor_agent.socket, 'create_connection', capture_socket)
    with pytest.raises(StopAtSocket):
        monitor_agent.fetch_url(f'https://{target}/all', timeout=1)
    assert destinations == [('8.8.8.8', 443)]


def test_ssrf_blocks_private_dns_before_socket(monkeypatch):
    import monitor_agent
    monkeypatch.setattr(monitor_agent.socket, 'getaddrinfo',
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with patch.object(monitor_agent.socket, 'create_connection', side_effect=AssertionError('SSRF')):
        assert monitor_agent.fetch_url('https://example.invalid/admin') == b''


def test_per_agent_wss_real_tls_handshake(tmp_path):
    import websockets
    import ws_client
    if not __import__('shutil').which('openssl'):
        pytest.skip('openssl missing')
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                    '-keyout',str(key),'-out',str(cert),'-days','2',
                    '-subj','/CN=sonar-agent','-addext','subjectAltName=IP:127.0.0.1'],
                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=10)
    async def handler(websocket):
        token = await websocket.recv()
        assert token == 'local-test-secret'
        for message in [await websocket.recv()]:
            assert 'get_stats' in message
            await websocket.send('{"status":"OK"}')
    async def run():
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert),str(key))
        async with websockets.serve(handler, '127.0.0.1', 0, ssl=context) as srv:
            port=srv.sockets[0].getsockname()[1]
            pool=ws_client.WebSocketPool(max_per_key=1, open_timeout=2)
            try:
                ok=await pool.request('127.0.0.1',port,'local-test-secret',
                                      {'action':'get_stats'},tls_certificate=cert.read_text(),retries=0)
                assert ok == {'status':'OK'}, ok
            finally:
                await pool.close_all()
    asyncio.run(run())
