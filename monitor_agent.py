import sys
import os
import json
import time
import subprocess
import base64
import urllib.parse
import urllib.request
import zipfile
import re
import random
import socket
import math
import shlex
import shutil
import argparse
import asyncio
import hmac
import ssl
from datetime import datetime, timedelta
import logging

logger = logging.getLogger('sonar.agent')  # ✅ خودکار به سیستم متصل می‌شه

# ✅ جمع‌آوری لاگ‌های WS (بدون ارجاع به متغیرهای تعریف‌نشده در سطح ماژول)
try:
    logger.debug("WS module initialized (ip=%s, port=%s)", locals().get('ip'), locals().get('port'))
except Exception:
    pass
try:
    logger.debug("WebSocket error hook ready")
except Exception:
    pass
# تلاش برای ایمپورت کتابخانه‌های وب‌سوکت (اگر نصب نباشند، در حالت CLI ارور ندهد)
try:
    import websockets
    import psutil
except ImportError:
    websockets = None
    psutil = None

# ==============================================================================
# ⚙️ SYSTEM CONFIGURATION
# ==============================================================================
USER_HOME = os.path.expanduser("~")
WORK_DIR = os.environ.get("SONAR_AGENT_WORK_DIR", os.path.join(USER_HOME, "xray_workspace"))
XRAY_BIN = os.path.join(WORK_DIR, "xray")
LOG_FILE = os.path.join(USER_HOME, "agent_debug.log")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
TEST_URL = "http://www.google.com/generate_204"
RUNTIME_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_runtime.lock.json")

if not os.path.exists(WORK_DIR):
    try: os.makedirs(WORK_DIR, mode=0o755)
    except: pass

def advanced_log(message, category="INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{category}] {message}\n"
    level = logging.ERROR if category.upper() in ('ERROR', 'FAIL') else logging.INFO
    logger.log(level, '%s: %s', category, message)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f: f.write(entry)
    except: pass

# ==============================================================================
# 🛠 UTILITIES (Common)
# ==============================================================================
def get_free_port():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0)); return s.getsockname()[1]
    except: return random.randint(20000, 40000)

def check_port_open(port, timeout=5):
    start = time.time()
    while time.time() - start < timeout:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(('127.0.0.1', port)) == 0: return True
        time.sleep(0.2)
    return False

def decode_base64(data):
    try:
        data = data.strip().replace('\n', '').replace(' ', '')
        missing = len(data) % 4
        if missing: data += '=' * (4 - missing)
        return base64.b64decode(data.replace('-', '+').replace('_', '/')).decode('utf-8', errors='ignore')
    except: return data


# ==============================================================================
# 🔗 SUBSCRIPTION FETCH & PARSE (CLI MODE SUPPORT)
# ==============================================================================
def fetch_url(url, timeout=20):
    """HTTPS-only, DNS-pinned fetch; reject redirects and private destinations.

    DNS is resolved once; the verified address is the actual socket target.
    TLS still validates the requested DNS hostname, not its resolved address.
    """
    import http.client
    import ipaddress

    try:
        parsed = urllib.parse.urlsplit(str(url or ''))
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in (None, 443)):
            raise ValueError('only standard-port HTTPS URLs without credentials are allowed')
        hostname = parsed.hostname.encode('idna').decode('ascii')
        results = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        addresses = [row[4][0] for row in results]
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ValueError('subscription destination is not public')
        pinned_ip = addresses[0]

        class PinnedHTTPSConnection(http.client.HTTPSConnection):
            def connect(self):
                raw = socket.create_connection((pinned_ip, 443), self.timeout)
                try:
                    self.sock = self._context.wrap_socket(raw, server_hostname=hostname)
                except BaseException:
                    raw.close()
                    raise

        ctx = ssl.create_default_context()
        conn = PinnedHTTPSConnection(hostname, 443, timeout=timeout, context=ctx)
        try:
            path = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
            conn.request('GET', path, headers={'Host': hostname, 'User-Agent': USER_AGENT,
                                               'Accept': '*/*', 'Connection': 'close'})
            response = conn.getresponse()
            if response.status != 200:
                raise ValueError(f'upstream status {response.status} (redirects forbidden)')
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('subscription response exceeded 2 MiB')
            return raw
        finally:
            conn.close()
    except (OSError, ValueError, ssl.SSLError, http.client.HTTPException) as exc:
        logger.warning('Subscription fetch blocked/failed: %s', type(exc).__name__)
        return b''


