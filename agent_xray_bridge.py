"""Pinned Xray release transfer from the main VPS if Iran-side GitHub is unavailable.

Keeps TLS enrollment and the public agent protocol completely unchanged.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import tempfile
import urllib.request
import urllib.parse
import time
from agent_bootstrap import _sftp_bundle, _emit


def fetch_pinned_archive(lock, dest, architecture=None):
    architecture = architecture or {'amd64':'x86_64','arm64':'aarch64'}.get(platform.machine().lower(),platform.machine().lower())
    record=lock['assets'][architecture]
    url=record['url'];expected=record['sha256']
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme!='https' or parsed.hostname!='github.com' or not re.fullmatch('[a-f0-9]{64}',expected):
        raise RuntimeError('XRAY_LOCK_INVALID')
    dest=Path(dest)
    for attempt in range(2):
        try:
            # Avoid unbounded disk/memory use; GitHub redirects remain TLS verified.
            request=urllib.request.Request(url, headers={'User-Agent':'Sonar-Agent-Installer/1.0'})
            with urllib.request.urlopen(request,timeout=35) as resp, dest.open('wb') as out:
                digest=hashlib.sha256();size=0
                while True:
                    chunk=resp.read(256*1024)
                    if not chunk:break
                    size+=len(chunk)
                    if size>90*1024*1024:raise RuntimeError('XRAY_ARCHIVE_TOO_LARGE')
                    digest.update(chunk);out.write(chunk)
            if digest.hexdigest()!=expected:raise RuntimeError('XRAY_ARCHIVE_SHA256_MISMATCH')
            return expected
        except (OSError,RuntimeError) as exc:
            if attempt==1:raise RuntimeError('XRAY_MIRROR_UNAVAILABLE: '+type(exc).__name__) from exc
            time.sleep(1+attempt)

def install_xray_via_main(run, host, port, username, password, runtime_lock, progress=None):
    from agent_bootstrap import probe
    target=probe(run,progress)
    _emit(progress,'xray_fallback','GitHub مقصد در دسترس نیست؛ انتقال نسخه پین‌شده Xray از سرور اصلی')
    with tempfile.TemporaryDirectory(prefix='sonar-xray-') as tmp:
        source=Path(tmp)/'runtime.zip'
        expected=fetch_pinned_archive(runtime_lock,source,target['arch'])
        remote=_sftp_bundle(host,port,username,password,tmp)
        quoted=__import__('shlex').quote(remote+'/runtime.zip')
        cmd=('install -d -m 0750 -o sonar-agent -g sonar-agent /var/lib/sonar-agent && '
             f'install -m 0640 -o sonar-agent -g sonar-agent {quoted} /var/lib/sonar-agent/.sonar-runtime.zip && '
             f'rm -rf -- {__import__("shlex").quote(remote)}')
        ok,out=run(cmd,65)
        if not ok:raise RuntimeError('XRAY_TRANSFER_FAILED: '+str(out)[-160:])
    # The remote runtime SHA-256 verifier runs as the limited sonar-agent account.
    return '/var/lib/sonar-agent/.sonar-runtime.zip',expected
