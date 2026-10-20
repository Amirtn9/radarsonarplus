"""Pinned-TLS, token-authenticated WSS tunnel for an SSH session.

The SSH encryption and Paramiko host-key verification are END-TO-END. The
intermediate Iran Agent only transports binary SSH packets to a public IP.
Never use this for arbitrary client-supplied destinations: callers must first
validate saved servers and choose a healthy administrator-approved Agent.
"""
from __future__ import annotations
import ipaddress
import json
import os
import ssl
import time


def valid_relay_destination(host, port):
    """No DNS resolution or localhost/private/internal network access."""
    try:
        addr = ipaddress.ip_address(str(host))
        port = int(port)
    except (ValueError, TypeError):
        raise ValueError('RELAY_PUBLIC_IP_REQUIRED') from None
    ports = frozenset(int(x) for x in os.environ.get('SONAR_AGENT_RELAY_PORTS', '22,2022,2222').split(',') if x.strip().isdigit())
    if not addr.is_global or port not in ports or not 1 <= port <= 65535:
        raise ValueError('RELAY_DESTINATION_NOT_ALLOWED')
    return str(addr), port


class RelaySocket:
    """Socket-like synchronous WebSocket stream; compatible with Paramiko sock=."""

    def __init__(self, websocket, target, port):
        self.ws = websocket
        self.target = target
        self.port = int(port)
        self.buf = b''
        self.timeout = 15.0
        self.closed = False

    @classmethod
    def connect(cls, node, target, port, timeout=8):
        from database import Database
        from websockets.sync.client import connect
        target, port = valid_relay_destination(target, port)
        token, cert = Database().get_agent_transport(node['ip'], int(node['ws_port']))
        if not token or not cert:
            raise PermissionError('RELAY_AGENT_NOT_ENROLLED')
        ctx = ssl.create_default_context(cadata=cert)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        host = str(node['ip'])
        if ':' in host and not host.startswith('['): host = f'[{host}]'
        ws = connect(f'wss://{host}:{int(node["ws_port"])}', ssl=ctx,
                     open_timeout=float(timeout), close_timeout=3, max_size=65536)
        try:
            ws.send(token)
            ws.send(json.dumps({'action': 'ssh_relay_open', 'target': target, 'port': port}))
            reply = ws.recv(timeout=float(timeout))
            response = json.loads(reply)
            if response.get('status') != 'relay_ready':
                raise ConnectionError(str(response.get('error') or 'RELAY_NOT_READY'))
            return cls(ws, target, port)
        except BaseException:
            ws.close()
            raise

    def recv(self, n):
        if n <= 0: return b''
        while not self.buf:
            if self.closed: return b''
            frame = self.ws.recv(timeout=self.timeout)
            if not isinstance(frame, bytes):
                self.close()
                raise ConnectionError('RELAY_UNEXPECTED_CONTROL_FRAME')
            if not frame: return b''
            self.buf = frame
        data, self.buf = self.buf[:n], self.buf[n:]
        return data

    def send(self, data):
        if self.closed: raise OSError('RELAY_CLOSED')
        if not data: return 0
        # Explicit chunks cap intermediate memory and message size.
        segment = bytes(data[:65536])
        self.ws.send(segment)
        return len(segment)

    def sendall(self, data):
        view = memoryview(data)
        while view:
            count = self.send(view)
            view = view[count:]

    def settimeout(self, timeout):
        self.timeout = float(timeout) if timeout is not None else None

    def gettimeout(self): return self.timeout

    def getpeername(self): return self.target, self.port

    def close(self):
        if not self.closed:
            self.closed = True
            try: self.ws.close()
            except Exception: pass

    def shutdown(self, how): self.close()


def eligible_relay_nodes(db, admin_id, *, exclude_ip=None, limit=3):
    """Healthy WSS-enrolled, admin-approved Iran nodes, ordered by live score.

    No raw SSH credentials are selected or transmitted to intermediate nodes.
    """
    from monitor_fleet import available, score, policy
    if not admin_id: return []
    try:
        pool = available(db, admin_id, admin_id)
        strategy = policy(db)
    except (AttributeError, TypeError, LookupError, RuntimeError):
        # Existing installations may not have finished the additive fleet migration.
        # A missing pool must never stop an otherwise healthy direct SSH add.
        return []
    entries = [r for r in pool if r.get('enabled') and r.get('approved_by') is not None
               and r.get('ip') != exclude_ip and r.get('relay_ready') is True
               and score(r,strategy) != float('inf')]
    entries.sort(key=lambda r:(score(r,strategy),r['id']))
    return entries[:max(0,min(int(limit),5))]


def retryable_direct_error(code):
    """NEVER bypass an invalid fingerprint or rejected credentials."""
    code = str(code or '').split(':',1)[0].strip()
    return code in {'SSH_TIMEOUT', 'SSH_PORT_UNREACHABLE', 'SSH_CONNECTION_REFUSED',
                    'SSH_CONNECTION_FAILED', 'SSH_DNS_FAILED'}
