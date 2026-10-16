"""Root/sudo enrollment, pinned runtime provisioning and a real remote WSS check."""
import asyncio
import ipaddress
import json
import os
from pathlib import Path
import shlex
import threading
import time
import secrets

from access_policy import valid_agent_port
from ssh_support import normalize_host, ssh_port

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def privileged_script(command):
    script = 'set -Eeuo pipefail\nexport DEBIAN_FRONTEND=noninteractive\n' + command
    args = '/bin/bash -Eeuo pipefail -c ' + shlex.quote(script)
    return (f'if [ "$(id -u)" = 0 ]; then exec {args}; '
            f'elif command -v sudo >/dev/null && sudo -n true 2>/dev/null; then exec sudo -n -- {args}; '
            'else echo "ADMIN_REQUIRED: SSH works; use root or passwordless sudo for agent installation" >&2; exit 77; fi')


def deploy_agent(monitor, db, ip, port, user, password, ws_port):
    ip, port, ws_port = normalize_host(ip), ssh_port(port), valid_agent_port(ws_port)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault((ip, port), threading.Lock())
    if not lock.acquire(blocking=False):
        return False, 'AGENT_INSTALL_BUSY: نصب دیگری روی همین سرور در حال اجراست.'
    try:
        return _deploy(monitor, db, ip, port, user, password, ws_port)
    except Exception as exc:
        # Commands and secrets must not appear in installer exceptions.
        return False, f'AGENT_INSTALL_FAILED: {type(exc).__name__}'
    finally:
        lock.release()


