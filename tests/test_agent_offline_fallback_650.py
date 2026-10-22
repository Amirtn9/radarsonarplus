"""Offline/unstable-network installer tests: no real SSH or public network used."""
import hashlib
import json
import pathlib
import sys
import tempfile
from unittest.mock import Mock

import pytest

import agent_bootstrap as b

TARGET={'system':'Linux','pkg':'apt','libc':'glibc','arch':'x86_64',
        'python':[3,11],'free':800*1024*1024,'os_id':'ubuntu'}

def fake_run(command, timeout=60, fail_direct=False, fail_bundle=False):
    if 'SONAR_PROBE' in command:return True,'SONAR_PROBE='+json.dumps(TARGET)
    if 'SONAR_VENV_PREFLIGHT' in command:return True,'SONAR_VENV_READY'
    if 'SONAR_VERIFY' in command and fail_bundle:return False,'BUNDLE_VERIFY_FAILED'
    if 'timeout 185s' in command and fail_direct:return False,'exit=124: ReadTimeout'
    return True,'SONAR_OFFLINE_OK'

def test_ubuntu_amd64_python_311_accepted():
    assert b.target_from_json(TARGET)['abi']=='cp311'

@pytest.mark.parametrize('update',[
    {'system':'Windows'}, {'pkg':'yum'}, {'libc':'musl'}, {'python':[3,7]},
    {'arch':'ppc64le'}, {'free':1234}
])
def test_unsafe_target_rejected(update):
    with pytest.raises(ValueError):b.target_from_json({**TARGET,**update})

def test_central_wheelhouse_is_primary_and_transferred_over_ssh_or_agent(monkeypatch):
    transfer=[]
    monkeypatch.setattr(b,'cached_wheelhouse',lambda target,dest:{'wheels':{'websockets.whl':'abc'}})
    monkeypatch.setattr(b,'_sftp_bundle',lambda *a,**kw:transfer.append((a,kw)) or '/home/runner/.verified-package')
    stages=[]
    name=b.install_agent_dependencies(lambda cmd,t=60:fake_run(cmd,t), 'example.com',22,'ubuntu','test',
                                      lambda stage,_:stages.append(stage))
    assert '.previous-' in name
    assert transfer
    assert stages.index('prepare_bundle') < stages.index('transfer') < stages.index('verify_bundle')
    assert 'direct_fallback' not in stages


def test_central_bundle_failure_uses_target_pip_only_as_last_resort(monkeypatch):
    monkeypatch.setattr(b,'cached_wheelhouse',lambda target,dest:{'wheels':{'websockets.whl':'abc'}})
    monkeypatch.setattr(b,'_sftp_bundle',lambda *a,**kw:'/home/runner/.verified-package')
    steps=[]
    def run(command,t=60):
        return fake_run(command,t,fail_bundle=True)
    name=b.install_agent_dependencies(run,'example.com',22,'ubuntu','test',
                                      lambda stage,_:steps.append(stage))
    assert '.previous-' in name
    assert 'verify_bundle' in steps and 'direct_fallback' in steps and 'dependencies_ok' in steps


def test_missing_matching_venv_package_returns_actionable_error(monkeypatch):
    calls=[]
    def run(command,timeout=60):
        calls.append(command)
        if 'SONAR_PROBE' in command:return True,'SONAR_PROBE='+json.dumps(TARGET)
        if 'sys.version_info' in command:return True,'3.11'
        if 'apt-get' in command:return True,'installed'
        return True,'SONAR_OFFLINE_OK'
    monkeypatch.setattr(b,'_venv_preflight',lambda run,timeout=35:(False,'VENV_CREATE_PROBE_FAILED: ensurepip missing'))
    with pytest.raises(RuntimeError,match='VENV_PREREQUISITE_FAILED'):
        b._ensure_os_tools(run,None)
    assert any('python3-venv' in cmd for cmd in calls)


def test_hash_corruption_detected_by_remote_verifier(tmp_path):
    # Execute the real remote verifier until its hash check; do not call pip.
    wh=tmp_path/'wheelhouse';wh.mkdir()
    wheel=wh/'websockets-13.1-py3-none-any.whl';wheel.write_bytes(b'wheel')
    lock=wh/'requirements.lock';lock.write_text('websockets==13.1\n')
    target=b.target_from_json({**TARGET, 'python':list(sys.version_info[:2])})
    manifest={'target':target,'wheels':{wheel.name:hashlib.sha256(b'wheel').hexdigest()},
              'requirements_sha256':hashlib.sha256(lock.read_bytes()).hexdigest()}
    (wh/'manifest.json').write_text(json.dumps(manifest))
    wheel.write_bytes(b'wheel-tampered')
    import subprocess
    result=subprocess.run([sys.executable,'-',str(wh),sys.executable],
                          input=b.VERIFY_INSTALL,text=True,capture_output=True)
    assert result.returncode != 0 and 'BUNDLE_SHA256_MISMATCH' in result.stderr