def normalize_subscription_text(raw_bytes):
    """Return a readable text for v2ray/xray subscriptions (base64 or plain)."""
    try:
        raw_text = raw_bytes.decode("utf-8", errors="ignore").strip()
        if not raw_text:
            return ""
        # Many subs are base64 of multiple lines
        decoded = decode_base64(raw_text)
        if any(proto in decoded for proto in ("vmess://", "vless://", "trojan://", "ss://")):
            return decoded
        return raw_text
    except Exception:
        return ""

def extract_name_from_link(link):
    """Best-effort name extraction from different link formats."""
    try:
        link = (link or "").strip()
        if link.startswith("vmess://"):
            c = json.loads(decode_base64(link[8:]))
            return (c.get("ps") or c.get("remark") or c.get("name") or "").strip()
        # For URL based links, name usually is in fragment (#...)
        p = urllib.parse.urlparse(link)
        if p.fragment:
            return urllib.parse.unquote(p.fragment).replace("+", " ").strip()
        return ""
    except Exception:
        return ""

def parse_subscription_links(text_data, limit=0):
    """Parse subscription text and return list of {'name','link'}."""
    results = []
    seen = set()
    if not text_data:
        return results
    lines = [ln.strip() for ln in text_data.replace("\r", "\n").split("\n") if ln.strip()]
    for ln in lines:
        if not (ln.startswith(("vmess://", "vless://", "trojan://", "ss://")) or ln.startswith("{")):
            continue
        if ln in seen: continue
        seen.add(ln)
        name = extract_name_from_link(ln) or f"Item_{len(results)+1}"
        results.append({"name": name, "link": ln})
        if limit and len(results) >= limit:
            break
    return results

