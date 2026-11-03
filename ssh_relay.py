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
import threading


def valid_relay_destination(host, port):
    """No DNS resolution or localhost/private/internal network access."""
    try:
        addr = ipaddress.ip_address(str(host))
        port = int(port)
    except (ValueError, TypeError):
        raise ValueError('RELAY_PUBLIC_IP_REQUIRED') from None
    ports = frozenset(int(x) for x in os.environ.get('SONAR_AGENT_RELAY_PORTS', '22,443,2022,2222,10022,22022').split(',') if x.strip().isdigit())
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
            try:
                frame = self.ws.recv(timeout=self.timeout)
            except Exception as exc:
                # A clean WebSocket close is end-of-stream, not a transport
                # programming error to be repeatedly logged by Paramiko.
                from websockets.exceptions import ConnectionClosed
                if isinstance(exc, ConnectionClosed):
                    self.close()
                    return b''
                raise
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
        try:
            self.ws.send(segment)
        except Exception as exc:
            from websockets.exceptions import ConnectionClosed
            if isinstance(exc, ConnectionClosed):
                self.close()
                raise OSError('RELAY_DISCONNECTED') from exc
            raise
        return len(segment)

    def sendall(self, data):
        view = memoryview(data)
        while view:
            count = self.send(view)
            view = view[count:]

    def settimeout(self, timeout):
        self.timeout = float(timeout) if timeout is not None else None

    def gettimeout(self): return self.timeout

    def setblocking(self, enabled):
        self.settimeout(None if enabled else 0)

    def getpeername(self): return self.target, self.port

    def _underlying_socket(self):
        # Paramiko versions differ in which optional socket methods they query.
        # websockets.sync exposes the underlying socket on supported releases.
        raw = getattr(self.ws, 'socket', None)
        if raw is None:
            transport = getattr(self.ws, 'transport', None)
            if transport is not None and hasattr(transport, 'get_extra_info'):
                raw = transport.get_extra_info('socket')
        if raw is not None and not hasattr(raw, 'fileno'):
            raw = getattr(raw, 'socket', None)
        return raw

    def recv_into(self, buffer, nbytes=0):
        """Implement the socket recv_into contract expected by socket adapters."""
        size = min(int(nbytes or len(buffer)), len(buffer))
        if size <= 0:
            return 0
        data = self.recv(size)
        count = min(len(data), len(buffer))
        buffer[:count] = data[:count]
        return count

    def getsockopt(self, *args):
        raw = self._underlying_socket()
        if raw is None:
            raise OSError('RELAY_SOCKET_OPTION_UNAVAILABLE')
        return raw.getsockopt(*args)

    def setsockopt(self, *args):
        raw = self._underlying_socket()
        if raw is None:
            raise OSError('RELAY_SOCKET_OPTION_UNAVAILABLE')
        return raw.setsockopt(*args)

    def getsockname(self):
        raw = self._underlying_socket()
        if raw is None:
            return ('0.0.0.0', 0)
        return raw.getsockname()

    def fileno(self):
        raw = self._underlying_socket()
        return raw.fileno() if raw is not None else -1

    def close(self):
        if not self.closed:
            self.closed = True
            try: self.ws.close()
            except Exception: pass

    def shutdown(self, how): self.close()


# Short-lived route affinity avoids unnecessary egress changes while retaining
# immediate failover when a route becomes unhealthy. It is intentionally local
# to the central controller; no target credentials are stored here.
_ROUTE_AFFINITY = {}
_ROUTE_LOCK = threading.Lock()
_ROUTE_AFFINITY_TTL = 1800


def stable_relay_order(nodes, target, ttl=_ROUTE_AFFINITY_TTL, db=None):
    pool = list(nodes or [])
    key = str(target or '')
    if not key or not pool:
        return pool
    node_id = None
    if db is not None:
        try:
            from monitor_fleet import get_relay_affinity
            node_id = get_relay_affinity(db,key,ttl)
        except Exception:
            node_id = None
    with _ROUTE_LOCK:
        saved = _ROUTE_AFFINITY.get(key)
        if node_id is None and saved:
            cached_id, stamp = saved
            if time.monotonic() - stamp <= max(1, int(ttl)):
                node_id = cached_id
            else:
                _ROUTE_AFFINITY.pop(key, None)
    if node_id is None:
        return pool
    for index, node in enumerate(pool):
        if str(node.get('id')) == str(node_id):
            return [pool[index], *pool[:index], *pool[index + 1:]]
    with _ROUTE_LOCK:
        _ROUTE_AFFINITY.pop(key, None)
    if db is not None and node_id is not None:
        try:
            from monitor_fleet import clear_relay_affinity
            clear_relay_affinity(db,key,node_id)
        except Exception:
            pass
    return pool


