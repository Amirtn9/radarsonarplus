"""SSH host verification and explicit fingerprint enrollment. Never auto-trust."""
import base64
import fcntl
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import socket
import tempfile

import paramiko

from settings import KNOWN_HOSTS_FILE


class HostKeyRotationError(PermissionError):
    """Existing trusted key differs: refuse replacement without offline recovery."""


class SSHCommandValidationError(RuntimeError):
    """SSH authentication succeeded but the required remote command check failed."""


def normalize_host(value):
    value = str(value or '').strip()
    if value.startswith('[') and value.endswith(']'):
        value = value[1:-1]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        try:
            value = value.encode('idna').decode('ascii').lower()
        except UnicodeError:
            raise ValueError('Invalid server address') from None
        if not 1 <= len(value) <= 253 or any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
            for label in value.rstrip('.').split('.')
        ):
            raise ValueError('Invalid server address')
        return value.rstrip('.')


def ssh_port(value):
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError('SSH port must be between 1 and 65535')
    return port


def host_label(host, port):
    return host if int(port) == 22 else f'[{host}]:{int(port)}'


def fingerprint(key):
    return 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')


def describe_ssh_error(exc):
    if isinstance(exc, SudoRequiredError):
        return 'SUDO_REQUIRED: حساب SSH باید مجوز sudo غیرتعاملی داشته باشد؛ رمز sudo در اجرای خودکار درخواست نمی‌شود.'
    if isinstance(exc, SSHCommandValidationError):
        return 'SSH_COMMAND_FAILED: ورود SSH انجام شد، اما اجرای فرمان آزمایشی روی سرور موفق نبود.'
    if isinstance(exc, (paramiko.BadHostKeyException, HostKeyRotationError)):
        return 'HOST_KEY_CHANGED: کلید SSH سرور تغییر کرده است؛ اثرانگشت را از کنسول سرور بررسی کنید.'
    if isinstance(exc, paramiko.AuthenticationException):
        return 'AUTH_FAILED: نام کاربری، رمز یا روش ورود SSH پذیرفته نشد.'
    if isinstance(exc, paramiko.ssh_exception.NoValidConnectionsError):
        return 'SSH_PORT_UNREACHABLE: پورت SSH قابل اتصال نیست؛ پورت واقعی و فایروال را بررسی کنید.'
    if isinstance(exc, socket.gaierror):
        return 'SSH_DNS_FAILED: آدرس سرور قابل resolve نیست.'
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return 'SSH_TIMEOUT: اتصال SSH در زمان مجاز پاسخ نداد.'
    if isinstance(exc, ConnectionRefusedError):
        return 'SSH_CONNECTION_REFUSED: سرویس SSH روی پورت واردشده پاسخ نمی‌دهد.'
    if 'not found in known_hosts' in str(exc):
        return 'HOST_KEY_UNKNOWN: کلید SSH این سرور هنوز برای ربات تأیید نشده است.'
    if isinstance(exc, ValueError):
        return 'SSH_INPUT_INVALID: آدرس یا پورت SSH نامعتبر است.'
    return f'SSH_CONNECTION_FAILED: {type(exc).__name__}; ارتباط یا سازگاری سرویس SSH را بررسی کنید.'


def connect_ssh(host, port, username, password, relay_node=None):
    host, port = normalize_host(host), ssh_port(port)
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        for path in (Path(KNOWN_HOSTS_FILE), Path(__file__).parent / '.ssh' / 'known_hosts'):
            try:
                client.load_host_keys(str(path))
            except FileNotFoundError:
                pass
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        relay_sock = None
        try:
            if relay_node is not None:
                from ssh_relay import RelaySocket
                relay_sock = RelaySocket.connect(relay_node,host,port)
            client.connect(host, port=port, username=str(username).strip(), password=password,
                           sock=relay_sock, timeout=10, banner_timeout=15, auth_timeout=15,
                           allow_agent=False, look_for_keys=False)
        except BaseException:
            if relay_sock is not None: relay_sock.close()
            raise
        return client
    except BaseException:
        client.close()
        raise