# ==============================================================================
# 📦 XRAY MANAGER
# ==============================================================================
def install_xray():
    """Install the checked-in, SHA256-pinned release and required geodata atomically."""
    import hashlib
    import platform
    from pathlib import Path
    import tempfile
    url = os.getenv('SONAR_XRAY_URL', '')
    expected = os.getenv('SONAR_XRAY_SHA256', '').lower()
    version = 'custom-pinned'
    if not url and not expected:
        try:
            lock = json.loads(Path(RUNTIME_LOCK_FILE).read_text())
            arch = {'amd64':'x86_64', 'arm64':'aarch64'}.get(platform.machine().lower(), platform.machine().lower())
            release = lock['assets'][arch]
            url, expected, version = release['url'], release['sha256'], lock['version']
        except (OSError, ValueError, KeyError):
            logger.error('Xray runtime lock missing or architecture unsupported')
            return False
    if not url or not re.fullmatch(r'[a-f0-9]{64}', expected):
        logger.warning('Xray installation requires a pinned URL and SHA-256')
        return False
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('github.com', 'objects.githubusercontent.com') or '/latest/' in url:
        logger.error('Refusing unpinned/untrusted Xray origin')
        return False
    def digest(path):
        with open(path,'rb') as stream:
            value=hashlib.sha256()
            for block in iter(lambda:stream.read(1024*1024),b''): value.update(block)
            return value.hexdigest()
    os.makedirs(WORK_DIR, mode=0o750, exist_ok=True)
    metadata = Path(WORK_DIR) / 'runtime-installed.json'
    try:
        saved=json.loads(metadata.read_text())
        if saved['archive_sha256']==expected and all(
            digest(Path(WORK_DIR)/name)==saved['files'][name]
            for name in ('xray','geoip.dat','geosite.dat')
        ) and os.access(XRAY_BIN, os.X_OK):
            return True
    except (OSError,ValueError,KeyError):
        pass
    try:
        with tempfile.TemporaryDirectory(prefix='.xray-install-',dir=WORK_DIR) as temp:
            archive_path = str(Path(temp)/'runtime.zip')
            result = subprocess.run(
                ['curl','--fail','--location','--show-error','--silent','--retry','2',
                 '--proto','=https','--proto-redir','=https','--connect-timeout','15',
                 '--max-time','180','--output',archive_path,url],
                timeout=200,capture_output=True,text=True,check=False)
            if result.returncode != 0:
                raise RuntimeError('Pinned runtime download failed')
            if not hmac.compare_digest(digest(archive_path),expected):
                raise ValueError('Xray SHA-256 mismatch')
            files={}
            with zipfile.ZipFile(archive_path) as archive:
                for name in ('xray','geoip.dat','geosite.dat'):
                    member=archive.getinfo(name)
                    if member.is_dir() or not 1024<=member.file_size<=150*1024*1024:
                        raise ValueError('Invalid Xray archive member')
                    target=Path(temp)/name
                    with archive.open(member) as source,target.open('wb') as dest:
                        shutil.copyfileobj(source,dest)
                    os.chmod(target,0o755 if name=='xray' else 0o644)
                    files[name]=digest(target)
            # Validate the actual core before making a staged release live.
            result=subprocess.run([str(Path(temp)/'xray'),'version'],capture_output=True,text=True,timeout=10)
            if result.returncode!=0:
                raise RuntimeError('Xray architecture/executable check failed')
            for name in files:
                os.replace(Path(temp)/name,Path(WORK_DIR)/name)
            staged=Path(temp)/'runtime-installed.json'
            staged.write_text(json.dumps({'version':version,'archive_sha256':expected,'files':files}))
            os.chmod(staged,0o600)
            os.replace(staged,metadata)
        logger.info('Pinned Xray and geodata installed: %s',version)
        return True
    except (OSError,ValueError,KeyError,RuntimeError,subprocess.SubprocessError,zipfile.BadZipFile) as exc:
        logger.error('Xray provisioning failed: %s',type(exc).__name__)
        return False


# ==============================================================================
# 🧩 PARSING & TESTING LOGIC
# ==============================================================================

