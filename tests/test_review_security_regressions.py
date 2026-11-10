"""Behavioral regressions found while reviewing the uploaded 6.9.2 release."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from urllib.parse import urlencode

import pytest
from cryptography.fernet import Fernet

from secret_crypto import Security


@pytest.mark.parametrize("hardlinks", [True, False])
def test_concurrent_key_initialization_never_publishes_partial_material(tmp_path, monkeypatch, hardlinks):
    if not hardlinks:
        import errno
        def unsupported(*args,**kwargs):raise OSError(errno.EOPNOTSUPP,"fixture unsupported")
        monkeypatch.setattr(os,"link",unsupported)
    original = Fernet.generate_key
    writing, release = Event(), Event()
    calls = []

    def delayed_key():
        calls.append(True)
        if len(calls) == 1:
            writing.set()
            assert release.wait(5)
        return original()

    monkeypatch.setattr(Fernet, 'generate_key', delayed_key)
    path = tmp_path / 'state' / 'secret.key'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(Security, path)
        assert writing.wait(5)
        try:
            second = pool.submit(Security,path)
            assert not second.done()
        finally:
            release.set()
        first = first.result(timeout=5)
        second = second.result(timeout=5)
    assert first.decrypt(second.encrypt('preserved credential')) == 'preserved credential'
    assert not list(path.parent.glob('.sonar-key-*'))


def test_nonregular_key_is_rejected_without_blocking(tmp_path):
    path = tmp_path / 'secret.key'
    os.mkfifo(path)
    with pytest.raises(PermissionError, match='regular file'):
        Security(path)


@pytest.mark.parametrize('signature', ['é' * 64, 'G' * 64, '0' * 63])
def test_invalid_signature_is_an_authentication_error(signature):
    from pro_auth import verify_init_data
    with pytest.raises(PermissionError):
        verify_init_data(urlencode({'hash': signature, 'auth_date': '123', 'user': '{"id":1}'}),
                         'test-token', now=123)


def test_backup_does_not_follow_predictable_temporary_symlink(tmp_path, monkeypatch):
    import subprocess
    import enterprise_runtime as runtime
    monkeypatch.setenv('SONAR_BACKUP_DIR', str(tmp_path))
    monkeypatch.setenv('SONAR_BACKUP_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setattr(runtime.subprocess, 'run', lambda *a, **kw:
                        subprocess.CompletedProcess(a[0], 0, b'archive', b''))
    victim = tmp_path / 'unrelated'
    victim.write_bytes(b'keep')
    (tmp_path / 'daily.enc.tmp').symlink_to(victim)
    runtime.create_encrypted_backup({}, 'daily.enc')
    assert victim.read_bytes() == b'keep'
    assert (tmp_path / 'daily.enc').stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob('.sonar-backup-*'))


def test_security_updated_paramiko_loads_encrypted_private_key():
    import io
    import paramiko
    from ssh_credentials import encode_private_key_auth, paramiko_auth_kwargs
    key = paramiko.RSAKey.generate(2048)
    stream = io.StringIO()
    key.write_private_key(stream, password='test-only-passphrase')
    encoded = encode_private_key_auth(stream.getvalue(), 'test-only-passphrase')
    loaded = paramiko_auth_kwargs(encoded, paramiko)
    assert loaded['pkey'].asbytes() == key.asbytes()
