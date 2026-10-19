"""Bounded HTTP(S) and TCP uptime probes for Sonar.

An address is resolved and validated immediately before use; the same resolved
address is used for the socket, so DNS rebinding cannot bypass the policy.
No redirects, proxies, response bodies, shell commands or credentials are used.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import os
import socket
import ssl
import time
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    latency_ms: float | None
    detail: str


def normalize_target(kind: str, target: str) -> str:
    kind = str(kind).lower().strip()
    target = str(target).strip()
    if kind == 'http':
        if len(target) > 1024 or not target:
            raise ValueError('HTTP URL must be 1-1024 characters')
        u = urlsplit(target)
        if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.fragment:
            raise ValueError('Use an http(s) URL without credentials or fragments')
        try:
            port = u.port or (443 if u.scheme == 'https' else 80)
        except ValueError as ex:
            raise ValueError('Invalid port') from ex
        host = u.hostname
        if any(ord(ch) < 33 for ch in host) or '%' in host or host.endswith('.'):
            raise ValueError('Invalid host')
        if not 1 <= port <= 65535:
            raise ValueError('Invalid port')
        return target
    if kind == 'tcp':
        if len(target) > 255 or any(x in target for x in ('/', '@', '?', '#', '\\')):
            raise ValueError('TCP target must be host:port')
        if target.startswith('['):
            host,sep,port_str = target[1:].partition(']:')
            if not sep: raise ValueError('Use [IPv6]:port')
        else:
            host,sep,port_str = target.rpartition(':')
            if not sep or ':' in host: raise ValueError('Use host:port')
        if not host or any(ch.isspace() for ch in host) or '%' in host:
            raise ValueError('Invalid host')
        try: port = int(port_str)
        except ValueError as ex: raise ValueError('Invalid port') from ex
        if not 1 <= port <= 65535: raise ValueError('Invalid port')
        return target
    raise ValueError('Type must be http or tcp')


def _resolve(host: str, port: int, allow_private: bool = False) -> list[tuple]:
    # The opt-in is operator-controlled. Keep disabled on multi-tenant installs.
    results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    public = []
    for family, socktype, proto, _canon, address in results:
        ip = ipaddress.ip_address(address[0])
        if allow_private or ip.is_global:
            public.append((family, socktype, proto, address))
    if not public:
        raise ValueError('Target does not resolve to an allowed public address')
    return public


def _connect(entries: list[tuple], timeout: float) -> socket.socket:
    last = None
    deadline = time.monotonic() + timeout
    for family, socktype, proto, address in entries[:6]:
        remaining = deadline - time.monotonic()
        if remaining <= 0: break
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(remaining)
            sock.connect(address)
            return sock
        except OSError as ex:
            last = ex
            sock.close()
    raise OSError('Connection failed') from last


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, entries, timeout):
        super().__init__(host, port, timeout=timeout)
        self._entries = entries

    def connect(self):
        self.sock = _connect(self._entries, self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, entries, timeout):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._entries = entries

    def connect(self):
        raw = _connect(self._entries, self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def check(kind: str, target: str, timeout: int = 5, expected_status: int | None = None,
          *, allow_private: bool | None = None) -> ProbeResult:
    """Probe one target. Failures are deliberately sanitized for UI/Telegram."""
    normalize_target(kind, target)
    if not 1 <= int(timeout) <= 15: raise ValueError('Timeout must be 1-15 seconds')
    if expected_status is not None and not 200 <= int(expected_status) <= 599:
        raise ValueError('Expected HTTP status must be 200-599')
    if allow_private is None:
        allow_private = os.getenv('SONAR_SERVICE_ALLOW_PRIVATE', '0') == '1'
    start = time.monotonic()
    try:
        if kind == 'http':
            url = urlsplit(target)
            host,port = url.hostname,url.port or (443 if url.scheme == 'https' else 80)
            entries = _resolve(host, port, allow_private)
            cls = _PinnedHTTPS if url.scheme == 'https' else _PinnedHTTP
            conn = cls(host, port, entries, timeout)
            try:
                path = url.path or '/'
                if url.query: path += '?' + url.query
                conn.request('GET', path, headers={'User-Agent': 'Sonar-Radar/6.3 uptime-check', 'Accept': '*/*', 'Connection': 'close'})
                response = conn.getresponse()
                # Do not follow redirects or read response bodies.
                status = response.status
            finally:
                conn.close()
            ok = status == expected_status if expected_status is not None else 200 <= status < 400
            return ProbeResult(ok, round((time.monotonic()-start)*1000, 2), f'HTTP {status}')
        target = normalize_target(kind,target)
        if target.startswith('['):
            host,port_str = target[1:].split(']:', 1)
        else:
            host,port_str = target.rsplit(':', 1)
        entries = _resolve(host, int(port_str), allow_private)
        with _connect(entries, timeout): pass
        return ProbeResult(True, round((time.monotonic()-start)*1000,2), 'TCP connected')
    except (OSError, ssl.SSLError, TimeoutError, ValueError, http.client.HTTPException) as ex:
        # Error messages can include raw URLs or credentials; do not forward them.
        if isinstance(ex, ValueError) and 'allowed public' in str(ex):
            return ProbeResult(False, None, 'Blocked by address policy')
        if isinstance(ex, socket.timeout) or isinstance(ex, TimeoutError):
            return ProbeResult(False, None, 'Connection timeout')
        if isinstance(ex, ssl.SSLCertVerificationError):
            return ProbeResult(False, None, 'TLS certificate verification failed')
        return ProbeResult(False, None, f'Probe error ({type(ex).__name__})')