def parse_xray_config(link):
    """Parse common v2ray/xray share links into a minimal Xray outbound."""
    try:
        link = (link or "").strip()
        if not link:
            return None

        # JSON outbound (already)
        if link.startswith('{'):
            return json.loads(link)

        # ----------------------------
        # VMESS
        # ----------------------------
        if link.startswith('vmess://'):
            c = json.loads(decode_base64(link[8:]))
            net = c.get('net', 'tcp')
            tls = c.get('tls', 'none')
            stream = {"network": net, "security": tls or "none"}

            if net == 'ws':
                stream["wsSettings"] = {
                    "path": c.get('path', '/') or '/',
                    "headers": {"Host": c.get('host', '') or ""}
                }
            elif net == 'grpc':
                stream["grpcSettings"] = {"serviceName": c.get('path', '') or ""}

            if tls == 'tls':
                stream["tlsSettings"] = {
                    "serverName": (c.get('sni') or c.get('host') or c.get('add') or ""),
                    "allowInsecure": True
                }

            return {
                "protocol": "vmess",
                "settings": {
                    "vnext": [{
                        "address": c.get('add'),
                        "port": int(c.get('port') or 443),
                        "users": [{
                            "id": c.get('id'),
                            "alterId": 0,
                            "security": c.get('scy', 'auto') or "auto"
                        }]
                    }]
                },
                "streamSettings": stream,
                "tag": "proxy"
            }

        # ----------------------------
        # VLESS
        # ----------------------------
        if link.startswith('vless://'):
            p = urllib.parse.urlparse(link)
            q = urllib.parse.parse_qs(p.query)

            net = q.get('type', ['tcp'])[0]
            sec = q.get('security', ['none'])[0]
            stream = {"network": net, "security": sec or "none"}

            if net == 'ws':
                stream['wsSettings'] = {
                    "path": q.get('path', ['/'])[0] or '/',
                    "headers": {"Host": q.get('host', [''])[0] or ""}
                }
            elif net == 'grpc':
                stream['grpcSettings'] = {"serviceName": q.get('serviceName', [''])[0] or ""}

            if sec == 'tls':
                stream['tlsSettings'] = {
                    "serverName": q.get('sni', [p.hostname or ""])[0] or (p.hostname or ""),
                    "allowInsecure": True
                }
            elif sec == 'reality':
                stream['realitySettings'] = {
                    "publicKey": q.get('pbk', [''])[0] or "",
                    "serverName": q.get('sni', [p.hostname or ""])[0] or (p.hostname or ""),
                    "fingerprint": q.get('fp', ['chrome'])[0] or "chrome"
                }

            return {
                "protocol": "vless",
                "settings": {
                    "vnext": [{
                        "address": p.hostname,
                        "port": int(p.port or 443),
                        "users": [{
                            "id": p.username,
                            "encryption": "none",
                            "flow": q.get('flow', [''])[0] or ""
                        }]
                    }]
                },
                "streamSettings": stream,
                "tag": "proxy"
            }

        # ----------------------------
        # TROJAN
        # ----------------------------
        if link.startswith('trojan://'):
            p = urllib.parse.urlparse(link)
            q = urllib.parse.parse_qs(p.query)

            password = p.username or p.password or ""
            net = q.get('type', ['tcp'])[0]
            sec = q.get('security', ['tls'])[0]  # trojan commonly uses tls
            stream = {"network": net, "security": sec or "tls"}

            if net == 'ws':
                stream['wsSettings'] = {
                    "path": q.get('path', ['/'])[0] or '/',
                    "headers": {"Host": q.get('host', [''])[0] or ""}
                }
            elif net == 'grpc':
                stream['grpcSettings'] = {"serviceName": q.get('serviceName', [''])[0] or ""}

            if sec == 'tls':
                stream['tlsSettings'] = {
                    "serverName": q.get('sni', [p.hostname or ""])[0] or (p.hostname or ""),
                    "allowInsecure": True
                }

            return {
                "protocol": "trojan",
                "settings": {
                    "servers": [{
                        "address": p.hostname,
                        "port": int(p.port or 443),
                        "password": password
                    }]
                },
                "streamSettings": stream,
                "tag": "proxy"
            }

        # ----------------------------
        # SHADOWSOCKS (basic)
        # ----------------------------
        if link.startswith('ss://'):
            body = link[5:]
            # strip fragment
            body, _, frag = body.partition('#')
            # strip query
            body, _, _q = body.partition('?')

            method = password = host = None
            port = None

            if '@' in body:
                userinfo, _, hostport = body.rpartition('@')
                if ':' in userinfo:
                    method, password = userinfo.split(':', 1)
                    method = urllib.parse.unquote(method)
                    password = urllib.parse.unquote(password)
                else:
                    dec = decode_base64(userinfo)
                    if ':' in dec:
                        method, password = dec.split(':', 1)
                if ':' in hostport:
                    host, port = hostport.split(':', 1)
            else:
                dec = decode_base64(body)
                if '@' in dec:
                    userinfo, hostport = dec.rsplit('@', 1)
                    if ':' in userinfo:
                        method, password = userinfo.split(':', 1)
                    if ':' in hostport:
                        host, port = hostport.split(':', 1)

            if not (method and password and host and port):
                return None

            return {
                "protocol": "shadowsocks",
                "settings": {
                    "servers": [{
                        "address": host,
                        "port": int(port),
                        "method": method,
                        "password": password
                    }]
                },
                "tag": "proxy"
            }

        return None
    except Exception as e:
        advanced_log(f"parse_xray_config error: {e}", "PARSE")
        return None
