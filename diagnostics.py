"""Sonar v6 portable, bounded, privacy-first support bundles.

Never read env files, SSH keys, DB dumps, credentials or config contents.
The ZIP is useful for diagnosis but the operator must still inspect it before sharing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
MAX_BYTES = 190_000
MAX_LINES = 500
MAX_BUNDLE = 3_500_000
SERVICES = ('sonar-bot', 'sonar-web')
# Redact on each bounded line so accidentally logged secrets are not forwarded.
TOKEN = re.compile(r'\b\d{7,12}:[A-Za-z0-9_-]{30,}\b')
ASSIGNMENT = re.compile(r'(?i)\b(password|passwd|token|secret|authorization|api[_-]?key|private[_-]?key|session|cookie|db[_-]?password|bot[_-]?token)\b\s*([:=]\s*|["\']\s*:\s*["\'])([^\s,;"\']+)')
BEARER = re.compile(r'(?i)\b(?:Bearer|Basic|tma)\s+[A-Za-z0-9_./+=%-]{12,}')
URI = re.compile(r'(?i)\b(?:vless|vmess|trojan|ss|hysteria2|tuic)://[^\s<>"\']+')
URL_SECRET = re.compile(r'(?i)([?&](?:token|key|password|secret|auth|access_token)=)[^\s&#]+')
PEM = re.compile(r'-----BEGIN [\w ]*PRIVATE KEY-----.*', re.I)
HTTP_URL = re.compile(r'''(?i)\b(?:https?|postgres(?:ql)?|redis)://[^\s<>\"']+''')
IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')


def sanitize(text: str, *, hide_ips: bool = True) -> str:
    value = str(text).replace('\x00', '')
    value = TOKEN.sub('[REDACTED_BOT_TOKEN]', value)
    value = BEARER.sub('[REDACTED_AUTH]', value)
    value = URI.sub('[REDACTED_CONFIG_LINK]', value)
    value = HTTP_URL.sub('[REDACTED_URL]', value)
    value = URL_SECRET.sub(r'\1[REDACTED]', value)
    value = ASSIGNMENT.sub(lambda m: m.group(1) + m.group(2) + '[REDACTED]', value)
    value = PEM.sub('[REDACTED_PRIVATE_KEY]', value)
    if hide_ips:
        value = IPV4.sub(lambda m: '[IP-' + hashlib.sha256(m.group().encode()).hexdigest()[:8] + ']', value)
    return value


def run(args: list[str], timeout: int = 8) -> str:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, errors='replace',
                                   timeout=timeout, check=False, env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
        return f'exit={completed.returncode}\n' + (completed.stdout + '\n' + completed.stderr)[-MAX_BYTES:]
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f'Not available: {type(exc).__name__}'


def service_status(name: str) -> str:
    return run(['systemctl', 'show', name, '--no-pager',
                '--property=LoadState,ActiveState,SubState,Result,NRestarts,ExecMainStatus'])


def check_tcp(host: str, port: int, timeout: float = 1.5) -> str:
    # localhost-only; never send data or make requests to arbitrary user-provided hosts
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
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    def mode(path):
        try: return oct(path.stat().st_mode & 0o777)
        except OSError: return 'not found'
    return {
        'schema': 'sonar-diagnostic-v6',
        'time_utc': datetime.now(timezone.utc).isoformat(),
        'project_version': version, 'python': platform.python_version(),
        'platform': platform.platform(),
        'config_exists': config_path.is_file(), 'config_mode': mode(config_path),
        'token_configured': bool(config.get('bot_token') or os.environ.get('SONAR_BOT_TOKEN')),
        'admin_configured': bool(config.get('admin_id')),
        'agent_port': config.get('agent_port', 8080),
        'web_port': int(os.environ.get('SONAR_WEB_PORT', '8090')) if str(os.environ.get('SONAR_WEB_PORT', '8090')).isdigit() else 'invalid',
        'web_local_tcp': check_tcp('127.0.0.1', int(os.environ.get('SONAR_WEB_PORT', '8090'))) if os.environ.get('SONAR_WEB_PORT', '8090').isdigit() else 'invalid',
        'disk_free_mb': round(__import__('shutil').disk_usage(ROOT).free / 1048576),
        'services': {s: sanitize(service_status(s)) for s in SERVICES},
    }


def _tail_file(path: Path):
    if not path.is_file() or path.is_symlink():
        return None
    try:
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-MAX_BYTES))
            return stream.read(MAX_BYTES).decode('utf-8','replace')
    except OSError:
        return None


def log_source(name: str) -> str:
    # A nonprivileged bot user cannot normally read systemd's full journal.
    # Prefer the application's own rotated log files, which it can read safely.
    if name == 'bot':
        return _tail_file(Path('/var/log/sonar/sonar.log')) or run(['journalctl','-u','sonar-bot','--no-pager','-n',str(MAX_LINES),'--output=short-iso'], timeout=10)
    if name == 'structured':
        return _tail_file(Path('/var/log/sonar/sonar.json')) or 'JSON log unavailable.' 
    if name == 'web':
        return run(['journalctl','-u','sonar-web','--no-pager','-n',str(MAX_LINES),'--output=short-iso'], timeout=10)
    if name == 'errors':
        local = _tail_file(Path('/var/log/sonar/sonar.log'))
        if local:
            return '\n'.join(line for line in local.splitlines() if re.search(r'ERROR|CRITICAL|Traceback|Exception',line,re.I))[-MAX_BYTES:] or 'No recent error lines.'
        return run(['journalctl','-u','sonar-bot','-u','sonar-web','-p','err','--no-pager','-n',str(MAX_LINES)], timeout=10)
    if name == 'install':
        path=Path('/var/log/sonar-install.log')
        if not path.is_file() or path.is_symlink():return 'No installer log found.'
        try:
            with path.open('rb') as f:
                f.seek(max(0,path.stat().st_size-MAX_BYTES))
                return f.read(MAX_BYTES).decode('utf-8','replace')
        except OSError:return 'Installer log unavailable.'
    return 'Unknown source'


def safe_agent_rows(rows):
    """Allowlist agent status fields. Never serialize arbitrary database columns."""
    safe=[]
    for row in list(rows or [])[:150]:
        if not isinstance(row,dict):
            try: row=dict(row)
            except (TypeError,ValueError):continue
        safe.append({field:sanitize(str(row.get(field,'')))[:1200]
                     for field in ('id','last_status','agent_last_seen','agent_last_log','last_error')})
    return safe


def remote_agent_logs(server_row):
    """One bounded, read-only agent journal snapshot via verified SSH.

    Invoked only by authorized administrator workflows, never by an HTTP user URL.
    """
    row=dict(server_row)
    from core import sec
    from ssh_support import connect_ssh, describe_ssh_error
    connection=None
    try:
        connection=connect_ssh(row['ip'],int(row['port']),row['username'],sec.decrypt(row['password']))
        command='journalctl -u sonar-agent --no-pager -n 120 --output=short-iso'
        _,stdout,stderr=connection.exec_command(command,timeout=10)
        stdout.channel.settimeout(12)
        data=stdout.read(60_000).decode('utf-8','replace')
        errors=stderr.read(8_000).decode('utf-8','replace')
        return sanitize((data+'\n'+errors)[-MAX_BYTES:])
    except Exception as exc:
        return sanitize('Agent log unavailable: '+describe_ssh_error(exc))
    finally:
        if connection is not None:
            try: connection.close()
            except OSError:pass


def collect_agent_journals(server_rows,limit=5):
    """Collect at most a few agents so one broken VPS cannot stall the entire bot."""
    import concurrent.futures
    servers=[dict(row) for row in list(server_rows or [])[:max(0,min(int(limit),8))]]
    result={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(remote_agent_logs,srv): int(srv['id']) for srv in servers}
        for future in concurrent.futures.as_completed(futures):
            server_id=futures[future]
            try: result[str(server_id)]=future.result()
            except Exception as exc:result[str(server_id)]='Unavailable: '+type(exc).__name__
    return result


def build_bundle(output: str | Path | None = None, *, include_logs: bool = True,
                 agent_rows=None, remote_logs=None) -> Path:
    if output is None:
        fd, name = tempfile.mkstemp(prefix='sonar-support-', suffix='.zip')
        os.close(fd)
        output = Path(name)
    else:
        output = Path(output)
    if output.is_symlink(): raise ValueError('Refusing symlink output')
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest = snapshot()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=7) as z:
        z.writestr('README.txt', 'SONAR V6 DIAGNOSTICS - redacted, bounded support report.\n'
                   'Review all entries before forwarding. No credentials or DB dumps are intended to be included.\n'
                   'Tests on this host do not validate connectivity to remote agents.\n')
        z.writestr('health.json', sanitize(json.dumps(manifest, ensure_ascii=False, indent=2)))
        if agent_rows is not None:
            z.writestr('agents/status.json', json.dumps(safe_agent_rows(agent_rows),ensure_ascii=False,indent=2))
        if remote_logs:
            for identifier,contents in list(remote_logs.items())[:8]:
                if str(identifier).isdigit():
                    z.writestr('agents/agent-' + str(identifier) + '.log', sanitize(str(contents))[-MAX_BYTES:])
        if include_logs:
            for source in ('bot','web','errors','install','structured'):
                z.writestr('logs/' + source + '.log', sanitize(log_source(source))[-MAX_BYTES:])
    os.chmod(output, 0o600)
    if output.stat().st_size > MAX_BUNDLE:
        output.unlink(missing_ok=True)
        raise ValueError('Support bundle exceeded safe size limit')
    return output


def cli():
    parser=argparse.ArgumentParser(description='Sonar Radar V6 private diagnostics')
    parser.add_argument('--output', help='Absolute or relative ZIP output path')
    parser.add_argument('--no-logs', action='store_true')
    parser.add_argument('--health', action='store_true')
    opts=parser.parse_args()
    if opts.health:
        print(sanitize(json.dumps(snapshot(), ensure_ascii=False, indent=2)))
    else:
        print(build_bundle(opts.output, include_logs=not opts.no_logs))

if __name__ == '__main__': cli()
