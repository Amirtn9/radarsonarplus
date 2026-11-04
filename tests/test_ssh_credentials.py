from types import SimpleNamespace

import pytest

from ssh_credentials import (
    SSH_AUTH_PREFIX,
    encode_private_key_auth,
    paramiko_auth_kwargs,
)


PRIVATE_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\n" + ("A" * 96) + "\n-----END OPENSSH PRIVATE KEY-----"


def test_private_key_envelope_is_versioned_and_never_changes_password_mode():
    envelope = encode_private_key_auth(PRIVATE_KEY, "pass phrase")
    assert envelope.startswith(SSH_AUTH_PREFIX)
    assert paramiko_auth_kwargs("legacy-password", object()) == {"password": "legacy-password"}


def test_private_key_is_parsed_in_memory_with_passphrase():
    seen = {}

    class FakeKey:
        @classmethod
        def from_private_key(cls, stream, password=None):
            seen["key"] = stream.read()
            seen["passphrase"] = password
            return "parsed-key"

    fake_paramiko = SimpleNamespace(Ed25519Key=FakeKey, RSAKey=None, ECDSAKey=None, DSSKey=None)
    envelope = encode_private_key_auth(PRIVATE_KEY, "phrase")
    kwargs = paramiko_auth_kwargs(envelope, fake_paramiko)
    assert kwargs == {"pkey": "parsed-key", "password": "phrase"}
    assert seen == {"key": PRIVATE_KEY, "passphrase": "phrase"}


def test_invalid_private_key_and_envelope_are_rejected():
    with pytest.raises(ValueError):
        encode_private_key_auth("not a private key")
    with pytest.raises(ValueError):
        paramiko_auth_kwargs(SSH_AUTH_PREFIX + "{broken", SimpleNamespace())


def test_unsupported_private_key_does_not_fall_back_to_password():
    fake_paramiko = SimpleNamespace(Ed25519Key=None, RSAKey=None, ECDSAKey=None, DSSKey=None)
    with pytest.raises(ValueError, match="Unsupported"):
        paramiko_auth_kwargs(encode_private_key_auth(PRIVATE_KEY), fake_paramiko)


def test_real_paramiko_parses_generated_ed25519_key_in_memory():
    paramiko = pytest.importorskip("paramiko")
    cryptography = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    private = cryptography.Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    kwargs = paramiko_auth_kwargs(encode_private_key_auth(pem), paramiko)
    assert kwargs["pkey"].get_name() == "ssh-ed25519"
    assert kwargs["password"] is None
