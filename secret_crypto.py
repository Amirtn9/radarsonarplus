"""Fernet-backed storage for server credentials and other persisted secrets."""
from __future__ import annotations

import os
import stat
import tempfile
from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(RuntimeError):
    """Stored credential cannot be opened with the active encryption key."""


class Security:
    def __init__(self, key_file: str, secure_parent: bool = True):
        self.key_file = os.fspath(key_file)
        parent = os.path.dirname(self.key_file) or "."
        os.makedirs(parent, mode=0o700, exist_ok=True)
        if os.path.islink(parent):
            raise PermissionError("Refusing symlinked encryption-key directory")
        # A legacy install may keep secret.key in the source directory. Never
        # chmod the whole application tree; the key file itself remains 0600.
        if secure_parent:
            os.chmod(parent, 0o700)

        if not os.path.exists(self.key_file):
            # Publish only fully written key material. Opening the final path
            # with O_EXCL exposes an empty file to concurrent Bot/Web startup.
            fd, temporary = tempfile.mkstemp(prefix=".sonar-key-", dir=parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(Fernet.generate_key() + b"\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, self.key_file)
                except FileExistsError:
                    # Another process published first; read its complete key.
                    pass
            finally:
                os.unlink(temporary)

        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                 | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0))
        fd = os.open(self.key_file, flags)
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise PermissionError("Encryption key must be a regular file")
            os.fchmod(fd, 0o600)
            chunks = []
            while True:
                chunk = os.read(fd, 4096)
                if not chunk:
                    break
                chunks.append(chunk)
            key = b"".join(chunks).strip()
        finally:
            os.close(fd)

        # Invalid/missing key material is a startup error, never a blank credential.
        self.cipher = Fernet(key)

    def encrypt(self, value):
        if value is None:
            return ""
        return self.cipher.encrypt(str(value).encode("utf-8")).decode("ascii")

    def decrypt(self, value):
        if value is None or value == "":
            return ""
        if not isinstance(value, (str, bytes)):
            raise SecretDecryptionError("Stored credential has an invalid data type")
        try:
            raw = value.encode("ascii") if isinstance(value, str) else value
            return self.cipher.decrypt(raw).decode("utf-8")
        except (InvalidToken, UnicodeError, ValueError, TypeError):
            # Never turn key loss into a passwordless SSH attempt.
            raise SecretDecryptionError(
                "Stored credential cannot be decrypted; restore its matching secret.key backup"
            ) from None
