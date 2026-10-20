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
import threading
import tempfile
import math
import shlex
import shutil
import argparse
import asyncio
import hmac
import ssl
from datetime import datetime, timedelta
import logging

AGENT_VERSION = '6.1.0-rc.1'
AGENT_PROTOCOL = 2

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
_TEST_PORTS = set()
_TEST_PORTS_LOCK = threading.Lock()


def get_free_port():
    """Reserve a candidate within this Agent process until test cleanup.

    The kernel socket is unbound before Xray binds; this does not guarantee
    exclusion against unrelated processes. Bind errors remain visible.
    """
    with _TEST_PORTS_LOCK:
        for _ in range(30):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(('127.0.0.1', 0))
                value=sock.getsockname()[1]
            if value not in _TEST_PORTS:
                _TEST_PORTS.add(value)
                return value
    raise RuntimeError('XRAY_TEST_PORT_UNAVAILABLE')


def release_test_port(port):
    with _TEST_PORTS_LOCK:
        _TEST_PORTS.discard(port)


def _curl_failure(exitcode, status=None):
    if exitcode==6:return 'DNS_ERROR'
    if exitcode==5:return 'SOCKS_PROXY_DNS_ERROR'
    if exitcode==7:return 'TCP_ERROR'
    if exitcode in (35,60):return 'TLS_ERROR'
    if exitcode==28:return 'TIMEOUT'
    if exitcode!=0:return 'HTTP_CONNECTION_ERROR'
    return 'HTTP_UNEXPECTED' if status!='204' else None


def _health_urls():
    # Operator-configured endpoints; absence preserves the original default.
    raw=os.getenv('SONAR_AGENT_HEALTH_URLS','')
    if not raw:return (TEST_URL,)
    urls=[]
    for value in raw.split(',')[:4]:
        try:
            url=urllib.parse.urlsplit(value.strip())
            if url.scheme not in ('http','https') or not url.hostname or url.username or url.password:
                continue
            if url.port is not None and url.port not in (80,443):continue
            urls.append(value.strip())
        except ValueError:
            continue
    return tuple(urls) if urls else (TEST_URL,)

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
class SubscriptionFetchError(ValueError):
    """Safe error code; the subscription URL and credentials must never enter logs."""


SUB_ALLOWED_PORTS = frozenset((443, 8443, 2096, 2053, 2083, 2087))


def fetch_url(url, timeout=20, *, raise_errors=False):
    """Fetch a bounded HTTPS subscription over DNS-pinned PUBLIC IPv4/IPv6 only.

    Reject DNS rebind/private answers and redirects; try ALL validated resolved
    addresses, so a dead IPv6 route does not prevent working IPv4.
    """
    import http.client
    import ipaddress
    try:
        parsed = urllib.parse.urlsplit(str(url or ''))
        try: port = parsed.port or 443
        except ValueError: raise SubscriptionFetchError('INVALID_PORT') from None
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
            parsed.password or port not in SUB_ALLOWED_PORTS):
            raise SubscriptionFetchError('UNSUPPORTED_HTTPS_URL_OR_PORT')
        hostname = parsed.hostname.encode('idna').decode('ascii')
        results = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(item[4][0] for item in results))
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise SubscriptionFetchError('NON_PUBLIC_DNS_RESULT')
        ctx = ssl.create_default_context()
        path = urllib.parse.urlunsplit(('', '', parsed.path or '/', parsed.query, ''))
        failures = []
        for pinned_ip in addresses:
            class PinnedHTTPSConnection(http.client.HTTPSConnection):
                def connect(self):
                    raw = socket.create_connection((pinned_ip, port), self.timeout)
                    try: self.sock = self._context.wrap_socket(raw, server_hostname=hostname)
                    except BaseException:
                        raw.close()
                        raise
            conn = PinnedHTTPSConnection(hostname, port, timeout=timeout, context=ctx)
            try:
                host_header = hostname if port == 443 else f'{hostname}:{port}'
                conn.request('GET', path, headers={'Host':host_header, 'User-Agent':USER_AGENT,
                                                   'Accept':'*/*', 'Connection':'close'})
                response = conn.getresponse()
                if response.status != 200:
                    raise SubscriptionFetchError(f'HTTP_STATUS_{response.status}')
                raw = response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:
                    raise SubscriptionFetchError('RESPONSE_TOO_LARGE')
                if not raw.strip():
                    raise SubscriptionFetchError('EMPTY_RESPONSE')
                return raw
            except SubscriptionFetchError:
                raise
            except (OSError, ssl.SSLError, http.client.HTTPException) as exc:
                failures.append(type(exc).__name__)
            finally:
                conn.close()
        raise SubscriptionFetchError('CONNECTION_FAILED_' + (failures[-1] if failures else 'UNKNOWN'))
    except (OSError,ValueError,ssl.SSLError,http.client.HTTPException) as exc:
        code = str(exc) if isinstance(exc, SubscriptionFetchError) else type(exc).__name__
        logger.warning('Subscription fetch failed: %s',code)
        if raise_errors:raise SubscriptionFetchError(code) from None
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