def _deploy(monitor, db, ip, port, user, password, ws_port):
    root = Path(__file__).resolve().parent
    script = (root / 'monitor_agent.py').read_text()
    runtime_lock = json.loads((root / 'agent_runtime.lock.json').read_text())
    token = db.get_or_create_agent_token(ip, port, ws_port)

    def run(command, timeout=120):
        return monitor._run_ssh_command(ip, port, user, password, privileged_script(command), timeout)

    ok, out = run('id -u', 25)
    if not ok:
        return False, out
    # Upload as small SFTP chunks using an independently verified SSH host key.
    # Never embed the entire agent source in a single bash -c argument.
    from ssh_support import connect_ssh
    ssh=None
    sftp=None
    remote_dir=None
    try:
        ssh=connect_ssh(ip,port,user,password)
        sftp=ssh.open_sftp()
        remote_dir=sftp.normalize('.')+'/.'+'sonar-upload-'+secrets.token_hex(8)
        sftp.mkdir(remote_dir,mode=0o700)
        for name,payload in (
            ('monitor_agent.py',script.encode('utf-8')),
            ('agent_runtime.lock.json',json.dumps(runtime_lock).encode('utf-8'))):
            with sftp.open(remote_dir+'/'+name,'wb') as target:
                target.write(payload)
                target.flush()
        destination=shlex.quote(remote_dir)
        upload=(f'install -d -m 0755 /opt/sonar-agent\n'
                f'install -m 0644 {destination}/monitor_agent.py /opt/sonar-agent/monitor_agent.py\n'
                f'install -m 0644 {destination}/agent_runtime.lock.json /opt/sonar-agent/agent_runtime.lock.json\n'
                'chown -R root:root /opt/sonar-agent\n')
        ok,out=run(upload,timeout=90)
        if not ok:return False,'AGENT_UPLOAD_INSTALL_FAILED: '+str(out)[-400:]
    except Exception as exc:
        return False,'AGENT_SFTP_FAILED: '+type(exc).__name__
    finally:
        if ssh:
            try:
                if remote_dir:
                    for name in ('monitor_agent.py','agent_runtime.lock.json'):
                        try:sftp.remove(remote_dir+'/'+name)
                        except OSError:pass
                    sftp.rmdir(remote_dir)
                if sftp is not None:
                    sftp.close()
            except (OSError, AttributeError):pass
            ssh.close()
    setup = '''apt-get update -y
apt-get install -y python3 python3-venv ca-certificates curl openssl iproute2
test -x /opt/sonar-agent-venv/bin/python || python3 -m venv /opt/sonar-agent-venv
/opt/sonar-agent-venv/bin/python -m pip install --disable-pip-version-check 'websockets==13.1' 'psutil==6.1.1' 'PyYAML==6.0.2'
chmod -R a+rX /opt/sonar-agent-venv
id -u sonar-agent >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/sonar-agent --shell /usr/sbin/nologin sonar-agent
install -d -m 0750 -o sonar-agent -g sonar-agent /var/lib/sonar-agent
'''
    ok, out = run(setup, 600)
    if not ok:
        return False, 'AGENT_DEPENDENCIES_FAILED: ' + out[-700:]
    try:
        ipaddress.ip_address(ip)
        san, check = f'IP:{ip}', '-checkip'
    except ValueError:
        san, check = f'DNS:{ip}', '-checkhost'
    cert_setup = f'''install -d -m 0750 -o root -g sonar-agent /etc/sonar-agent
if ! openssl x509 -in /etc/sonar-agent/tls.crt -noout -checkend 604800 >/dev/null 2>&1 || ! openssl x509 -in /etc/sonar-agent/tls.crt -noout {check} {shlex.quote(ip)} >/dev/null 2>&1 || [ ! -s /etc/sonar-agent/tls.key ]; then
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 -subj '/CN=sonar-agent' -addext {shlex.quote('subjectAltName='+san)} -keyout /etc/sonar-agent/tls.key -out /etc/sonar-agent/tls.crt >/dev/null 2>&1
fi
chown root:sonar-agent /etc/sonar-agent/tls.key /etc/sonar-agent/tls.crt
chmod 0640 /etc/sonar-agent/tls.key /etc/sonar-agent/tls.crt
'''
    ok, out = run(cert_setup, 90)
    if not ok:
        return False, 'AGENT_TLS_FAILED: ' + out[-700:]
    bind = '::' if ':' in ip else '0.0.0.0'
    managed = {'SONAR_AGENT_TOKEN': token, 'SONAR_AGENT_BIND': bind,
               'SONAR_AGENT_TLS_CERT': '/etc/sonar-agent/tls.crt',
               'SONAR_AGENT_TLS_KEY': '/etc/sonar-agent/tls.key',
               'SONAR_AGENT_WORK_DIR': '/var/lib/sonar-agent/xray_workspace'}
    for name in ('SONAR_XRAY_URL', 'SONAR_XRAY_SHA256'):
        if os.getenv(name):
            value = os.environ[name]
            if any(c in value for c in '\r\n\0'):
                return False, 'Invalid Xray deployment setting'
            managed[name] = value
    # Merge managed values; keep manually configured pinned Xray/environment values.
    env_script = '''import json,os,pathlib,tempfile
p=pathlib.Path('/etc/sonar-agent.env')
if p.is_symlink(): raise SystemExit('Refusing environment symlink')
updates=json.loads(REPLACED_VALUES)
lines=p.read_text().splitlines() if p.exists() else []
lines=[line for line in lines if line.partition('=')[0] not in updates]
lines += [k+'='+v for k,v in updates.items()]
fd,tmp=tempfile.mkstemp(prefix='.sonar-agent-env-',dir='/etc')
with os.fdopen(fd,'w') as f: f.write('\\n'.join(lines)+'\\n')
os.chmod(tmp,0o600);os.replace(tmp,p)
'''.replace('REPLACED_VALUES', repr(json.dumps(managed)))
    ok, out = run("python3 - <<'SONAR_ENV_PY'\n" + env_script + '\nSONAR_ENV_PY')
    if not ok:
        return False, 'AGENT_ENV_FAILED: ' + out[-700:]
    runtime_script = '''import os,pathlib,subprocess
env=os.environ.copy()
for line in pathlib.Path('/etc/sonar-agent.env').read_text().splitlines():
 k,sep,v=line.partition('=')
 if sep and (k.startswith('SONAR_XRAY_') or k=='SONAR_AGENT_WORK_DIR'): env[k]=v
subprocess.run(['runuser','-u','sonar-agent','--','/opt/sonar-agent-venv/bin/python','/opt/sonar-agent/monitor_agent.py','--install-runtime'],env=env,check=True,timeout=300)
'''
    ok, out = run("python3 - <<'SONAR_RUNTIME_PY'\n" + runtime_script + '\nSONAR_RUNTIME_PY', 360)
    if not ok:
        return False, 'XRAY_PROVISION_FAILED: ' + out[-700:]
    quota = os.getenv('SONAR_AGENT_CPU_QUOTA', '60%')
    if not __import__('re').fullmatch(r'[1-9][0-9]{0,3}%', quota):
        return False, 'Invalid agent CPU quota'
    unit = f'''[Unit]
Description=Sonar Monitor Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=8
[Service]
Type=simple
User=sonar-agent
Group=sonar-agent
WorkingDirectory=/var/lib/sonar-agent
EnvironmentFile=/etc/sonar-agent.env
ExecStart=/opt/sonar-agent-venv/bin/python -u /opt/sonar-agent/monitor_agent.py {ws_port}
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/sonar-agent
LimitNOFILE=4096
Nice=15
IOSchedulingClass=idle
CPUWeight=20
CPUQuota={quota}
Environment=SONAR_AGENT_MAX_TESTS=3
Restart=on-failure
RestartSec=3
[Install]
WantedBy=multi-user.target
'''
    command = ("cat > /etc/systemd/system/sonar-agent.service <<'SONAR_UNIT'\n" + unit + 'SONAR_UNIT\n'
               'chmod 0644 /etc/systemd/system/sonar-agent.service\n'
               'systemd-analyze verify /etc/systemd/system/sonar-agent.service\n'
               'systemctl daemon-reload\nsystemctl enable sonar-agent\nsystemctl restart sonar-agent\n'
               f"for n in $(seq 1 20); do if systemctl is-active --quiet sonar-agent && ss -H -lnt 'sport = :{ws_port}' | grep -q .; then exit 0; fi; sleep 0.5; done\n"
               'echo AGENT_LISTENER_FAILED >&2\nexit 6\n')
    ok, out = run(command, 45)
    if not ok:
        return False, 'AGENT_SERVICE_FAILED: ' + out[-700:]
    ok, cert = run('cat /etc/sonar-agent/tls.crt', 20)
    if not ok:
        return False, 'AGENT_CERTIFICATE_READ_FAILED'
    bot_ip = monitor.get_bot_public_ip()
    firewall = 'Bot public IP unavailable; check the agent firewall manually.'
    if bot_ip:
        allowed, firewall = monitor.whitelist_bot_ip(ip, port, user, password, bot_ip, ws_port)
        if not allowed:
            return False, 'AGENT_FIREWALL_FAILED: ' + firewall[-500:]
    from ws_client import WebSocketPool
    async def probe():
        pool = WebSocketPool(max_per_key=1, open_timeout=6, acquire_timeout=8)
        try:
            for attempt in range(3):
                result = await pool.request(ip, ws_port, token, {'action': 'get_stats'},
                                            timeout=10, retries=0, tls_certificate=cert.strip())
                if result.get('status') == 'Online' and result.get('capabilities', {}).get('xray_ready'):
                    return True
                if attempt < 2:
                    await asyncio.sleep(1)
            return False
        finally:
            await pool.close_all()
    if not asyncio.run(probe()):
        return False, f'WSS_HEALTH_FAILED: از میزبان ربات اتصال TLS/توکن/Xray روی {ws_port} تأیید نشد؛ فایروال و سرویس را بررسی کنید.'
    db.store_agent_certificate(ip, port, cert.strip(), ws_port)
    return True, f'ایجنت و Xray نصب شدند؛ اتصال WSS:{ws_port} با TLS و توکن آزموده شد.\n{firewall}'
