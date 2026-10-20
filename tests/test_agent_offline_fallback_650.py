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

def fake_run(command, timeout=60, fail_direct=False):
    if 'SONAR_PROBE' in command:return True,'SONAR_PROBE='+json.dumps(TARGET)
    if ' -m venv --help' in command:return True,''
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

def test_direct_install_never_builds_bundle(monkeypatch):
    monkeypatch.setattr(b,'_sftp_bundle',lambda *a,**kw: pytest.fail('SFTP used on healthy direct path'))
    monkeypatch.setattr(b,'cached_wheelhouse',lambda *a,**kw: pytest.fail('wheel download used on healthy path'))
    stages=[]
    name=b.install_agent_dependencies(lambda cmd, t=60:fake_run(cmd,t), 'example.com',22,'root','test',
                                      lambda stage,_: stages.append(stage))
    assert '.previous-' in name
    assert 'direct' in stages and 'fallback' not in stages

def test_direct_timeout_activates_offline(monkeypatch):
    def fake_prepare(target,dest):
        wheel=pathlib.Path(dest)/'websockets-13.1-py3-none-any.whl'
        wheel.write_bytes(b'fake-test-only')
        (pathlib.Path(dest)/'manifest.json').write_text('{}')
        (pathlib.Path(dest)/'requirements.lock').write_text('websockets==13.1')
    monkeypatch.setattr(b,'cached_wheelhouse',fake_prepare)
    monkeypatch.setattr(b,'_sftp_bundle',lambda *a,**kw:'/home/runner/.verified-package')
    steps=[]
    run=lambda cmd,t=60:fake_run(cmd,t,True)
    assert '.previous-' in b.install_agent_dependencies(run,'example.com',22,'root','test',
                                                       lambda stage,_:steps.append(stage))
    assert steps.index('fallback')<steps.index('prepare_bundle')<steps.index('verify_bundle')
    assert 'dependencies_ok' in steps

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