def remember_relay_success(target, node_id, db=None):
    if not target or node_id is None:
        return
    with _ROUTE_LOCK:
        _ROUTE_AFFINITY[str(target)] = (int(node_id), time.monotonic())
    if db is not None:
        try:
            from monitor_fleet import set_relay_affinity
            set_relay_affinity(db,str(target),int(node_id))
        except Exception:
            pass


def forget_relay_route(target, node_id=None, db=None):
    if not target:
        return
    with _ROUTE_LOCK:
        current = _ROUTE_AFFINITY.get(str(target))
        if current and (node_id is None or str(current[0]) == str(node_id)):
            _ROUTE_AFFINITY.pop(str(target), None)
    if db is not None:
        try:
            from monitor_fleet import clear_relay_affinity
            clear_relay_affinity(db,str(target),node_id)
        except Exception:
            pass



def retryable_relay_error(exc, code=None):
    """Retry a different Agent only for relay transport failures, never destination trust/auth failures."""
    text=(str(code or '')+' '+type(exc).__name__+' '+str(exc)).upper()
    if any(marker in text for marker in ('HOST_KEY_UNKNOWN','HOST_KEY_CHANGED','HOST_FINGERPRINT_MISMATCH','BADHOSTKEYEXCEPTION')):
        return False
    # These errors originate before the SSH handshake reaches the destination.
    if any(marker in text for marker in ('RELAY_','WEBSOCKET','CONNECTIONCLOSED','CONNECTIONRESET',
                                         'CONNECTIONREFUSED','NETWORKISUNREACHABLE','NO ROUTE TO HOST',
                                         'TIMED OUT','TIMEOUT','SSL','TLSV1_ALERT','CERTIFICATE VERIFY FAILED')):
        return True
    if any(marker in text for marker in ('AUTHENTICATION FAILED','AUTH_FAILED','AUTHENTICATIONEXCEPTION',
                                         'PERMISSION DENIED','BAD PASSWORD','INVALID USERNAME')):
        return False
    return retryable_direct_error(code or '')

def eligible_relay_nodes(db, admin_id, *, user_id=None, exclude_ip=None, limit=5):
    """Return healthy approved TLS-enrolled Agents across Iran and foreign regions.

    The direct controller always tries the destination first. This pool is only
    for network-level fallback and never bypasses host-key or SSH-auth checks.
    """
    if not admin_id:
        return []
    from monitor_fleet import score, policy
    try:
        from monitor_fleet import available_relay_nodes
        pool = available_relay_nodes(db, admin_id, user_id=user_id, exclude_ip=exclude_ip)
        strategy = policy(db)
    except (ImportError, AttributeError, TypeError, LookupError, RuntimeError):
        # Compatibility for isolated/older installs; this path retains the Iran-only pool.
        try:
            from monitor_fleet import available, score, policy
            pool = available(db, admin_id, int(user_id if user_id is not None else admin_id))
            strategy = policy(db)
        except (AttributeError, TypeError, LookupError, RuntimeError):
            return []
    effective_user=int(user_id if user_id is not None else admin_id)
    entries = [r for r in pool if (effective_user==int(admin_id) or not r.get('admin_only'))
               and r.get('enabled') and r.get('approved_by') is not None
               and r.get('ip') != exclude_ip and r.get('relay_ready') is True
               and r.get('tls_enrolled', True) is not False
               and score(r, strategy) != float('inf')]
    entries.sort(key=lambda r:(int(r.get('route_priority') or 100), score(r, strategy), int(r['id'])))
    return entries[:max(0, min(int(limit), 8))]

def retryable_direct_error(code):
    """NEVER bypass an invalid fingerprint or rejected credentials."""
    code = str(code or '').split(':',1)[0].strip()
    return code in {'SSH_TIMEOUT', 'SSH_PORT_UNREACHABLE', 'SSH_CONNECTION_REFUSED',
                    'SSH_CONNECTION_FAILED', 'SSH_DNS_FAILED'}