def scan_host_key(host, port, relay_node=None):
    """Get a candidate key without sending credentials; caller must verify its SHA256."""
    host, port = normalize_host(host), ssh_port(port)
    if relay_node is not None:
        from ssh_relay import RelaySocket
        sock = RelaySocket.connect(relay_node,host,port)
    else:
        sock = socket.create_connection((host, port), timeout=10)
    transport = paramiko.Transport(sock)
    try:
        transport.start_client(timeout=15)
        key = transport.get_remote_server_key()
        return {'host': host, 'port': port, 'type': key.get_name(),
                'key': key.get_base64(), 'fingerprint': fingerprint(key)}
    finally:
        transport.close()
        sock.close()


def trust_host_key(candidate, expected):
    """Atomically save only an independently confirmed candidate key."""
    host, port = normalize_host(candidate['host']), ssh_port(candidate['port'])
    key = paramiko.PKey.from_type_string(candidate['type'], base64.b64decode(candidate['key'], validate=True))
    actual = fingerprint(key)
    if not __import__('hmac').compare_digest(actual, str(expected).strip()):
        raise ValueError('Fingerprint mismatch; host was not trusted')
    path = Path(KNOWN_HOSTS_FILE)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise PermissionError('Refusing symlinked SSH trust store')
    os.chmod(path.parent, 0o700)
    lock_fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        keys = paramiko.HostKeys()
        if path.exists():
            keys.load(str(path))
        label = host_label(host, port)
        existing = keys.lookup(label)
        if existing and not keys.check(label, key):
            raise HostKeyRotationError('Existing host key differs; verify rotation from the server console')
        keys.add(label, key.get_name(), key)
        fd, temp = tempfile.mkstemp(prefix='.known-hosts-', dir=path.parent)
        os.close(fd)
        try:
            keys.save(temp)
            os.chmod(temp, 0o600)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
    return actual


def authenticate_candidate(candidate, expected, username, password, relay_node=None):
    """Authenticate against the scanned key and verify command execution on the same transport.

    The key is still untrusted at this point. Only after the fingerprint matches and
    authentication plus a harmless command succeed may the caller persist it. Reusing
    this transport avoids a second, unnecessary SSH connection that can fail after a
    successful authentication due to transient routing or relay changes.
    """
    key = paramiko.PKey.from_type_string(
        candidate['type'], base64.b64decode(candidate['key'], validate=True))
    if not __import__('hmac').compare_digest(fingerprint(key), str(expected).strip()):
        raise ValueError('Fingerprint mismatch')
    client = paramiko.SSHClient()
    relay_sock = None
    try:
        client.get_host_keys().add(host_label(candidate['host'], candidate['port']), key.get_name(), key)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        if relay_node is not None:
            from ssh_relay import RelaySocket
            relay_sock = RelaySocket.connect(relay_node, candidate['host'], candidate['port'])
        client.connect(candidate['host'], port=ssh_port(candidate['port']),
            username=str(username).strip(), password=password, sock=relay_sock,
            timeout=10, banner_timeout=15, auth_timeout=15,
            allow_agent=False, look_for_keys=False)
        if str(username).strip() == 'root':
            probe_command = "/bin/bash -c 'printf SONAR_SSH_OK'"
        else:
            probe_command = "sudo -n -- /bin/bash -c 'printf SONAR_SSH_OK'"
        _stdin, stdout, stderr = client.exec_command(probe_command, timeout=12)
        stdout.channel.settimeout(15)
        output = stdout.read(128).decode('utf-8', 'replace')
        error = stderr.read(512).decode('utf-8', 'replace')
        exit_status = stdout.channel.recv_exit_status()
        low_error = error.lower()
        if exit_status != 0 and str(username).strip() != 'root' and (
                'sudo: a password is required' in low_error or
                'a terminal is required to read the password' in low_error or
                'is not in the sudoers' in low_error or 'not allowed to execute' in low_error):
            raise SudoRequiredError('Non-interactive sudo is required')
        if exit_status != 0 or 'SONAR_SSH_OK' not in output:
            # Keep only the error class/exit status in the raised exception; do not
            # propagate remote shell output that could contain secrets.
            raise SSHCommandValidationError(f'remote command exit={exit_status}; stderr={bool(error.strip())}')
        return {'authenticated': True, 'command_verified': True}
    finally:
        # Close Paramiko's transport before closing the underlying WebSocket relay.
        client.close()
        if relay_sock is not None:
            relay_sock.close()

