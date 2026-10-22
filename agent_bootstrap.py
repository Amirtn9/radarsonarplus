"""Direct-first, SSH-verified/offline Python dependency installer for Ubuntu agents.

Does not handle tokens, enrollment, TLS credentials, WebSocket or agent runtime settings.
Refuses a fallback package if target tags cannot be resolved into binary wheels.
"""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import platform
import socket
import re
import secrets
import shlex
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen, HTTPRedirectHandler, build_opener

REQUIREMENTS = ('websockets==13.1', 'psutil==6.1.1', 'PyYAML==6.0.2')
MIN_DISK_BYTES = 350 * 1024 * 1024
ALLOWED_ARCH = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'aarch64': 'aarch64', 'arm64': 'aarch64'}

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def _emit(progress, stage, details=''):
    if progress:
        try: progress(stage, details)
        except Exception: pass  # UI outages must not invalidate deployment.

def target_from_json(value):
    """Fail closed on cross-platform/cross-ABI package confusion."""
    arch = ALLOWED_ARCH.get(str(value.get('arch', '')).lower())
    py = value.get('python')
    if (value.get('system') != 'Linux' or value.get('pkg') != 'apt' or
        value.get('libc') != 'glibc' or arch is None or
        not isinstance(py, list) or len(py) < 2 or py[0] != 3 or
        not 9 <= py[1] <= 13):
        raise ValueError('UNSUPPORTED_TARGET: requires Debian/Ubuntu glibc Linux, Python 3.9–3.13, amd64/arm64')
    if int(value.get('free', 0)) < MIN_DISK_BYTES:
        raise ValueError('DISK_SPACE_LOW: at least 350 MiB free required')
    return {'arch':arch,'py':f'{py[0]}.{py[1]}', 'abi':f'cp{py[0]}{py[1]}',
            'platform':f'manylinux2014_{arch}', 'os_id': str(value.get('os_id', ''))}

# The preflight deliberately uses the existing Python interpreter; apt is handled separately.
PREFLIGHT = '''python3 - <<'SONAR_PROBE'
import json,os,platform,sys
osr={}
try:
 for line in open('/etc/os-release'):
  k,_,v=line.partition('=');osr[k]=v.strip().strip('"')
except OSError:pass
s=os.statvfs('/opt')
print('SONAR_PROBE='+json.dumps({'system':platform.system(),'arch':platform.machine(),
 'libc':platform.libc_ver()[0], 'python':list(sys.version_info[:2]),
 'pkg':'apt' if os.path.exists('/usr/bin/apt-get') else 'none',
 'free':s.f_bavail*s.f_frsize,'os_id':osr.get('ID','')}))
SONAR_PROBE'''

def probe(run, progress=None):
    _emit(progress, 'preflight', 'سیستم‌عامل، Python، معماری و دیسک')
    ok, out = run(PREFLIGHT, 35)
    if not ok: raise RuntimeError('PREFLIGHT_FAILED: Python 3 or SSH/sudo unavailable: ' + str(out)[-170:])
    try:
        raw = next(line.partition('=')[2] for line in str(out).splitlines() if line.startswith('SONAR_PROBE='))
        return target_from_json(json.loads(raw))
    except (StopIteration, ValueError, TypeError, KeyError) as exc:
        raise ValueError('PREFLIGHT_INVALID: unsupported or incomplete target environment') from exc

def _venv_preflight(run, timeout=35):
    """Create a throw-away venv and verify its pip; --help alone is insufficient."""
    script = r'''import pathlib,shutil,subprocess,sys,tempfile
base=pathlib.Path(tempfile.mkdtemp(prefix=".sonar-venv-check-",dir="/opt"))
try:
 target=base/"env"
 try:
  created=subprocess.run([sys.executable,"-m","venv",str(target)],capture_output=True,text=True,timeout=25)
 except Exception as exc:
  print("VENV_CREATE_PROBE_FAILED:"+type(exc).__name__);raise SystemExit(1)
 if created.returncode:
  print("VENV_CREATE_PROBE_FAILED:"+(created.stderr or created.stdout)[-500:]);raise SystemExit(1)
 try:
  pip=subprocess.run([str(target/"bin/python"),"-m","pip","--version"],capture_output=True,text=True,timeout=15)
 except Exception as exc:
  print("VENV_PIP_PROBE_FAILED:"+type(exc).__name__);raise SystemExit(1)
 if pip.returncode:
  print("VENV_PIP_PROBE_FAILED:"+(pip.stderr or pip.stdout)[-500:]);raise SystemExit(1)
 print("SONAR_VENV_READY")
finally:
 shutil.rmtree(base,ignore_errors=True)
'''
    command = "python3 - <<'SONAR_VENV_PREFLIGHT'\n" + script + "\nSONAR_VENV_PREFLIGHT\n"
    return run(command, timeout)


