"""Direct-first, SSH-verified/offline Python dependency installer for Ubuntu agents.

Does not handle tokens, enrollment, TLS credentials, WebSocket or agent runtime settings.
Refuses a fallback package if target tags cannot be resolved into binary wheels.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shlex
import subprocess
import tempfile
import time

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

def _ensure_os_tools(run, progress):
    _emit(progress,'system_packages','بررسی ابزارهای ضروری سیستم')
    # If everything already exists, never run apt update or touch the package DB.
    check='''python3 -m venv --help >/dev/null 2>&1 && command -v openssl >/dev/null && command -v curl >/dev/null && command -v ss >/dev/null && test -s /etc/ssl/certs/ca-certificates.crt'''
    ok, _ = run(check, 20)
    if ok:return
    _emit(progress,'system_packages','تکمیل پیش‌نیازهای سیستم با apt (محدودیت زمانی)')
    cmd=('export DEBIAN_FRONTEND=noninteractive; '
         'timeout 95s apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=12 '
         '-o Acquire::https::Timeout=12 update -y && '
         'timeout 110s apt-get -o Acquire::Retries=1 -o Acquire::http::Timeout=12 '
         '-o Acquire::https::Timeout=12 install -y '
         'python3-venv ca-certificates curl openssl iproute2')
    ok, out = run(cmd, 230)
    if not ok: raise RuntimeError('SYSTEM_PACKAGES_UNAVAILABLE: apt dependencies unavailable; configure Ubuntu mirror/cache: '+str(out)[-200:])
    ok,_ = run(check,20)
    if not ok:raise RuntimeError('SYSTEM_PACKAGES_INCOMPLETE: required tools missing after apt')

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
    """Set up staged venv; return rollback marker. Call commit_venv *after* WSS health.

    An existing live venv is never deleted; moves to .previous for rollback.
    """
    target=probe(run,progress)
    _ensure_os_tools(run,progress)
    marker=secrets.token_hex(6)
    staged=f'/opt/sonar-agent-venv.stage-{marker}'
    live='/opt/sonar-agent-venv'
    previous=f'/opt/sonar-agent-venv.previous-{marker}'
    ok,out=run('python3 -m venv '+shlex.quote(staged)+' && test -x '+shlex.quote(staged+'/bin/python'),75)
    if not ok:raise RuntimeError('VENV_CREATE_FAILED: '+str(out)[-170:])
    _emit(progress,'direct','نصب مستقیم وابستگی‌ها (زمان محدود)')
    direct=(f'{shlex.quote(staged)}/bin/python -m pip install '
            '--disable-pip-version-check --timeout 12 --retries 1 '
            + ' '.join(shlex.quote(s) for s in REQUIREMENTS)
            + f' && {shlex.quote(staged)}/bin/python -m pip check && '
              f'{shlex.quote(staged)}/bin/python -c '+shlex.quote('import websockets, psutil, yaml'))
    ok,out=run('timeout 185s '+direct,205)
    if not ok:
        _emit(progress,'fallback','نصب مستقیم ناموفق یا کند؛ دریافت بسته آفلاین از سرور اصلی')
        with tempfile.TemporaryDirectory(prefix='sonar-wheelhouse-') as tmp:
            _emit(progress,'prepare_bundle','تهیه wheel سازگار با معماری و Python مقصد')
            manifest=cached_wheelhouse(target,tmp)
            _emit(progress,'transfer','ارسال امن بسته با SSH/SFTP')
            remote=_sftp_bundle(host,port,username,password,tmp,connector=connector)
            _emit(progress,'verify_bundle','بررسی SHA-256 و نصب کاملاً آفلاین')
            # Remote installer verifies target metadata on disk too.
            command=(f'python3 - {shlex.quote(remote)} {shlex.quote(staged+"/bin/python")} <<\'SONAR_VERIFY\'\n'
                     + VERIFY_INSTALL + '\nSONAR_VERIFY\n')
            ok,out=run(command,170)
            if not ok:
                raise RuntimeError('OFFLINE_INSTALL_FAILED: '+str(out)[-320:])
            # Preserve failed stage for diagnosis; bundle is removed after successful installation only.
            run(f'rm -rf -- {shlex.quote(remote)}',35)
    _emit(progress,'dependencies_ok','وابستگی‌ها با Python مقصد بررسی شدند')
    # Swapping a venv by moving it is safe only if interpreter/shebang path points at live path.
    # Python console scripts in this project are not used; python -m and shared installed libs are.
    check=(f'test ! -L {live} && '
           f'if test -e {live}; then mv -- {live} {shlex.quote(previous)}; fi; '
           f'mv -- {shlex.quote(staged)} {live}; '
           f'{live}/bin/python -c '+shlex.quote('import websockets,psutil,yaml'))
    ok,out=run(check,50)
    if not ok:
        run(f'if test -d {shlex.quote(previous)} && ! test -e {live}; then mv -- {shlex.quote(previous)} {live}; fi',30)
        raise RuntimeError('VENV_ACTIVATION_FAILED: '+str(out)[-180:])
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