# 🔧 نسخه ۴.۳: تست‌ها با اولویت پایین اجرا می‌شوند.
# روی نود ایران، xray و curl نباید با پروسه‌های مهم (ربات، دیتابیس، sshd)
# سر CPU و I/O رقابت کنند. nice=+15 یعنی «هر وقت CPU بیکار بود».
def _lowprio_prefix():
    prefix = []
    if shutil.which("nice"):
        prefix += ["nice", "-n", "15"]
    if shutil.which("ionice"):
        prefix += ["ionice", "-c", "3"]
    return prefix


def _lowprio_shell(cmd: str) -> str:
    """همان کار، برای دستورهایی که با shell=True اجرا می‌شوند."""
    prefix = " ".join(_lowprio_prefix())
    return f"{prefix} {cmd}" if prefix else cmd


def test_config_logic(outbound, dl_size_mb=0.5):
    """لاجیک اصلی تست کانفیگ (Ping + optional Download Speed)."""
    if not os.path.isfile(XRAY_BIN) or not os.path.isfile(os.path.join(WORK_DIR,'geoip.dat')):
        return {"status":"Fail","msg":"XRAY_NOT_READY: install the pinned runtime and geodata","ping":0,"jitter":0,"down":None,"up":None}
    local_port = get_free_port()
    conf_file = os.path.join(WORK_DIR, f"config_{local_port}.json")
    full_conf = {
        "log": {"loglevel": "none"},
        "inbounds": [{"port": local_port, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": True}}],
        "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}],
        "routing": {"domainStrategy": "IPOnDemand", "rules": [{"type": "field", "ip": ["geoip:private", "geoip:ir"], "outboundTag": "direct"}]}
    }

    proc = None
    try:
        with open(conf_file, 'w') as f:
            json.dump(full_conf, f)

        proc = subprocess.Popen(_lowprio_prefix() + [XRAY_BIN, "-c", conf_file], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not check_port_open(local_port):
            return {"status": "Fail", "msg": "Core Start Fail", "ping": 0, "jitter": 0, "down": None, "up": None, "score": 0}

        prox = f"socks5://127.0.0.1:{local_port}"
        pings = []

        # Ping Test (3 tries)
        for _ in range(3):
            try:
                cmd = _lowprio_prefix() + ["curl", "--proxy", prox, "--silent", "--show-error",
                                            "--output", "/dev/null", "--write-out", "%{http_code} %{time_total}",
                                            "--max-time", "4", TEST_URL]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
                parts = res.stdout.split()
                if res.returncode == 0 and len(parts) == 2 and parts[0] == "204":
                    pings.append(float(parts[1]) * 1000)
            except Exception:
                pass

        if not pings:
            return {"status": "Fail", "msg": "Timeout", "ping": 0, "jitter": 0, "down": None, "up": None, "score": 0}

        avg_ping = int(sum(pings) / len(pings))
        jitter = int(max(pings) - min(pings)) if len(pings) >= 2 else 0

        # Download Speed Test (Optional)
        dl_spd = None
        if dl_size_mb and dl_size_mb > 0.5:
            try:
                url = f"https://speed.cloudflare.com/__down?bytes={int(dl_size_mb * 1024 * 1024)}"
                cmd = _lowprio_prefix() + ["curl", "--proxy", prox, "--fail", "--silent", "--show-error",
                                            "--write-out", "%{speed_download}", "--output", "/dev/null",
                                            "--max-time", "12", url]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)
                if res.returncode == 0:
                    dl_spd = round(float(res.stdout) * 8 / 1_000_000, 2)
            except Exception:
                dl_spd = None

        # Simple score
        score = 10
        if avg_ping > 1200:
            score = 1
        elif avg_ping > 800:
            score = 3
        elif avg_ping > 500:
            score = 5
        elif avg_ping > 300:
            score = 7
        elif avg_ping > 150:
            score = 9

        return {"status": "OK", "ping": avg_ping, "jitter": jitter, "down": dl_spd, "up": None, "speed_unit": "Mbps", "score": score}

    except Exception as e:
        return {"status": "Fail", "msg": str(e), "ping": 0, "jitter": 0, "down": None, "up": None, "score": 0}

    finally:
        try:
            if proc:
                proc.kill()
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if os.path.exists(conf_file):
                os.remove(conf_file)
        except Exception:
            pass

async def run_sys_command(command, timeout=60):
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            output = stdout.decode('utf-8', errors='ignore').strip()
            err_out = stderr.decode('utf-8', errors='ignore').strip()
            final_output = output if output else err_out
            return True, final_output
        except asyncio.TimeoutError:
            try: process.kill()
            except: pass
            return False, "Error: Command Timed Out"
    except Exception as e:
        return False, f"Agent Error: {str(e)}"
"""Sonar Monitor Agent (WebSocket server).

This agent is deployed on remote servers and provides a stable, long-lived
WebSocket interface for:
  - get_stats (CPU/RAM/Disk/Traffic/Uptime)
  - run_cmd (execute system commands with a timeout)
  - test_config (test a single v2ray/xray config link)

Stability improvements in this version:
  - WebSocket keepalive enabled (ping_interval/ping_timeout)
  - Persistent connections supported (multiple requests per connection)
  - Optional token validation (backward compatible)
  - Better JSON error handling
"""

# ==============================================================================
# 🌐 WEBSOCKET SERVER LOGIC
# ==============================================================================

async def get_stats():
    if psutil is None:
        return {'error':'psutil is not installed'}
    def snapshot():
        cpu=psutil.cpu_percent(interval=0.1)
        uptime=int(time.time()-psutil.boot_time())
        net=psutil.net_io_counters()
        return {'status':'Online','cpu':cpu,'ram':psutil.virtual_memory().percent,
            'disk':psutil.disk_usage('/').percent,
            'traffic_gb':round((net.bytes_sent+net.bytes_recv)/(1024**3),2),
            'uptime_sec':uptime,'uptime_str':str(timedelta(seconds=uptime)),
            'capabilities':{'xray_ready':os.path.isfile(XRAY_BIN) and os.path.isfile(os.path.join(WORK_DIR,'geoip.dat'))}}
    return await asyncio.get_running_loop().run_in_executor(_get_test_executor(),snapshot)


AGENT_TOKEN = os.environ.get("SONAR_AGENT_TOKEN", "")  # Required in server mode.

# 🔧 نسخه ۴.۲: کنترل بار روی خود ایجنت.
# حالا که ربات تست‌ها را موازی و روی وب‌سوکت می‌فرستد، ایجنت باید خودش
# سقف داشته باشد؛ وگرنه ده‌ها پروسه xray + curl همزمان بالا می‌آید و
# سرور ایران زانو می‌زند.
AGENT_MAX_PARALLEL_TESTS = int(os.getenv("SONAR_AGENT_MAX_TESTS", "3"))
_TEST_SEMAPHORE = None
_TEST_EXECUTOR = None


def _get_test_semaphore():
    global _TEST_SEMAPHORE
    if _TEST_SEMAPHORE is None:
        _TEST_SEMAPHORE = asyncio.Semaphore(AGENT_MAX_PARALLEL_TESTS)
    return _TEST_SEMAPHORE


def _get_test_executor():
    global _TEST_EXECUTOR
    if _TEST_EXECUTOR is None:
        from concurrent.futures import ThreadPoolExecutor
        _TEST_EXECUTOR = ThreadPoolExecutor(
            max_workers=AGENT_MAX_PARALLEL_TESTS + 4,
            thread_name_prefix="agent-test",
        )
    return _TEST_EXECUTOR


async def ws_handler(websocket, path=None):
    """Handle one connected client.

Protocol:
  1) client sends token (string)
  2) client sends JSON messages forever (request/response)
"""
    try:
        # 1) Authentication is mandatory; there is no legacy anonymous mode.
        try:
            token = await asyncio.wait_for(websocket.recv(), timeout=12)
        except asyncio.TimeoutError:
            return

        if not AGENT_TOKEN or not hmac.compare_digest(str(token), AGENT_TOKEN):
            try:
                await websocket.close(code=4001, reason="unauthorized")
            except Exception:
                pass
            return

        # 2) Handle requests
        async for message in websocket:
            try:
                data = json.loads(message)
            except Exception:
                try:
                    await websocket.send(json.dumps({"error": "invalid_json"}))
                except Exception:
                    pass
                continue

            if not isinstance(data,dict):
                await websocket.send(json.dumps({'error':'invalid_request_type'}))
                continue
            action = data.get('action')
            
            if action == 'get_stats':
                await websocket.send(json.dumps(await get_stats()))
                
            elif action == 'test_config':
                # اجرای تست کانفیگ داخل پروسه سرور (بدون باز کردن پایتون جدید)
                # این کار بسیار سریع‌تر از روش SSH است
                link = data.get('link')
                try:
                    size = max(0.0, min(float(data.get('size', 0.5)), 10.0))
                except (ValueError, TypeError):
                    await websocket.send(json.dumps({'error': 'invalid_test_size'}))
                    continue
                if not isinstance(link, str) or len(link) > 65536:
                    await websocket.send(json.dumps({'error': 'invalid_config_link'}))
                    continue
                outbound = parse_xray_config(link)
                if outbound:
                    # اجرای تست در ترد جداگانه تا وب‌سوکت قفل نشود
                    # (🔧 v4.2: executor اختصاصی + سقف همزمانی)
                    loop = asyncio.get_running_loop()
                    async with _get_test_semaphore():
                        res = await loop.run_in_executor(
                            _get_test_executor(), test_config_logic, outbound, size
                        )
                    # attach extracted name if possible
                    try:
                        res['extracted_name'] = extract_name_from_link(link) or ''
                    except Exception:
                        pass
                    await websocket.send(json.dumps(res))
                else:
                    await websocket.send(json.dumps({"status": "Fail", "msg": "Parse Error"}))
                    
            elif action == 'fetch_sub':
                # 🆕 نسخه ۴.۲: فقط لیست کانفیگ‌های داخل یک لینک اشتراک را برگردان.
                # تست کردنشان کار سمت ربات است (موازی، با صف عادلانه).
                sub_link = data.get('link')
                loop = asyncio.get_running_loop()

                def _fetch():
                    raw = fetch_url(sub_link, timeout=25)
                    text = normalize_subscription_text(raw)
                    return parse_subscription_links(text, limit=600)

                try:
                    configs = await loop.run_in_executor(_get_test_executor(), _fetch)
                    await websocket.send(json.dumps({
                        "links": configs,
                        "sub_info": {
                            "url": sub_link,
                            "total": len(configs),
                            "fetched_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                            "mode": "list",
                        },
                    }))
                except Exception as e:
                    await websocket.send(json.dumps({"error": "fetch_sub_failed"}))

            elif action == 'run_cmd':
                # Remote shell via WebSocket is deliberately disabled.
                # Maintenance commands must use SSH with server ownership checks.
                await websocket.send(json.dumps({"error": "remote_shell_disabled"}))
            else:
                # Unknown action
                try:
                    await websocket.send(json.dumps({"error": "unknown_action", "action": action}))
                except Exception:
                    pass

    except Exception:
        logger.exception("Agent client handler failed")

async def start_server(port):
    if websockets is None:
        raise RuntimeError('websockets not installed. Install with: pip3 install websockets psutil')
    # Keepalive is crucial for long-lived connections behind NAT/firewalls.
    tls_cert = os.environ.get('SONAR_AGENT_TLS_CERT', '')
    tls_key = os.environ.get('SONAR_AGENT_TLS_KEY', '')
    if bool(tls_cert) != bool(tls_key):
        raise RuntimeError('Both SONAR_AGENT_TLS_CERT and SONAR_AGENT_TLS_KEY required')
    ssl_ctx = None
    if tls_cert:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_ctx.load_cert_chain(tls_cert, tls_key)
    bind = os.environ.get("SONAR_AGENT_BIND", "127.0.0.1")
    if not ssl_ctx and bind not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError("Refusing cleartext agent listener on public interface; TLS required")
    async with websockets.serve(
        ws_handler,
        bind,
        port,
        ssl=ssl_ctx,
        max_size=min(max(1024, int(os.getenv("SONAR_AGENT_MAX_MESSAGE_SIZE", "4194304"))), 4194304),
        # 🔧 v4.3: قبلاً با ping_timeout=20 وقتی CPU نود اشباع می‌شد، ایجنت
        # نمی‌رسید به ping جواب بدهد و websockets *همه* کانکشن‌ها را می‌بست؛
        # بعد ربات دوباره وصل می‌شد و چرخه تکرار می‌شد.
        ping_interval=30,
        ping_timeout=90,
    ):
        await asyncio.Future()

# ==============================================================================
# 🏁 MAIN ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    if '--install-runtime' in sys.argv:
        raise SystemExit(0 if install_xray() else 1)
    # Provision during installation; at service start verify existing pinned assets.
    if not install_xray():
        logger.error('Xray is unavailable; get_stats will report xray_ready=false')

    # CLI args (backward compatible):
    #   python3 monitor_agent.py 8080
    #   python3 monitor_agent.py 8080 --token "..."
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("port", nargs="?", default="8080")
    parser.add_argument("--token", dest="token", default=None)
    args, _ = parser.parse_known_args()

    if args.token is not None:
        AGENT_TOKEN = str(args.token)
    if str(args.port).isdigit() and len(AGENT_TOKEN) < 32:
        parser.error("SONAR_AGENT_TOKEN (32+ chars) required for websocket server mode")

    # حالت 1: اجرا به عنوان سرور وب‌سوکت
    # (اگر اولین آرگومان عددی باشد => پورت)
    if str(args.port).isdigit():
        try:
            asyncio.run(start_server(int(args.port)))
        except KeyboardInterrupt:
            pass

    # حالت 2: اجرا به عنوان ابزار تست (CLI Mode - برای سازگاری با کدهای قدیمی)
    elif len(sys.argv) >= 2:
        link = sys.argv[1]
        size = float(sys.argv[2]) if len(sys.argv) > 2 else 0.5

        # اگر لینک سابسکریپشن بود
        if link.startswith(("http://", "https://")):
            raw = fetch_url(link, timeout=25)
            sub_text = normalize_subscription_text(raw)
            configs = parse_subscription_links(sub_text)

            sub_info = {
                "url": link,
                "total": len(configs),
                "fetched_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                "mode": "tested" if size and size > 0.5 else "list"
            }

            # همیشه یک meta چاپ می‌کنیم تا کدهای جدید/قدیمی سازگار باشند
            print(json.dumps({"type": "meta", "sub_info": sub_info}), flush=True)

            # حالت سریع (فقط لیست)
            if not size or size <= 0.5:
                print(json.dumps({"type": "sub", "configs": configs}), flush=True)
            else:
                # حالت تست کامل (خروجی استریم به صورت result)
                for item in configs:
                    c_link = item.get("link")
                    c_name = item.get("name", "Unknown")
                    outbound = parse_xray_config(c_link)
                    if outbound:
                        res = test_config_logic(outbound, size)
                        res.update({
                            "type": "result",
                            "name": c_name,
                            "link": c_link
                        })
                        if "extracted_name" not in res:
                            res["extracted_name"] = c_name
                        print(json.dumps(res), flush=True)
                    else:
                        print(json.dumps({
                            "type": "result",
                            "name": c_name,
                            "link": c_link,
                            "status": "Fail",
                            "msg": "Parse Error",
                            "ping": 0,
                            "jitter": 0,
                            "down": 0,
                            "up": 0,
                            "score": 0
                        }), flush=True)

        # اگر کانفیگ تکی بود
        else:
            outbound = parse_xray_config(link)
            if outbound:
                res = test_config_logic(outbound, size)
                try:
                    res["extracted_name"] = extract_name_from_link(link) or ""
                except Exception:
                    pass
                print(json.dumps(res), flush=True)
            else:
                print(json.dumps({"status": "Fail", "msg": "Parse Error"}), flush=True)

    # حالت پیش‌فرض: اجرای سرور روی پورت 8080
    else:
        try:
            asyncio.run(start_server(8080))
        except Exception:
            pass