def _validate_public_apt_url(url):
    parts=urlsplit(str(url))
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password:
        raise RuntimeError('APT_BUNDLE_UNSAFE_URI')
    try:
        addresses={item[4][0].split('%',1)[0] for item in socket.getaddrinfo(parts.hostname,parts.port or (443 if parts.scheme=='https' else 80),type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
            raise RuntimeError('APT_BUNDLE_NONPUBLIC_MIRROR')
    except OSError as exc:
        raise RuntimeError('APT_BUNDLE_MIRROR_DNS_FAILED') from exc
    return parts


class _PublicAptRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_apt_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def _parse_apt_uris(output):
    """Parse apt's own URI plan. Only signed-index hashes and public HTTP(S) hosts are accepted."""
    records=[]
    pattern=re.compile(r"'(?P<url>https?://[^']+)'\s+(?P<name>[^\s]+)\s+(?P<size>\d+)\s+(?P<kind>MD5Sum|SHA256):(?P<digest>[A-Fa-f0-9]+)")
    for line in str(output).splitlines():
        match=pattern.search(line)
        if not match: continue
        url=match.group('url'); name=match.group('name')
        if Path(name).name!=name or not name.endswith('.deb') or '..' in name:
            raise RuntimeError('APT_BUNDLE_UNSAFE_FILENAME')
        _validate_public_apt_url(url)
        records.append({'url':url,'name':name,'size':int(match.group('size')),
                        'hash_kind':match.group('kind').lower(),'expected':match.group('digest').lower()})
    # apt can mention the same package URL more than once; keep one exact record.
    unique={}
    for item in records:
        previous=unique.get(item['name'])
        if previous and (previous['url']!=item['url'] or previous['expected']!=item['expected']):
            raise RuntimeError('APT_BUNDLE_CONFLICTING_PACKAGE_RECORD')
        unique[item['name']]=item
    return list(unique.values())


def _download_apt_bundle(run, host, port, username, password, packages, progress=None, connector=None):
    """Download exact target .deb URIs centrally, verify apt-index digest, then transfer via verified SSH/Agent."""
    if not all((host, username, password, connector)):
        return False, 'CENTRAL_APT_TRANSFER_UNAVAILABLE: SSH transport context missing'
    package_args=' '.join(shlex.quote(str(x)) for x in packages)
    plan_command='DEBIAN_FRONTEND=noninteractive apt-get --print-uris --yes --no-install-recommends install ' + package_args
    ok, output=run(plan_command,45)
    records=[]
    try: records=_parse_apt_uris(output) if ok else []
    except Exception as exc: return False, type(exc).__name__+': '+str(exc)
    if not records:
        # One metadata refresh attempt; package bytes still download on the central host.
        run('timeout 95s apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=12 -o Acquire::https::Timeout=12 update',110)
        ok, output=run(plan_command,45)
        try: records=_parse_apt_uris(output) if ok else []
        except Exception as exc: return False, type(exc).__name__+': '+str(exc)
    if not records:
        return False, 'APT_URI_PLAN_UNAVAILABLE: target apt metadata did not return verifiable public .deb URIs'
    total=sum(item['size'] for item in records)
    if total > 800*1024*1024:
        return False, 'APT_BUNDLE_TOO_LARGE: target prerequisite package set exceeds 800 MiB'
    _emit(progress,'central_os_packages',f'سرور مرکزی {len(records)} بسته سیستم را از مخزن APT تأییدشده دانلود می‌کند')
    with tempfile.TemporaryDirectory(prefix='sonar-apt-bundle-') as temp:
        folder=Path(temp); manifest=[]; downloaded=0
        for item in records:
            request=Request(item['url'],headers={'User-Agent':'Sonar-Radar-Pro-Agent-Bootstrap/6.6'})
            try:
                opener=build_opener(_PublicAptRedirectHandler())
                with opener.open(request,timeout=45) as response:
                    _validate_public_apt_url(response.geturl())
                    data=response.read(300*1024*1024+1)
            except Exception as exc:
                return False,'APT_PACKAGE_DOWNLOAD_FAILED: '+item['name']+' ('+type(exc).__name__+')'
            if len(data)>300*1024*1024:
                return False,'APT_PACKAGE_TOO_LARGE: '+item['name']
            digest=hashlib.md5(data).hexdigest() if item['hash_kind']=='md5sum' else hashlib.sha256(data).hexdigest()
            if digest.lower()!=item['expected']:
                return False,'APT_PACKAGE_HASH_MISMATCH: '+item['name']
            (folder/item['name']).write_bytes(data)
            manifest.append({'name':item['name'],'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)})
            downloaded+=len(data)
        (folder/'sonar-apt-manifest.json').write_text(json.dumps({'schema':'sonar-apt-bundle-v1','packages':manifest},indent=2),encoding='utf-8')
        _emit(progress,'central_os_transfer','ارسال بسته‌های سیستم از مسیر SSH مستقیم یا Agent تأییدشده')
        remote=_sftp_bundle(host,port,username,password,temp,connector=connector)
        remote_files=' '.join(shlex.quote(remote+'/'+item['name']) for item in manifest)
        command=('DEBIAN_FRONTEND=noninteractive apt-get -o Acquire::Retries=0 --no-download --no-install-recommends '
                 '-y install '+remote_files)
        ok,out=run(command,220)
        if not ok:
            run('rm -rf -- '+shlex.quote(remote),35)
            return False,'CENTRAL_APT_INSTALL_FAILED: '+str(out)[-260:]
        run('rm -rf -- '+shlex.quote(remote),35)
    ok,out=_venv_preflight(run,40)
    return (True,'CENTRAL_APT_BUNDLE_INSTALLED') if ok and 'SONAR_VENV_READY' in str(out) else (False,'CENTRAL_APT_BUNDLE_INSTALLED_BUT_VENV_PREFLIGHT_FAILED: '+str(out)[-220:])


def _ensure_os_tools(run, progress, host=None, port=22, username=None, password=None, connector=None):
    _emit(progress, 'system_packages', 'بررسی واقعی python3-venv، pip و ابزارهای سیستم با sudo')
    basic = ('command -v python3 >/dev/null && command -v openssl >/dev/null && '
             'command -v curl >/dev/null && command -v ss >/dev/null && '
             'test -s /etc/ssl/certs/ca-certificates.crt')
    ok, out = run(basic, 20)
    if not ok:
        _emit(progress, 'system_packages', 'نصب پیش‌نیازهای سیستم با دسترسی مدیر')
        update = ('export DEBIAN_FRONTEND=noninteractive; '
                  'timeout 95s apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=12 '
                  '-o Acquire::https::Timeout=12 update && '
                  'timeout 110s apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=12 '
                  '-o Acquire::https::Timeout=12 install -y python3-venv ca-certificates curl openssl iproute2')
        ok, out = run(update, 230)
        ok, out = run(basic, 20)
        if not ok:
            central_ok, central_out = _download_apt_bundle(run,host,port,username,password,
                ['python3-venv','ca-certificates','curl','openssl','iproute2'],progress,connector)
            if not central_ok:
                raise RuntimeError('SYSTEM_PACKAGES_UNAVAILABLE: target apt and central verified package transfer failed: '+str(central_out)[-240:])
            ok,out=run(basic,20)
            if not ok: raise RuntimeError('SYSTEM_PACKAGES_UNAVAILABLE_AFTER_CENTRAL_TRANSFER: '+str(out)[-200:])
    # --help alone is insufficient: actually create a temporary venv and verify pip.
    ok, out = _venv_preflight(run, 40)
    if ok and 'SONAR_VENV_READY' in str(out):
        return
    _emit(progress, 'system_packages', 'محیط مجازی ناقص است؛ نصب بسته نسخه دقیق Python با sudo')
    version_script = "python3 -c 'import sys; print(\"%s.%s\" % sys.version_info[:2])'"
    version_ok, version = run(version_script, 15)
    pyver = str(version).strip().splitlines()[-1] if version_ok and str(version).strip() else ''
    if not re.fullmatch(r'3\.[0-9]{1,2}', pyver):
        raise RuntimeError('VENV_PREREQUISITE_FAILED: python3-venv did not provide ensurepip and Python version could not be identified')
    exact_package = 'python' + pyver + '-venv'
    exact = ('export DEBIAN_FRONTEND=noninteractive; '
             '(timeout 100s apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=12 '
             '-o Acquire::https::Timeout=12 install -y python3-venv ' + shlex.quote(exact_package) +
             ' || (timeout 95s apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=12 '
             '-o Acquire::https::Timeout=12 update && timeout 100s apt-get -o Acquire::Retries=1 '
             '-o Acquire::http::Timeout=12 -o Acquire::https::Timeout=12 install -y python3-venv ' + shlex.quote(exact_package) + '))')
    exact_ok, exact_out = run(exact, 220)
    ok, out = _venv_preflight(run, 40)
    if not (ok and 'SONAR_VENV_READY' in str(out)):
        central_ok,central_out=_download_apt_bundle(run,host,port,username,password,
                    ['python3-venv',exact_package],progress,connector)
        ok,out=_venv_preflight(run,40) if central_ok else (False,central_out)
        if not (ok and 'SONAR_VENV_READY' in str(out)):
            detail = str(central_out if not central_ok else (exact_out if not exact_ok else out))[-260:]
            raise RuntimeError('VENV_PREREQUISITE_FAILED: target and central verified apt package install could not create a venv; run sudo apt-get install -y python3-venv ' + shlex.quote(exact_package) + '; ' + detail)

def make_wheelhouse(target, dest, timeout=170):
    """Download binary-only target wheels from main VPS; reject source builds.

    pip resolves recursive dependencies for target Python/ABI, fail closed on failure.
    """
    dest=Path(dest);dest.mkdir(parents=True,exist_ok=True)
    cmd=[os.environ.get('SONAR_BOOTSTRAP_PYTHON', 'python3'), '-m','pip','download',
         '--disable-pip-version-check','--only-binary=:all:',
         '--platform',target['platform'],'--python-version',target['py'],
         '--implementation','cp','--abi',target['abi'],
         '--timeout','15','--retries','2','--dest',str(dest),*REQUIREMENTS]
    try:
        p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                         text=True,timeout=timeout,check=False)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise RuntimeError('WHEEL_DOWNLOAD_UNAVAILABLE: '+type(exc).__name__) from exc
    if p.returncode != 0:raise RuntimeError('WHEEL_DOWNLOAD_FAILED: main VPS cannot resolve target wheels')
    wheels=sorted(dest.glob('*.whl'))
    if not wheels or any(not re.fullmatch(r'[A-Za-z0-9_.+\-]+\.whl', f.name) for f in wheels):
        raise RuntimeError('WHEEL_BUNDLE_INCOMPLETE: binary wheels unavailable')
    # Pin transitive deps and exact hashes; never permit pip to query the internet.
    requirements=[]
    for wheel in wheels:
        from_name=wheel.name.split('-')
        if len(from_name)<5:raise RuntimeError('WHEEL_FILENAME_INVALID')
        requirements.append(f'{from_name[0].replace("_", "-")}=={from_name[1]} --hash=sha256:{sha256(wheel.read_bytes())}')
    (dest/'requirements.lock').write_text('\n'.join(requirements)+'\n')
    manifest={'format':1,'target':target,'wheels':{f.name:sha256(f.read_bytes()) for f in wheels},
              'requirements_sha256':sha256((dest/'requirements.lock').read_bytes())}
    (dest/'manifest.json').write_text(json.dumps(manifest,sort_keys=True))
    return manifest

def cached_wheelhouse(target, dest):
    """Reuse verified main-server wheel bundles keyed by exact target Python/ABI.

    Cache is never inside the public source tree; records are never silently trusted.
    """
    import fcntl
    import shutil
    cache=Path(os.getenv('SONAR_AGENT_PACKAGE_CACHE_DIR',
        str(Path(os.getenv('SONAR_STATE_DIR',str(Path(__file__).parent/'runtime')))/'agent-wheel-cache')))
    cache.mkdir(parents=True,exist_ok=True,mode=0o700)
    if cache.is_symlink():raise RuntimeError('UNTRUSTED_WHEEL_CACHE')
    os.chmod(cache,0o700)
    cache_key=sha256(json.dumps({'target':target,'pins':REQUIREMENTS},sort_keys=True).encode())[:24]
    folder=cache/cache_key
    lock_fd=os.open(str(cache/'.cache.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(lock_fd,'w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        def verified(directory):
            try:
                m=json.loads((directory/'manifest.json').read_text())
                if m.get('target')!=target or not m.get('wheels'):return False
                if sha256((directory/'requirements.lock').read_bytes())!=m['requirements_sha256']:return False
                actual={x.name for x in directory.glob('*.whl')}
                if actual!=set(m['wheels']):return False
                return all(sha256((directory/name).read_bytes())==digest for name,digest in m['wheels'].items())
            except (OSError,KeyError,ValueError,TypeError):return False
        if not folder.is_dir() or not verified(folder):
            staging=Path(tempfile.mkdtemp(prefix='.wheels-',dir=cache))
            try:
                make_wheelhouse(target,staging)
                if not verified(staging):raise RuntimeError('WHEEL_CACHE_VERIFY_FAILED')
                if folder.is_dir():shutil.rmtree(folder)
                staging.rename(folder)
            finally:
                if staging.exists():shutil.rmtree(staging)
        dest=Path(dest)
        for file in folder.iterdir():
            if file.is_file():shutil.copy2(file,dest/file.name)
        return json.loads((folder/'manifest.json').read_text())


def _sftp_bundle(host, port, username, password, dest, path=None, connector=None):
    """Transfer to a private SSH folder; reuse intact files, retry damaged ones.

    Existing ssh_support enforces administrator-confirmed known_hosts fingerprints.
    """
    from ssh_support import connect_ssh
    ssh=(connector(host,port,username,password) if connector else
         connect_ssh(host,port,username,password))
    try:
        sftp=ssh.open_sftp()
        if path is None:
            reference=Path(dest)/'manifest.json'
            if not reference.is_file():reference=Path(dest)/'runtime.zip'
            if not reference.is_file():raise RuntimeError('Missing bundle identity')
            path=sftp.normalize('.')+'/.sonar-package-'+sha256(reference.read_bytes())[:16]
        try:
            try:sftp.mkdir(path, mode=0o700)
            except IOError:
                st=sftp.stat(path)
                import stat
                if stat.S_ISLNK(st.st_mode) or st.st_mode & 0o077:
                    raise PermissionError('Unsafe remote bundle directory')
            for f in sorted(Path(dest).iterdir()):
                if not f.is_file():continue
                remote=f'{path}/{f.name}'
                expected=f.stat().st_size
                expected_hash=sha256(f.read_bytes())
                for attempt in range(3):
                    try:
                        st=sftp.stat(remote)
                        if st.st_size==expected:
                            with sftp.open(remote,'rb') as existing:
                                hash_remote=hashlib.sha256()
                                for block in iter(lambda:existing.read(128*1024),b''):
                                    hash_remote.update(block)
                            if hash_remote.hexdigest()==expected_hash:break
                        sftp.remove(remote)
                    except IOError:pass
                    # Resume only when an already-uploaded prefix matches the source.
                    offset=0
                    try:
                        part_st=sftp.stat(remote+'.part')
                        if 0 < part_st.st_size < expected:
                            with f.open('rb') as local, sftp.open(remote+'.part','rb') as remote_part:
                                offset=int(part_st.st_size)
                                left=offset
                                while left:
                                    size=min(128*1024,left)
                                    if local.read(size)!=remote_part.read(size):
                                        offset=0;break
                                    left-=size
                        elif part_st.st_size==expected:
                            with sftp.open(remote+'.part','rb') as remote_part:
                                check=hashlib.sha256()
                                for block in iter(lambda:remote_part.read(128*1024),b''):check.update(block)
                            offset=expected if check.hexdigest()==expected_hash else 0
                    except IOError:offset=0
                    with f.open('rb') as src, sftp.open(remote+'.part','ab' if offset else 'wb') as dst:
                        src.seek(offset)
                        while True:
                            data=src.read(128*1024)
                            if not data:break
                            dst.write(data)
                        dst.flush()
                    sftp.rename(remote+'.part',remote)
                else:raise IOError('Incomplete or corrupt SFTP transfer')
        finally:sftp.close()
        return path
    finally:ssh.close()

# No user-controlled values are inserted in the embedded installer (all remote paths are fixed).
VERIFY_INSTALL='''import hashlib,json,pathlib,subprocess,sys
p=pathlib.Path(sys.argv[1]); m=json.loads((p/'manifest.json').read_text())
files=m['wheels']
import platform
import socket
actual_arch={'amd64':'x86_64','arm64':'aarch64'}.get(platform.machine().lower(), platform.machine().lower())
if m['target']['arch']!=actual_arch or m['target']['py']!='.'.join(str(x) for x in sys.version_info[:2]):
 raise SystemExit('TARGET_ARCH_PYTHON_MISMATCH')
if not files:raise SystemExit('BUNDLE_EMPTY')
actual={f.name for f in p.glob('*.whl')}
if actual!=set(files):raise SystemExit('BUNDLE_MISSING_OR_EXTRA')
for name,expected in files.items():
 if pathlib.Path(name).name!=name:raise SystemExit('BUNDLE_UNSAFE_FILENAME')
 digest=hashlib.sha256((p/name).read_bytes()).hexdigest()
 if digest!=expected:raise SystemExit('BUNDLE_SHA256_MISMATCH '+name)
if hashlib.sha256((p/'requirements.lock').read_bytes()).hexdigest()!=m['requirements_sha256']:
 raise SystemExit('LOCK_SHA256_MISMATCH')
from pathlib import Path
out=subprocess.run([sys.argv[2],'-m','pip','install','--no-index','--find-links',str(p),
 '--require-hashes','-r',str(p/'requirements.lock')],capture_output=True,text=True,timeout=125)
if out.returncode:raise SystemExit('OFFLINE_PIP_FAILED: '+out.stderr[-260:])
out=subprocess.run([sys.argv[2],'-m','pip','check'],capture_output=True,text=True,timeout=20)
if out.returncode:raise SystemExit('PIP_CHECK_FAILED: '+out.stdout[-260:])
out=subprocess.run([sys.argv[2],'-c','import websockets,psutil,yaml;print("DEPENDENCIES_OK")'],capture_output=True,text=True,timeout=20)
if out.returncode:raise SystemExit('IMPORT_CHECK_FAILED')
print('SONAR_OFFLINE_OK')
'''

def install_agent_dependencies(run, host, port, username, password, progress=None, connector=None):
    """Prepare dependencies on the central host, transfer over verified SSH/Agent,
    install offline first, and only use target-side pip as a last-resort fallback.

    The staged venv is created with the privileged runner; a working live venv is
    never removed until the replacement is verified and activated.
    """
    target = probe(run, progress)
    _ensure_os_tools(run, progress, host, port, username, password, connector)
    marker = secrets.token_hex(6)
    staged = f'/opt/sonar-agent-venv.stage-{marker}'
    live = '/opt/sonar-agent-venv'
    previous = f'/opt/sonar-agent-venv.previous-{marker}'
    ok, out = run('python3 -m venv ' + shlex.quote(staged) + ' && test -x ' + shlex.quote(staged + '/bin/python') +
                  ' && ' + shlex.quote(staged + '/bin/python') + ' -m pip --version', 90)
    if not ok:
        # Remove only the new incomplete staging directory; the live/previous venv is untouched.
        run('rm -rf -- ' + shlex.quote(staged), 30)
        raise RuntimeError('VENV_CREATE_FAILED: create/check with sudo failed after installing the matching python3-venv package: ' + str(out)[-260:])

    central_installed = False
    central_error = ''
    remote = None
    _emit(progress, 'prepare_bundle', 'سرور مرکزی بسته‌های سازگار را آماده و هش آن‌ها را ثبت می‌کند')
    try:
        with tempfile.TemporaryDirectory(prefix='sonar-wheelhouse-') as tmp:
            manifest = cached_wheelhouse(target, tmp)
            if not manifest or not manifest.get('wheels'):
                raise RuntimeError('WHEEL_BUNDLE_INCOMPLETE')
            _emit(progress, 'transfer', 'ارسال امن بسته‌ها از مسیر مرکزی؛ در صورت قطع مسیر مستقیم، Agent مجاز')
            remote = _sftp_bundle(host, port, username, password, tmp, connector=connector)
            _emit(progress, 'verify_bundle', 'بررسی SHA-256 و نصب کاملاً آفلاین روی مقصد')
            command = (f'python3 - {shlex.quote(remote)} {shlex.quote(staged + "/bin/python")} <<\'SONAR_VERIFY\'\n'
                       + VERIFY_INSTALL + '\nSONAR_VERIFY\n')
            ok, out = run(command, 190)
            if not ok:
                raise RuntimeError('OFFLINE_INSTALL_FAILED: ' + str(out)[-320:])
            run('rm -rf -- ' + shlex.quote(remote), 35)
            central_installed = True
    except Exception as exc:
        central_error = f'{type(exc).__name__}: {str(exc)[:180]}'
        _emit(progress, 'direct_fallback', 'بسته مرکزی در دسترس نبود؛ تلاش محدود برای pip مستقیم از مقصد')

    if not central_installed:
        direct = (f'{shlex.quote(staged)}/bin/python -m pip install '
                  '--disable-pip-version-check --timeout 12 --retries 1 '
                  + ' '.join(shlex.quote(item) for item in REQUIREMENTS)
                  + f' && {shlex.quote(staged)}/bin/python -m pip check && '
                    f'{shlex.quote(staged)}/bin/python -c ' + shlex.quote('import websockets, psutil, yaml'))
        ok, out = run('timeout 185s ' + direct, 205)
        if not ok:
            run('rm -rf -- ' + shlex.quote(staged), 30)
            raise RuntimeError('DEPENDENCY_INSTALL_FAILED: central bundle/Agent route failed (' + central_error[:150] +
                               ') and target pip fallback failed: ' + str(out)[-220:])
        if remote:
            run('rm -rf -- ' + shlex.quote(remote), 35)

    _emit(progress, 'dependencies_ok', 'وابستگی‌ها و pip check با Python مقصد تأیید شدند')
    check = (f'test ! -L {live} && '
             f'if test -e {live}; then mv -- {live} {shlex.quote(previous)}; fi; '
             f'mv -- {shlex.quote(staged)} {live}; '
             f'{live}/bin/python -m pip check && '
             f'{live}/bin/python -c ' + shlex.quote('import websockets,psutil,yaml'))
    ok, out = run(check, 60)
    if not ok:
        run(f'if test -d {shlex.quote(previous)} && ! test -e {live}; then mv -- {shlex.quote(previous)} {live}; fi', 30)
        raise RuntimeError('VENV_ACTIVATION_FAILED: ' + str(out)[-220:])
    return previous

def rollback_venv(run, previous, progress=None):
    """Restore original environment if present. Never discard a known-good venv."""
    if not previous:return
    safe=re.fullmatch(r'/opt/sonar-agent-venv\.previous-[a-f0-9]{12}',previous)
    if not safe:raise ValueError('Invalid rollback marker')
    _emit(progress,'rollback','بازیابی وابستگی‌های قبلی')
    run(f'if test -d {shlex.quote(previous)}; then mv /opt/sonar-agent-venv /opt/sonar-agent-venv.failed-{previous[-12:]}; mv {shlex.quote(previous)} /opt/sonar-agent-venv; systemctl try-restart sonar-agent || :; fi',60)


def commit_venv(run, previous, progress=None):
    """Post-health garbage collection; retain one verified rollback environment.

    This function does NOT operate during staging or failed installs. Older backup
    directories are deleted only if systemd is running and the new venv exists.
    """
    if previous and not re.fullmatch(r'/opt/sonar-agent-venv\.previous-[a-f0-9]{12}',previous):
        raise ValueError('Invalid venv marker')
    script = r'''import pathlib,shutil,subprocess,sys
keep=sys.argv[1]
root=pathlib.Path('/opt')
venv=root/'sonar-agent-venv'
if not venv.is_dir() or venv.is_symlink():raise SystemExit('LIVE_VENV_INVALID')
health=subprocess.run(['systemctl','is-active','--quiet','sonar-agent'],timeout=8)
if health.returncode:raise SystemExit('AGENT_NOT_ACTIVE_NO_CLEANUP')
for path in sorted(root.glob('sonar-agent-venv.previous-*')):
 if str(path)==keep or path.is_symlink() or not path.is_dir():continue
 if not __import__('re').fullmatch(r'sonar-agent-venv\.previous-[a-f0-9]{12}',path.name):continue
 shutil.rmtree(path)
print('SONAR_VENV_COMMITTED')
'''
    import shlex
    command="python3 - " + shlex.quote(previous or '') + " <<'SONAR_COMMIT_VENV'\n" + script + "\nSONAR_COMMIT_VENV\n"
    ok,details=run(command,60)
    if not ok:
        _emit(progress,'cleanup_warning','Agent فعال است؛ پاک‌سازی پشتیبان‌های قدیمی انجام نشد')
    return ok