def _clash_link(proxy):
    """Lossless subset of Clash/Sing-box -> Xray link; reject unsupported knobs."""
    if not isinstance(proxy,dict):return None
    kind=str(proxy.get('type','')).lower()
    host=str(proxy.get('server') or '').strip()
    name=str(proxy.get('name') or proxy.get('tag') or host or 'Proxy')[:180]
    try:port=int(proxy.get('port'))
    except (TypeError,ValueError):return None
    if not host or not 1<=port<=65535:return None
    netloc='['+host+']' if ':' in host and not host.startswith('[') else host
    net=str(proxy.get('network') or 'tcp').lower()
    if net not in ('tcp','ws','grpc'):return None
    if proxy.get('plugin') or proxy.get('plugin-opts') or proxy.get('h2-opts') or proxy.get('http-opts'):
        return None
    if proxy.get('skip-cert-verify'):
        # Never silently disregard requested certificate exceptions.
        return None
    qs={'type':net}
    ws_opts=proxy.get('ws-opts') or {}
    if net=='ws':
        if not isinstance(ws_opts,dict):return None
        qs['path']=str(ws_opts.get('path') or '/')
        headers=ws_opts.get('headers') or {}
        if not isinstance(headers,dict):return None
        if headers.get('Host'):qs['host']=str(headers['Host'])
    if net=='grpc':
        grpc=proxy.get('grpc-opts') or {}
        if not isinstance(grpc,dict):return None
        qs['serviceName']=str(grpc.get('grpc-service-name') or '')
    if proxy.get('servername') or proxy.get('sni'):
        qs['sni']=str(proxy.get('servername') or proxy.get('sni'))
    tls=bool(proxy.get('tls'))
    if proxy.get('client-fingerprint'):qs['fp']=str(proxy['client-fingerprint'])
    if proxy.get('alpn'):
        if not isinstance(proxy['alpn'],list):return None
        qs['alpn']=','.join(str(x) for x in proxy['alpn'])
    reality=proxy.get('reality-opts') or {}
    if reality and not isinstance(reality,dict):return None
    if reality.get('public-key'):
        tls=True
        qs.update(security='reality',pbk=str(reality['public-key']),sid=str(reality.get('short-id') or ''))
        if reality.get('spider-x'):qs['spx']=str(reality['spider-x'])
    else:qs['security']='tls' if tls else 'none'
    quote=urllib.parse.quote
    urlencode=urllib.parse.urlencode
    if kind=='vmess':
        if qs['security']=='reality':return None
        uuid=proxy.get('uuid')
        if not uuid:return None
        cfg={'v':'2','ps':name,'add':host,'port':str(port),'id':uuid,
             'aid':str(proxy.get('alterId',0)),'scy':str(proxy.get('cipher','auto')),
             'net':net,'tls':'tls' if tls else 'none','path':qs.get('path',qs.get('serviceName','')),
             'host':qs.get('host',''),'sni':qs.get('sni',''),'alpn':qs.get('alpn',''),'fp':qs.get('fp','')}
        return 'vmess://'+base64.urlsafe_b64encode(json.dumps(cfg).encode()).decode().rstrip('=')
    if kind=='vless':
        if not proxy.get('uuid'):return None
        if proxy.get('flow'):qs['flow']=str(proxy['flow'])
        return 'vless://'+quote(str(proxy['uuid']),safe='')+'@'+netloc+':'+str(port)+'?'+urlencode(qs)+'#'+quote(name)
    if kind=='trojan':
        if not proxy.get('password'):return None
        if qs['security']=='none':qs['security']='tls'
        return 'trojan://'+quote(str(proxy['password']),safe='')+'@'+netloc+':'+str(port)+'?'+urlencode(qs)+'#'+quote(name)
    if kind in ('ss','shadowsocks'):
        method,password=proxy.get('cipher'),proxy.get('password')
        if not method or not password or net!='tcp' or tls:return None
        user=base64.urlsafe_b64encode((str(method)+':'+str(password)).encode()).decode().rstrip('=')
        return 'ss://'+user+'@'+netloc+':'+str(port)+'#'+quote(name)
    return None


