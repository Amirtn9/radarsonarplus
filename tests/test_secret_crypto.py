import os

import pytest
from cryptography.fernet import Fernet

from secret_crypto import Security, SecretDecryptionError


def test_key_file_is_created_private_and_round_trips(tmp_path):
    key_path = tmp_path / "state" / "secret.key"
    security = Security(key_path)
    assert key_path.is_file()
    assert key_path.stat().st_mode & 0o777 == 0o600
    token = security.encrypt("credential")
    assert security.decrypt(token) == "credential"


def test_wrong_or_lost_key_never_becomes_empty_password(tmp_path):
    first = Security(tmp_path / "first" / "secret.key")
    token = first.encrypt("ssh-secret")
    second_path = tmp_path / "second" / "secret.key"
    second_path.parent.mkdir()
    second_path.write_bytes(Fernet.generate_key())
    second = Security(second_path)
    with pytest.raises(SecretDecryptionError, match="matching secret.key"):
        second.decrypt(token)


def test_symlinked_key_file_is_rejected(tmp_path):
    real = tmp_path / "real.key"
    real.write_bytes(Fernet.generate_key())
    link = tmp_path / "state" / "secret.key"
    link.parent.mkdir()
    link.symlink_to(real)
    with pytest.raises(OSError):
        Security(link)


def test_legacy_key_in_source_directory_does_not_chmod_application_tree(tmp_path):
    key_path = tmp_path / "secret.key"
    key_path.write_bytes(Fernet.generate_key())
    os.chmod(tmp_path, 0o755)
    Security(key_path, secure_parent=False)
    assert tmp_path.stat().st_mode & 0o777 == 0o755
    assert key_path.stat().st_mode & 0o777 == 0o600


def test_utf8_bytes_round_trip_without_python_bytes_literal(tmp_path):
    sec=Security(tmp_path/'secret.key')
    value=b'-----BEGIN PRIVATE KEY-----\nfixture\n-----END PRIVATE KEY-----'
    assert sec.decrypt(sec.encrypt(value))==value.decode()
    assert sec.encrypt(None)==''
    assert sec.encrypt('')!=''
    with pytest.raises(TypeError):sec.encrypt({'password':'fixture'})


def test_key_creation_without_hardlink_support_is_safe(tmp_path,monkeypatch):
    import errno
    def unsupported(*args,**kwargs):raise OSError(errno.EOPNOTSUPP,'fixture unsupported')
    monkeypatch.setattr(os,'link',unsupported)
    sec=Security(tmp_path/'secret.key')
    assert Security(tmp_path/'secret.key').decrypt(sec.encrypt('fixture'))=='fixture'


def test_symlinked_ancestor_is_rejected_before_creating_directories(tmp_path):
    target=tmp_path/'target';target.mkdir()
    (tmp_path/'linked').symlink_to(target,target_is_directory=True)
    with pytest.raises(OSError):Security(tmp_path/'linked'/'nested'/'secret.key')
    assert not (target/'nested').exists()


def test_hardlinked_key_is_rejected_without_chmodding_target(tmp_path):
    target=tmp_path/'target';target.write_bytes(Fernet.generate_key());target.chmod(0o644)
    os.link(target,tmp_path/'secret.key')
    with pytest.raises(PermissionError):Security(tmp_path/'secret.key')
    assert target.stat().st_mode&0o777==0o644
