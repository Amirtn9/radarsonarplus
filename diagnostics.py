"""Sonar V6 full-window diagnostics with line-preserving secret redaction.

Exports complete logs for the requested recent window (default seven days),
including systemd journals, rotated application logs, installer logs, safe
operational state and optional SSH-verified remote Agent journals. Source logs
are never modified or deleted. Secret values are redacted without dropping the
surrounding log line. Remote commands use sudo -n and fail explicitly when the
SSH account is not authorized for administrative log access.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import gzip
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import zipfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
# Status fields are intentionally short; log bodies are not truncated.
STATUS_MAX_CHARS = 4_000
MAX_BUNDLE = int(os.getenv('SONAR_DIAGNOSTIC_MAX_BUNDLE', str(1024 * 1024 * 1024)))
DEFAULT_WINDOW_DAYS = 7
SERVICES = ('sonar-bot', 'sonar-web')
TOKEN = re.compile(r'\b[0-9]{7,12}:[A-Za-z0-9_-]{30,}\b')
SENSITIVE_KEY = re.compile(
    r'(?i)(?:[A-Za-z0-9]+[_-])*(?:password|passwd|passphrase|token|secret|authorization|credential|'
    r'api[_-]?key|private[_-]?key|cookie|session|client[_-]?secret|ssh[_-]?key)(?:[_-][A-Za-z0-9]+)*')
SENSITIVE_ASSIGNMENT = re.compile(
    r'''(?ix)(?P<prefix>["']?(?:[A-Za-z0-9]+[_-])*(?:password|passwd|passphrase|token|secret|authorization|credential|api[_-]?key|private[_-]?key|cookie|session|client[_-]?secret|ssh[_-]?key)(?:[_-][A-Za-z0-9]+)*["']?\s*[:=]\s*)'''
    r'''(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)''')
BEARER = re.compile(r'(?i)\b(Bearer|Basic|tma)\s+([^\s\\"\']+)')
URI = re.compile(r'(?i)\b(?:vless|vmess|trojan|ss|hysteria2|tuic)://[^\s<>"\']+')
HTTP_URL = re.compile(r'''(?i)\b(?:https?|postgres(?:ql)?|redis)://[^\s<>"']+''')
PRIVATE_BLOCK = re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----', re.I | re.S)
IPV4 = re.compile(r'(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])')
HOSTNAME = re.compile(r'(?i)\b(?:[a-z0-9-]+\.)+(?:com|net|org|ir|dev|info|io|co|online|app|site|xyz|me|ru|uk|de|fr|jp|cn|us)\b')
IPV6_CANDIDATE = re.compile(r'(?<![0-9A-Za-z_])(?:[0-9a-fA-F]{0,4}:){2,}[0-9a-fA-F:.%]*(?![0-9A-Za-z_])')
TIMESTAMP = re.compile(r'(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)')
SECRET_QUERY_KEYS = {'token', 'key', 'password', 'passwd', 'secret', 'auth', 'authorization', 'signature', 'sig', 'user', 'username', 'api_key', 'apikey', 'access_token', 'client_secret'}


def _redact_ipv6(match):
    import ipaddress
    candidate = match.group().rstrip(':,.;]')
    try:
        ipaddress.IPv6Address(candidate)
        return '[REDACTED_IPV6]' + match.group()[len(candidate):]
    except ValueError:
        return match.group()


def _redact_assignment(match):
    value = match.group('value')
    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
        value = value[0] + '[REDACTED]' + value[-1]
    else:
        value = '[REDACTED]'
    return match.group('prefix') + value


def _redact_url(match):
    raw = match.group(0)
    try:
        parts = urlsplit(raw)
        host = parts.hostname or ''
        if not host:
            return '[REDACTED_URL]'
        if ':' in host and not host.startswith('['):
            host = '[' + host + ']'
        netloc = host + (f':{parts.port}' if parts.port else '')
        if parts.username is not None or parts.password is not None:
            netloc = '[REDACTED_AUTH]@' + netloc
        if parts.scheme.lower() in ('postgres', 'postgresql', 'redis', 'rediss'):
            path = '/[REDACTED]'
            query = ''
        else:
            path = parts.path
            query_items = []
            for key, value in parse_qsl(parts.query, keep_blank_values=True):
                query_items.append((key, '[REDACTED]' if key.lower() in SECRET_QUERY_KEYS else value))
            query = urlencode(query_items, doseq=True)
        return urlunsplit((parts.scheme, netloc, path, query, ''))
    except (ValueError, UnicodeError):
        return '[REDACTED_URL]'


def sanitize(text: str, *, hide_ips: bool = True, hide_identifiers: bool = True) -> str:
    """Redact secret values while preserving all log lines and useful context."""
    value = str(text).replace('\x00', '')
    value = PRIVATE_BLOCK.sub('[REDACTED_PRIVATE_KEY]', value)
    value = SENSITIVE_ASSIGNMENT.sub(_redact_assignment, value)
    value = TOKEN.sub('[REDACTED_BOT_TOKEN]', value)
    value = BEARER.sub(lambda m: m.group(1) + ' [REDACTED_AUTH]', value)
    value = URI.sub('[REDACTED_CONFIG_LINK]', value)
    value = HTTP_URL.sub('[REDACTED_URL]' if hide_ips else _redact_url, value)
    if hide_identifiers:
        value = re.sub(r'(?i)\b(owner_id|user_id|chat_id|telegram_id|uid|peer_id)\s*[:=]\s*[0-9]+', r'\1=[REDACTED_ID]', value)
    if hide_ips:
        value = IPV4.sub('[REDACTED_IPV4]', value)
        value = IPV6_CANDIDATE.sub(_redact_ipv6, value)
        value = HOSTNAME.sub('[REDACTED_DOMAIN]', value)
    return value


def _command(args: list[str], timeout: int = 8, *, privileged: bool = False, limit: int | None = None) -> str:
    command = list(args)
    if privileged and os.geteuid() != 0:
        command = ['sudo', '-n', '--', *command]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, errors='replace',
                                   timeout=timeout,
                                   env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'},
                                   check=False)
        value = f'exit={completed.returncode}\n' + completed.stdout
        if completed.stderr:
            value += ('\n' if not value.endswith('\n') else '') + completed.stderr
        if limit is not None and len(value) > limit:
            return value[-limit:] + '\n[STATUS_OUTPUT_TRUNCATED]'
        return value
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f'Not available: {type(exc).__name__}'


def run(args: list[str], timeout: int = 8) -> str:
    """Compatibility wrapper used by health checks; concise status output only."""
    return _command(args, timeout, privileged=True, limit=STATUS_MAX_CHARS)


def service_status(name: str) -> str:
    alias = {'sonar-bot':'bot','sonar-web':'web','sonar-agent':'agent'}.get(name)
    helper = Path('/usr/local/sbin/sonar-diagnostic-read')
    if alias and helper.is_file():
        result = _command([str(helper),'service',alias], timeout=8, privileged=True, limit=STATUS_MAX_CHARS)
        if result.startswith('exit=0\n'):
            return result
    return run(['systemctl', 'show', name, '--no-pager',
                '--property=LoadState,ActiveState,SubState,Result,NRestarts,ExecMainStatus'])

def check_tcp(host: str, port: int, timeout: float = 1.5) -> str:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return 'listening'
    except OSError as exc:
        return type(exc).__name__


def snapshot() -> dict:
    version = (ROOT / 'VERSION').read_text(encoding='utf-8').strip() if (ROOT / 'VERSION').is_file() else 'unknown'
    config_path = ROOT / 'sonar_config.json'
    config = {}
    if config_path.is_file():
        try: config = json.loads(config_path.read_text(encoding='utf-8'))
        except (ValueError, OSError): pass
    def mode(path):
        try: return oct(path.stat().st_mode & 0o777)
        except OSError: return 'not found'
    web_port_raw = os.environ.get('SONAR_WEB_PORT', '8090')
    web_port = int(web_port_raw) if web_port_raw.isdigit() else 8090
    return {
        'schema': 'sonar-diagnostic-v6-full-window',
        'time_utc': datetime.now(timezone.utc).isoformat(),
        'project_version': version, 'python': platform.python_version(),
        'platform': platform.platform(), 'log_window_days_default': DEFAULT_WINDOW_DAYS,
        'config_exists': config_path.is_file(), 'config_mode': mode(config_path),
        'token_configured': bool(config.get('bot_token') or os.environ.get('SONAR_BOT_TOKEN')),
        'admin_configured': bool(config.get('admin_id')),
        'agent_port': config.get('agent_port', 8080),
        'web_port': web_port, 'web_local_tcp': check_tcp('127.0.0.1', web_port),
        'disk_free_mb': round(shutil.disk_usage(ROOT).free / 1048576),
        'services': {s: sanitize(service_status(s)) for s in SERVICES},
    }


def _read_file(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    def decode(data):
        return data.decode('utf-8', 'replace')
    try:
        if path.suffix == '.gz':
            with gzip.open(path, 'rb') as stream:
                return decode(stream.read())
        return path.read_text(encoding='utf-8', errors='replace')
    except PermissionError:
        # Prefer the root-owned allowlisted reader; never sudo arbitrary user paths.
        helper = Path('/usr/local/sbin/sonar-diagnostic-read')
        action = None
        if path == Path('/var/log/sonar-install.log'):
            action = ['install']
        elif path.parent == Path('/var/log/sonar'):
            action = ['app-file', path.name]
        if action and helper.is_file():
            try:
                command = [str(helper), *action]
                if os.geteuid() != 0:
                    command = ['sudo', '-n', '--', *command]
                result = subprocess.run(command, capture_output=True, timeout=120, check=False,
                                        env={'PATH':'/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
                if result.returncode == 0:
                    return decode(result.stdout)
            except (OSError, subprocess.SubprocessError):
                pass
        # Compatibility with older installs; this path is selected from a fixed allowlist.
        try:
            command = ['cat', '--', str(path)] if os.geteuid() == 0 else ['sudo', '-n', '--', 'cat', '--', str(path)]
            result = subprocess.run(command, capture_output=True, timeout=60, check=False,
                                     env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
            if result.returncode == 0:
                raw = result.stdout
                if path.suffix == '.gz':
                    import io
                    with gzip.GzipFile(fileobj=io.BytesIO(raw), mode='rb') as stream:
                        raw = stream.read()
                return decode(raw)
        except (OSError, subprocess.SubprocessError, gzip.BadGzipFile):
            pass
    except (OSError, gzip.BadGzipFile, EOFError):
        pass
    return None

def _parse_timestamp(line: str):
    match = TIMESTAMP.search(line[:300])
    if not match:
        return None
    raw = match.group(1).replace('Z', '+00:00')
    try:
        value = datetime.fromisoformat(raw)
        if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except ValueError:
        return None


def filter_log_window(text: str, cutoff: datetime) -> str:
    """Keep complete records in the time window, including multiline tracebacks."""
    lines = str(text).splitlines()
    if not lines: return ''
    output = []
    keep = True
    for line in lines:
        stamp = _parse_timestamp(line)
        if stamp is not None:
            keep = stamp >= cutoff
        if keep:
            output.append(line)
    return '\n'.join(output) + ('\n' if output else '')


def _journal(unit_args: list[str], days: int, *, timeout: int = 120, priority: str | None = None) -> str:
    helper = Path('/usr/local/sbin/sonar-diagnostic-read')
    if helper.is_file():
        if priority == 'err': service = 'errors'
        elif unit_args == ['-u','sonar-bot']: service = 'bot'
        elif unit_args == ['-u','sonar-web']: service = 'web'
        elif unit_args == ['-u','sonar-agent']: service = 'agent'
        else: service = 'both'
        result = _command([str(helper),'journal',service,str(days)], timeout, privileged=True)
        if result.startswith('exit=0\n'):
            return result
    args = ['journalctl', *unit_args, '--since', f'{days} days ago', '--no-pager', '--output=short-iso-precise']
    if priority:
        args += ['--priority', priority]
    return _command(args, timeout, privileged=True)

def _application_log_paths():
    log_dir = Path(os.getenv('SONAR_LOG_DIR', '/var/log/sonar'))
    fixed = []
    def mtime(path):
        try: return path.stat().st_mtime
        except OSError: return 0
    for prefix in ('sonar.log', 'sonar.json', 'sonar_errors.json', 'sonar_bot.log', 'sonar-web.log'):
        # Include current file and every rotation still present; no source file is modified.
        try: candidates=list(log_dir.glob(prefix + '*'))
        except OSError: candidates=[]
        fixed.extend(sorted(candidates, key=lambda path: (mtime(path), path.name)))
    # Installer log is intentionally separate from application logs.
    fixed.append(Path('/var/log/sonar-install.log'))
    result = []
    seen = set()
    for path in fixed:
        try:
            if path in seen or path.is_symlink() or not path.is_file(): continue
            seen.add(path); result.append(path)
        except OSError:
            continue
    return result


def _read_window_file(path: Path, cutoff: datetime) -> str | None:
    text = _read_file(path)
    if text is None: return None
    return filter_log_window(text, cutoff)


def log_source(name: str, days: int = DEFAULT_WINDOW_DAYS) -> str:
    days = max(1, min(30, int(days)))
    if name == 'bot':
        return _journal(['-u', 'sonar-bot'], days, timeout=120)
    if name == 'web':
        return _journal(['-u', 'sonar-web'], days, timeout=120)
    if name == 'errors':
        journal = _journal(['-u', 'sonar-bot', '-u', 'sonar-web'], days, timeout=120, priority='err')
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        app = []
        for path in _application_log_paths():
            if 'error' not in path.name.lower(): continue
            text = _read_window_file(path, cutoff)
            if text: app.append(f'===== {path} =====\n{text}')
        pieces = [x for x in [journal, *app] if x and x.strip()]
        return '\n'.join(pieces) if pieces else 'No ERROR/CRITICAL records found in the selected window.\n'
    if name == 'install':
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        path = Path('/var/log/sonar-install.log')
        return _read_window_file(path, cutoff) or 'Installer log unavailable; check sudo -n access to /var/log/sonar-install.log.\n'
    if name == 'structured':
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        parts = []
        for path in _application_log_paths():
            if not (path.name.startswith('sonar.json') or path.name.startswith('sonar_errors.json')): continue
            text = _read_window_file(path, cutoff)
            if text: parts.append(f'===== {path.name} =====\n{text}')
        return '\n'.join(parts) if parts else 'Structured application logs unavailable for the selected window.\n'
    return 'Unknown source\n'


def safe_agent_rows(rows):
    """Allowlist safe status fields; include every row without exporting credentials."""
    safe = []
    for row in list(rows or []):
        if not isinstance(row, dict):
            try: row = dict(row)
            except (TypeError, ValueError): continue
        item = {field: sanitize(str(row.get(field, '')), hide_ips=False, hide_identifiers=False)[:STATUS_MAX_CHARS]
                for field in ('last_status', 'agent_last_seen', 'agent_last_log', 'last_error')}
        # Useful operational identity, but never username/password/token/certificate.
        for field in ('id', 'name', 'ip', 'port', 'ws_port', 'location_type', 'is_active', 'last_agent_status', 'agent_id'):
            if field in row:
                value = row[field]
                item[field] = value.isoformat() if hasattr(value, 'isoformat') else value
        safe.append(item)
    return safe


def collect_operational_state(db, days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """Export allowlisted operational metadata only; never export credentials/config links."""
    days = max(1, min(30, int(days)))
    state = {'generated_at_utc': datetime.now(timezone.utc).isoformat(), 'window_days': days}
    queries = {
        'servers': """SELECT id,name,ip,port,ws_port,location_type,is_active,last_status,last_error,
                    agent_last_log,agent_last_seen,last_cpu,last_ram,last_disk,last_agent_status,
                    last_checked_at,agent_id,created_at FROM servers ORDER BY id""",
        'agents': """SELECT id,ip,ssh_port,ws_port,(COALESCE(agent_cert_pem,'')<>'') AS tls_enrolled,
                   (COALESCE(agent_token,'')<>'') AS token_configured FROM agent_nodes ORDER BY id""",
        'web_agent_jobs': """SELECT id,server_id,owner_id,requested_by,port,state,stage,error_code,created_at,updated_at
                         FROM web_agent_install_jobs WHERE created_at >= NOW()-(%s * INTERVAL '1 day') ORDER BY id""",
        'fleet_install_jobs': """SELECT id,server_id,requested_by,requested_port,port_mode,state,stage,error_code,created_at,updated_at
                           FROM monitor_fleet_install_jobs WHERE created_at >= NOW()-(%s * INTERVAL '1 day') ORDER BY id""",
        'fleet_nodes': """SELECT server_id,offered_by,approved_by,enabled,admin_only,relay_ready,latency_ms,cpu_pct,
                       last_probe_ok,last_probe_at,error_code,consecutive_failures,completed_tests,successful_tests
                       FROM monitor_fleet_nodes ORDER BY server_id""",
    }
    for key, query in queries.items():
        try:
            rows = db.execute(query, (days,), fetch='all') if key in ('web_agent_jobs', 'fleet_install_jobs') else db.execute(query, fetch='all')
            clean = []
            for row in rows or []:
                item = dict(row)
                for k, value in list(item.items()):
                    if hasattr(value, 'isoformat'): item[k] = value.isoformat()
                    elif isinstance(value, str): item[k] = sanitize(value, hide_ips=False, hide_identifiers=False)
                clean.append(item)
            state[key] = clean
        except Exception as exc:
            state[key] = {'unavailable': type(exc).__name__}
    return state


def remote_agent_logs(server_row, days: int = DEFAULT_WINDOW_DAYS):
    """Collect a complete recent Agent journal over direct SSH, then approved relays.

    Remote collection has per-command socket timeouts. If a target cannot be reached,
    a diagnostic status is recorded instead of failing the whole archive.
    """
    row = dict(server_row)
    days = max(1, min(30, int(days)))
    connection = None
    try:
        from core import ServerMonitor, sec
        from database import Database
        from settings import SUPER_ADMIN_ID
        from ssh_relay import eligible_relay_nodes
        db = Database()
        relays = eligible_relay_nodes(db, SUPER_ADMIN_ID, user_id=SUPER_ADMIN_ID, exclude_ip=str(row['ip']))
        try:
            from ssh_relay import stable_relay_order
            relays = stable_relay_order(relays, f"{row['ip']}:{int(row.get('port') or row.get('ssh_port') or 22)}", db=db)
        except Exception:
            relays = list(relays or [])
        # Diagnostic export is read-only; three approved relay candidates are enough
        # to keep the export bounded without silently trusting an unapproved node.
        relays = list(relays or [])[:3]
        connection = ServerMonitor.get_ssh_client(
            row['ip'], int(row.get('port') or row.get('ssh_port') or 22),
            row['username'], sec.decrypt(row['password']), relay_nodes=relays)
        sudo_prefix = '' if row.get('username') == 'root' else 'sudo -n '
        command = ('if [ -x /usr/local/sbin/sonar-diagnostic-read ]; then '
                   + sudo_prefix + '/usr/local/sbin/sonar-diagnostic-read service agent; '
                   'printf "\n===== SONAR AGENT JOURNAL =====\n"; '
                   + sudo_prefix + '/usr/local/sbin/sonar-diagnostic-read journal agent ' + str(days) +
                   '; else printf "DIAGNOSTIC_HELPER_NOT_INSTALLED\n"; '
                   + sudo_prefix + 'systemctl show sonar-agent.service --no-pager --property=LoadState,ActiveState,SubState,Result,NRestarts,ExecMainStatus; '
                   + sudo_prefix + 'journalctl -u sonar-agent --since ' + repr(f'{days} days ago') +
                   ' --no-pager --output=short-iso-precise; fi')
        _stdin, stdout, stderr = connection.exec_command(command, timeout=90)
        stdout.channel.settimeout(100)
        data = stdout.read().decode('utf-8', 'replace')
        errors = stderr.read().decode('utf-8', 'replace')
        exit_code = stdout.channel.recv_exit_status()
        if exit_code and not data.strip():
            data = f'REMOTE_LOG_ACCESS_FAILED exit={exit_code}: {errors.strip() or "sudo/journalctl unavailable"}'
        return sanitize(data + ('\n' + errors if errors else ''), hide_ips=False, hide_identifiers=False)
    except Exception as exc:
        try:
            from ssh_support import describe_ssh_error
            detail = describe_ssh_error(exc)
        except Exception:
            detail = type(exc).__name__
        return sanitize('REMOTE_AGENT_LOG_UNAVAILABLE: ' + detail, hide_ips=False, hide_identifiers=False)
    finally:
        if connection is not None:
            try: connection.close()
            except OSError: pass


def collect_agent_journals(server_rows, limit=None, days: int = DEFAULT_WINDOW_DAYS,
                           *, timeout_seconds: float = 420, max_workers: int = 16):
    """Collect remote journals with a global deadline and an entry for every server.

    A single unreachable VPS must not abort the complete diagnostics ZIP. Pending
    hosts receive an explicit timeout record; source logs are never shortened or
    removed. The worker pool is shut down without waiting for already timed-out
    network operations, so the caller can finish the archive deterministically.
    """
    servers = [dict(row) for row in list(server_rows or [])]
    if limit is not None:
        servers = servers[:max(0, min(int(limit), len(servers)))]
    result = {}
    if not servers:
        return result
    worker_count = max(1, min(int(max_workers), len(servers), 24))
    timeout_seconds = max(0.1, min(float(timeout_seconds), 900.0))
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix='sonar-diag-agent')
    futures = {}
    try:
        for index, server in enumerate(servers, 1):
            identifier = int(server.get('id') or index)
            futures[pool.submit(remote_agent_logs, server, days)] = identifier
        done, pending = concurrent.futures.wait(futures, timeout=timeout_seconds)
        for future in done:
            identifier = futures[future]
            try:
                result[str(identifier)] = future.result()
            except Exception as exc:
                result[str(identifier)] = 'REMOTE_AGENT_LOG_UNAVAILABLE: ' + type(exc).__name__
        for future in pending:
            identifier = futures[future]
            future.cancel()
            result[str(identifier)] = (
                f'REMOTE_AGENT_LOG_TIMEOUT: exceeded {timeout_seconds:g}s diagnostics collection budget; '
                'host was not deleted or modified; retry this host separately if full remote logs are required.\n')
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return dict(sorted(result.items(), key=lambda item: int(item[0]) if item[0].isdigit() else 0))

def build_bundle(output: str | Path | None = None, *, include_logs: bool = True,
                 agent_rows=None, remote_logs=None, operational_state=None,
                 days: int = DEFAULT_WINDOW_DAYS) -> Path:
    days = max(1, min(30, int(days)))
    if output is None:
        fd, name = tempfile.mkstemp(prefix='sonar-support-', suffix='.zip')
        os.close(fd); output = Path(name)
    else:
        output = Path(output)
    if output.is_symlink(): raise ValueError('Refusing symlink output')
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = snapshot()
    manifest['log_window_days'] = days
    manifest['log_collection'] = 'complete-window-no-line-count-or-per-file-truncation'
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
        z.writestr('README.txt',
            f'SONAR V6 FULL DIAGNOSTICS\nWindow: last {days} days (UTC-aware systemd journal filtering).\n'
            'Application and systemd logs are included without per-file tail/line truncation.\n'
            'Secret values are redacted in place; surrounding lines and stack traces are retained.\n'
            'Source logs are read-only and are never deleted or modified by this exporter.\n'
            'Remote Agent logs use SSH host-key verification, central-first connectivity, then approved healthy relay Agents.\n'
            'Some remote logs may state that sudo -n access is unavailable; no privilege escalation is bypassed.\n')
        z.writestr('health.json', sanitize(json.dumps(manifest, ensure_ascii=False, indent=2)))
        if agent_rows is not None:
            z.writestr('agents/status.json', json.dumps(safe_agent_rows(agent_rows), ensure_ascii=False, indent=2, default=str))
        if operational_state is not None:
            z.writestr('state/operational-state.json', sanitize(json.dumps(operational_state, ensure_ascii=False, indent=2, default=str), hide_ips=False, hide_identifiers=False))
        if remote_logs:
            for identifier, contents in sorted(remote_logs.items(), key=lambda item: str(item[0])):
                if str(identifier).isdigit():
                    z.writestr('agents/server-' + str(identifier) + f'-{days}d.log', sanitize(str(contents), hide_ips=False, hide_identifiers=False))
        if include_logs:
            for source in ('bot', 'web', 'errors', 'install', 'structured'):
                try:
                    source_text = log_source(source, days)
                except TypeError:  # compatibility for tests/extensions with a one-argument source provider
                    source_text = log_source(source)
                z.writestr('logs/' + source + '.log', sanitize(source_text, hide_ips=False, hide_identifiers=False))
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            for path in _application_log_paths():
                try:
                    if path.name == 'sonar-install.log': continue
                    # Skip only files whose modification time proves they cannot contain this window.
                    if path.stat().st_mtime < cutoff.timestamp() - 86400: continue
                    text = _read_window_file(path, cutoff)
                    if text is not None:
                        z.writestr('logs/application/' + path.name, sanitize(text, hide_ips=False, hide_identifiers=False))
                except OSError:
                    z.writestr('logs/application/' + path.name + '.unavailable.txt', 'Unable to read file; source was not modified.\n')
    os.chmod(output, 0o600)
    if output.stat().st_size > MAX_BUNDLE:
        # Only the temporary export is removed; source logs remain untouched.
        output.unlink(missing_ok=True)
        raise ValueError(f'BUNDLE_SIZE_LIMIT_EXCEEDED: complete {days}-day logs exceed configured {MAX_BUNDLE} byte limit; source logs were preserved')
    return output


def cli():
    parser = argparse.ArgumentParser(description='Sonar Radar V6 full-window diagnostics')
    parser.add_argument('--output', help='Absolute or relative ZIP output path')
    parser.add_argument('--no-logs', action='store_true')
    parser.add_argument('--health', action='store_true')
    parser.add_argument('--days', type=int, default=DEFAULT_WINDOW_DAYS, choices=range(1, 31), help='Complete log window in days (default: 7)')
    opts = parser.parse_args()
    if opts.health:
        print(sanitize(json.dumps(snapshot(), ensure_ascii=False, indent=2)))
    else:
        print(build_bundle(opts.output, include_logs=not opts.no_logs, days=opts.days))

if __name__ == '__main__': cli()