def _normalize_singbox(proxy):
    """Preserve nested sing-box TLS, Reality, uTLS, WS and gRPC settings."""
    if not isinstance(proxy,dict):return None
    kind=proxy.get('type')
    if kind not in ('vmess','vless','trojan','shadowsocks'):return None
    if proxy.get('multiplex') or proxy.get('detour') or proxy.get('packet_encoding'):
        return None
    result={'type':'ss' if kind=='shadowsocks' else kind,'name':proxy.get('tag','Proxy'),
            'server':proxy.get('server'),'port':proxy.get('server_port'),
            'uuid':proxy.get('uuid'),'password':proxy.get('password'),
            'cipher':proxy.get('method',proxy.get('security','auto')),'flow':proxy.get('flow')}
    transport=proxy.get('transport') or {}
    if not isinstance(transport,dict):return None
    t=transport.get('type','tcp')
    if t not in ('tcp','ws','grpc'):return None
    result['network']=t
    if t=='ws':
        result['ws-opts']={'path':transport.get('path') or '/',
                           'headers':transport.get('headers') or {}}
        if transport.get('max_early_data') or transport.get('early_data_header_name'):return None
    if t=='grpc':result['grpc-opts']={'grpc-service-name':transport.get('service_name') or ''}
    tls=proxy.get('tls') or {}
    if not isinstance(tls,dict):return None
    if tls.get('ech') or tls.get('certificate') or tls.get('certificate_path') or tls.get('disable_sni'):
        return None
    result['tls']=bool(tls.get('enabled'))
    if tls.get('insecure'):return None
    if tls.get('server_name'):result['servername']=tls['server_name']
    if tls.get('alpn'):result['alpn']=tls['alpn']
    utls=tls.get('utls') or {}
    if utls:
        if not isinstance(utls,dict):return None
        if utls.get('enabled') and utls.get('fingerprint'):
            result['client-fingerprint']=utls['fingerprint']
    reality=tls.get('reality') or {}
    if reality:
        if not isinstance(reality,dict):return None
        if reality.get('enabled'):
            if not reality.get('public_key'):return None
            result['reality-opts']={'public-key':reality['public_key'],
                                    'short-id':reality.get('short_id') or ''}
    return result


def _structured_sub_links(text_data):
    """Safely parse common JSON/sing-box and Clash YAML subscriptions."""
    text_data=text_data.strip()
    obj=None
    if text_data.startswith(('{','[')):
        try: obj=json.loads(text_data)
        except (TypeError,ValueError):pass
    elif ('proxies:' in text_data[:400] or text_data.startswith('mixed-port:')) and len(text_data)<=2*1024*1024:
        try:
            import yaml
            class NoAliasLoader(yaml.SafeLoader):
                # Subscription data is untrusted: reject potentially explosive YAML anchors.
                def compose_node(self, parent, index):
                    if self.check_event(yaml.AliasEvent):
                        raise ValueError('Subscription YAML aliases not allowed')
                    return super().compose_node(parent,index)
            obj=yaml.load(text_data,Loader=NoAliasLoader)
        except Exception as _exc:
            logger.debug('YAML parse not available: %s',type(_exc).__name__)
    if not obj:return []
    if isinstance(obj,list): proxies=obj
    elif isinstance(obj,dict): proxies=obj.get('proxies') or obj.get('outbounds') or []
    else:return []
    if not isinstance(proxies,list):return []
    out=[]
    for proxy in proxies[:601]:
        if not isinstance(proxy,dict):continue
        if 'server_port' in proxy:
            proxy=_normalize_singbox(proxy)
            if proxy is None:continue
        link=_clash_link(proxy)
        if link:out.append({'name':str(proxy.get('name') or proxy.get('tag') or 'Proxy')[:180], 'link':link})
    return out