def test_missing_wheel_detected(tmp_path):
    wh=tmp_path/'wheelhouse';wh.mkdir()
    lock=wh/'requirements.lock';lock.write_text('websockets==13.1\n')
    manifest={'target':b.target_from_json({**TARGET,'python':list(sys.version_info[:2])}),'wheels':{'websockets-13.1-py3-none-any.whl':'0'*64},
              'requirements_sha256':hashlib.sha256(lock.read_bytes()).hexdigest()}
    (wh/'manifest.json').write_text(json.dumps(manifest))
    import subprocess
    result=subprocess.run([sys.executable,'-',str(wh),sys.executable],input=b.VERIFY_INSTALL,text=True,capture_output=True)
    assert result.returncode and 'BUNDLE_MISSING_OR_EXTRA' in result.stderr

def test_lock_hash_validation(tmp_path):
    wh=tmp_path/'wheelhouse';wh.mkdir()
    wheel=wh/'websockets-13.1-py3-none-any.whl';wheel.write_bytes(b'wheel')
    lock=wh/'requirements.lock';lock.write_text('tampered')
    manifest={'target':b.target_from_json({**TARGET,'python':list(sys.version_info[:2])}),'wheels':{wheel.name:hashlib.sha256(wheel.read_bytes()).hexdigest()},
              'requirements_sha256':'f'*64}
    (wh/'manifest.json').write_text(json.dumps(manifest))
    import subprocess
    res=subprocess.run([sys.executable,'-',str(wh),sys.executable],input=b.VERIFY_INSTALL,text=True,capture_output=True)
    assert res.returncode and 'LOCK_SHA256_MISMATCH' in res.stderr

def test_no_network_commands_in_remote_offline_installer():
    script=b.VERIFY_INSTALL
    assert "'--no-index'" in script
    assert "'--require-hashes'" in script
    assert 'curl ' not in script and 'wget ' not in script

def test_verified_wheel_cache_reused_and_corrupt_entry_rebuilt(monkeypatch,tmp_path):
    cache=tmp_path/'private-cache'
    monkeypatch.setenv('SONAR_AGENT_PACKAGE_CACHE_DIR',str(cache))
    counter=[]
    def fake_download(target, dest):
        counter.append(1)
        wh=pathlib.Path(dest)
        wheel=wh/'psutil-6.1.1-py3-none-any.whl'
        wheel.write_bytes(b'known good bytes')
        lock=wh/'requirements.lock';lock.write_text('psutil==6.1.1\n')
        manifest={'format':1,'target':target,'wheels':{wheel.name:b.sha256(wheel.read_bytes())},
                  'requirements_sha256':b.sha256(lock.read_bytes())}
        (wh/'manifest.json').write_text(json.dumps(manifest))
        return manifest
    monkeypatch.setattr(b,'make_wheelhouse',fake_download)
    target=b.target_from_json(TARGET)
    first=tmp_path/'one';first.mkdir();second=tmp_path/'two';second.mkdir()
    b.cached_wheelhouse(target,first)
    b.cached_wheelhouse(target,second)
    assert len(counter)==1
    cached=next(cache.glob('*/psutil-*.whl'))
    cached.write_bytes(b'tampered but plausible')
    third=tmp_path/'three';third.mkdir()
    b.cached_wheelhouse(target,third)
    assert len(counter)==2
    assert (third/cached.name).read_bytes()==b'known good bytes'


def test_apt_uri_plan_requires_public_mirror_and_signed_index_hash(monkeypatch):
    monkeypatch.setattr(b.socket, 'getaddrinfo', lambda *a, **kw: [(None,None,None,None,('91.189.91.81',80))])
    output = "'http://archive.ubuntu.com/ubuntu/pool/main/p/python3-venv.deb' python3-venv.deb 1234 MD5Sum:0123456789abcdef0123456789abcdef"
    records = b._parse_apt_uris(output)
    assert len(records) == 1
    assert records[0]['name'] == 'python3-venv.deb'
    assert records[0]['hash_kind'] == 'md5sum'


def test_system_prerequisites_can_use_central_deb_bundle_after_target_apt_failure(monkeypatch):
    state={'basic_calls':0,'central_calls':0}
    def run(command, timeout=60):
        if 'SONAR_VENV_PREFLIGHT' in command:return True,'SONAR_VENV_READY'
        if 'command -v python3' in command:
            state['basic_calls']+=1
            return (False,'missing') if state['basic_calls']<3 else (True,'ready')
        if 'apt-get' in command:return False,'APT_NETWORK_BLOCKED'
        return True,'ok'
    def central(*args,**kwargs):
        state['central_calls']+=1
        return True,'CENTRAL_APT_BUNDLE_INSTALLED'
    monkeypatch.setattr(b,'_download_apt_bundle',central)
    b._ensure_os_tools(run,None,'central.example',22,'ubuntu','password',lambda *a,**k:None)
    assert state['central_calls']==1
    assert state['basic_calls']>=3


def test_apt_uri_plan_rejects_private_mirror_hosts(monkeypatch):
    monkeypatch.setattr(b.socket, 'getaddrinfo', lambda *a, **kw: [(None,None,None,None,('127.0.0.1',80))])
    output = "'http://localhost/pool/python3-venv.deb' python3-venv.deb 1234 MD5Sum:0123456789abcdef0123456789abcdef"
    with pytest.raises(RuntimeError,match='APT_BUNDLE_NONPUBLIC_MIRROR'):
        b._parse_apt_uris(output)
