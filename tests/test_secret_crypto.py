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