def parse_subscription_links(text_data, limit=600):
    """Parse ordinary, base64-decoded and structured subscriptions; unique URLs."""
    results, seen = [], set()
    if not text_data: return results
    structured=_structured_sub_links(text_data)
    plain=[] if structured else [ln.strip() for ln in text_data.replace("\r", "\n").split("\n") if ln.strip()]
    for item in structured + [{'link':x,'name':None} for x in plain]:
        link=item['link']
        if not (link.startswith(('vmess://','vless://','trojan://','ss://')) or link.startswith('{')):continue
        if link in seen or len(link)>65536:continue
        seen.add(link)
        results.append({'name': item.get('name') or extract_name_from_link(link) or f'Item_{len(results)+1}', 'link':link})
        if limit and len(results)>=limit:break
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
    local_archive = os.getenv('SONAR_XRAY_LOCAL_ARCHIVE', '')
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
            if local_archive:
                local_path = Path(local_archive)
                if (not local_path.is_file() or local_path.is_symlink() or
                    not str(local_path).startswith('/var/lib/sonar-agent/')):
                    raise ValueError('Untrusted local Xray path')
                shutil.copyfile(local_path, archive_path)
                result = subprocess.CompletedProcess(['local-verified-archive'], 0)
            else:
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
                    "allowInsecure": False,
                    "alpn": [x for x in str(c.get("alpn", "")).split(",") if x],
                    "fingerprint": c.get("fp") or "chrome"
                }

            return {
                "protocol": "vmess",
                "settings": {
                    "vnext": [{
                        "address": c.get('add'),
                        "port": int(c.get('port') or 443),
                        "users": [{
                            "id": c.get('id'),
                            "alterId": int(c.get("aid") or 0),
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
                    "allowInsecure": q.get('allowInsecure',['false'])[0].lower() == 'true',
                    "fingerprint": q.get('fp',['chrome'])[0] or 'chrome',
                    "alpn": [x for x in q.get('alpn',[''])[0].split(',') if x]
                }
            elif sec == 'reality':
                stream['realitySettings'] = {
                    "publicKey": q.get('pbk', [''])[0] or "",
                    "serverName": q.get('sni', [p.hostname or ""])[0] or (p.hostname or ""),
                    "fingerprint": q.get('fp', ['chrome'])[0] or "chrome",
                    "shortId": q.get('sid', [''])[0] or "",
                    "spiderX": q.get('spx', ['/'])[0] or "/"
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

            password = urllib.parse.unquote(p.username or p.password or '')
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
                    "allowInsecure": q.get('allowInsecure',['false'])[0].lower()=='true',
                    "fingerprint": q.get('fp',['chrome'])[0],
                    "alpn": [x for x in q.get('alpn',[''])[0].split(',') if x]
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
            parsed=urllib.parse.urlsplit(link)
            if parsed.query:  # plugin= and other unsupported transports
                return None
            body=link[5:].split('#',1)[0]
            if '@' not in body:
                body=decode_base64(body)
            if '@' not in body:return None
            userinfo, hostport=body.rsplit('@',1)
            userinfo=urllib.parse.unquote(userinfo)
            if ':' not in userinfo:userinfo=decode_base64(userinfo)
            if ':' not in userinfo:return None
            method,password=userinfo.split(':',1)
            authority=urllib.parse.urlsplit('ss://'+hostport)
            host,port=authority.hostname,authority.port
            if not method or not password or not host or not port:return None
            return {'protocol':'shadowsocks','settings':{'servers':[
                {'address':host,'port':int(port),'method':method,'password':password}]},'tag':'proxy'}

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
    try:
        local_port = get_free_port()
    except (OSError,RuntimeError):
        return {"status":"Fail","msg":"XRAY_TEST_PORT_UNAVAILABLE",
                "error_code":"XRAY_TEST_PORT_UNAVAILABLE","ping":0,"jitter":0,
                "down":None,"up":None,"score":0}
    conf_file = os.path.join(WORK_DIR, f"config_{local_port}_{os.urandom(6).hex()}.json")
    full_conf = {
        "log": {"loglevel": "none"},
        "inbounds": [{"port": local_port, "listen": "127.0.0.1", "protocol": "socks", "settings": {"udp": True}}],
        "outbounds": [outbound, {"protocol": "freedom", "tag": "direct"}],
        "routing": {"domainStrategy": "IPOnDemand", "rules": [{"type": "field", "ip": ["geoip:private", "geoip:ir"], "outboundTag": "direct"}]}
    }

    proc = None
    try:
        fd=os.open(conf_file, os.O_CREAT|os.O_EXCL|os.O_WRONLY, 0o600)
        with os.fdopen(fd,'w') as f:
            json.dump(full_conf, f)

        proc = subprocess.Popen(_lowprio_prefix() + [XRAY_BIN, "-c", conf_file], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if not check_port_open(local_port):
            return {"status": "Fail", "msg": "Core Start Fail", "ping": 0, "jitter": 0, "down": None, "up": None, "score": 0}

        # socks5h delegates hostname resolution to Xray/remote egress.
        # socks5:// performed resolution locally, causing false failures.
        prox = f"socks5h://127.0.0.1:{local_port}"
        pings = []
        failure_codes=[]

        # Ping Test (3 tries)
        for _ in range(3):
            # Distinguish unreachable measurement targets from broken Xray.
            for target_url in _health_urls():
                try:
                    cmd = _lowprio_prefix() + ["curl", "--proxy", prox, "--silent", "--show-error",
                                                "--output", "/dev/null", "--write-out", "%{http_code} %{time_total}",
                                                "--max-time", "4", target_url]
                    res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
                    parts = res.stdout.split()
                    if res.returncode == 0 and len(parts) == 2 and parts[0] == "204":
                        pings.append(float(parts[1]) * 1000)
                        break
                    failure_codes.append(_curl_failure(res.returncode,parts[0] if parts else None))
                except subprocess.TimeoutExpired:
                    failure_codes.append('TIMEOUT')
                except (ValueError, OSError):
                    failure_codes.append('HTTP_CONNECTION_ERROR')

        if not pings:
            code=('XRAY_ERROR' if proc.poll() is not None else
                  next((c for c in failure_codes if c not in ('TIMEOUT','HTTP_UNEXPECTED')),None)
                  or (failure_codes[-1] if failure_codes else 'TIMEOUT'))
            return {"status": "Fail", "msg": code, "error_code": code,
                    "ping": 0, "jitter": 0, "down": None, "up": None, "score": 0}

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
        release_test_port(local_port)
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
            'agent_version':AGENT_VERSION,'agent_protocol':AGENT_PROTOCOL,
            'uptime_sec':uptime,'uptime_str':str(timedelta(seconds=uptime)),
            'capabilities':{'xray_ready':os.path.isfile(XRAY_BIN) and os.path.isfile(os.path.join(WORK_DIR,'geoip.dat')),
                            'formats':['plain','base64','clash_yaml','singbox_json'],
                            'proxies':['vmess','vless','trojan','shadowsocks']}}
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

        if not AGENT_TOKEN or not isinstance(token,str) or not token.isascii() or len(token)>256 or not hmac.compare_digest(token.encode('utf-8'), AGENT_TOKEN.encode('utf-8')):
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
                    raw=fetch_url(sub_link,timeout=25,raise_errors=True)
                    text=normalize_subscription_text(raw)
                    if not text:raise SubscriptionFetchError('EMPTY_SUBSCRIPTION')
                    results=parse_subscription_links(text,limit=601)
                    if not results:raise SubscriptionFetchError('NO_SUPPORTED_CONFIGS')
                    # Never authorize pruning if the source was truncated or contains
                    # unsupported records: partial parsing is not authoritative.
                    plain_source = not text.lstrip().startswith(('{','[','proxies:','mixed-port:'))
                    entries = [line.strip() for line in text.splitlines() if line.strip()]
                    supported = all(line.startswith(('vmess://','vless://','trojan://','ss://')) for line in entries)
                    complete = plain_source and supported and len(results) < 600
                    return results[:600],complete
                try:
                    configs,complete=await loop.run_in_executor(_get_test_executor(),_fetch)
                    await websocket.send(json.dumps({'links':configs,'sub_info':{
                        'total':len(configs),'complete':complete,
                        'fetched_at':datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                        'mode':'list'}}))
                except SubscriptionFetchError as exc:
                    await websocket.send(json.dumps({'error':str(exc)[:100]}))
                except Exception as exc:
                    logger.warning('fetch_sub failed: %s',type(exc).__name__)
                    await websocket.send(json.dumps({'error':'FETCH_INTERNAL_ERROR'}))

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

