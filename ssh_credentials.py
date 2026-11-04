"""SSH credential envelope helpers.

Private keys are submitted in-memory, encrypted by the existing Fernet storage
layer, and parsed by Paramiko only at connection time. No key material is ever
written to a temporary file or included in a command line/log.
"""
from __future__ import annotations

import io
import json

SSH_AUTH_PREFIX = "SONAR_SSH_AUTH_V1:"
MAX_PRIVATE_KEY_CHARS = 32_768
MAX_PASSPHRASE_CHARS = 1_024


def encode_private_key_auth(private_key: str, passphrase: str | None = None) -> str:
    if not isinstance(private_key, str):
        raise ValueError("Private key must be text")
    key = private_key.strip()
    if (
        not 80 <= len(key) <= MAX_PRIVATE_KEY_CHARS
        or "\x00" in key
        or "-----BEGIN " not in key
        or "PRIVATE KEY-----" not in key
        or "-----END " not in key
    ):
        raise ValueError("Private key must be a valid PEM/OpenSSH private-key block")
    phrase = "" if passphrase is None else str(passphrase)
    if len(phrase) > MAX_PASSPHRASE_CHARS or "\x00" in phrase:
        raise ValueError("Private-key passphrase is invalid")
    payload = {
        "version": 1,
        "method": "private_key",
        "private_key": key,
        "passphrase": phrase,
    }
    return SSH_AUTH_PREFIX + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _decode_envelope(value: str) -> dict | None:
    if not isinstance(value, str) or not value.startswith(SSH_AUTH_PREFIX):
        return None
    try:
        payload = json.loads(value[len(SSH_AUTH_PREFIX):])
    except (TypeError, ValueError):
        raise ValueError("Stored SSH credential envelope is invalid") from None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or payload.get("method") != "private_key"
    ):
        raise ValueError("Stored SSH credential envelope version is unsupported")
    key = payload.get("private_key")
    phrase = payload.get("passphrase", "")
    if not isinstance(key, str) or not isinstance(phrase, str):
        raise ValueError("Stored SSH key credential is malformed")
    if not 80 <= len(key) <= MAX_PRIVATE_KEY_CHARS or "\x00" in key:
        raise ValueError("Stored SSH private key is invalid")
    if len(phrase) > MAX_PASSPHRASE_CHARS or "\x00" in phrase:
        raise ValueError("Stored SSH private-key passphrase is invalid")
    return {"private_key": key, "passphrase": phrase}


def paramiko_auth_kwargs(credential, paramiko_module) -> dict:
    """Translate a legacy password or encrypted private-key envelope to kwargs."""
    if not isinstance(credential, str) or not credential.startswith(SSH_AUTH_PREFIX):
        return {"password": credential}

    payload = _decode_envelope(credential)
    key_text = payload["private_key"]
    passphrase = payload["passphrase"] or None
    key_classes = (
        getattr(paramiko_module, "Ed25519Key", None),
        getattr(paramiko_module, "RSAKey", None),
        getattr(paramiko_module, "ECDSAKey", None),
        getattr(paramiko_module, "DSSKey", None),
    )
    for key_class in key_classes:
        if key_class is None:
            continue
        stream = io.StringIO(key_text)
        try:
            key = key_class.from_private_key(stream, password=passphrase)
            return {"pkey": key, "password": passphrase}
        except TypeError:
            # Compatibility with Paramiko key classes exposing a positional
            # passphrase parameter instead of the keyword form.
            try:
                stream.seek(0)
                key = key_class.from_private_key(stream, passphrase)
                return {"pkey": key, "password": passphrase}
            except Exception:
                continue
        except Exception:
            continue
    raise ValueError("Unsupported, malformed, or incorrectly encrypted SSH private key")
