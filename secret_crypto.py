"""Fernet-backed storage for server credentials and other persisted secrets."""
from __future__ import annotations

import os
import stat
import errno
import fcntl
import secrets
from cryptography.fernet import Fernet, InvalidToken


class SecretDecryptionError(RuntimeError):
    """Stored credential cannot be opened with the active encryption key."""


class Security:
    def __init__(self, key_file: str, secure_parent: bool = True):
        self.key_file = os.fspath(key_file)
        parent=os.path.dirname(os.path.abspath(self.key_file))
        name=os.path.basename(self.key_file)
        # Walk using directory descriptors: never traverse a symlink ancestor
        # and never chmod or create files through a substituted pathname.
        directory=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
        try:
            for part in parent.split('/'):
                if not part:continue
                try:os.mkdir(part,0o700,dir_fd=directory)
                except FileExistsError:pass
                child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
                os.close(directory);directory=child
            if secure_parent:os.fchmod(directory,0o700)
            flags=os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK
            lock=os.open('.'+name+'.lock',os.O_CREAT|os.O_RDWR|flags,0o600,dir_fd=directory)
            try:
                info=os.fstat(lock)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
                    raise PermissionError('Unsafe encryption-key lock')
                fcntl.flock(lock,fcntl.LOCK_EX)
                try:os.stat(name,dir_fd=directory,follow_symlinks=False)
                except FileNotFoundError:
                    material=Fernet.generate_key()+b'\n'
                    temporary='.sonar-key-'+secrets.token_hex(16)
                    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|flags,0o600,dir_fd=directory)
                    try:
                        with os.fdopen(fd,'wb') as stream:
                            stream.write(material);stream.flush();os.fsync(stream.fileno())
                        try:os.link(temporary,name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
                        except FileExistsError:pass
                        except OSError as exc:
                            if exc.errno not in (errno.EPERM,errno.EOPNOTSUPP,errno.EXDEV,errno.ENOSYS):raise
                            # All cooperating readers hold this lock, including
                            # readers of an existing key, so no partial key leaks.
                            fd=os.open(name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|flags,0o600,dir_fd=directory)
                            with os.fdopen(fd,'wb') as stream:
                                stream.write(material);stream.flush();os.fsync(stream.fileno())
                    finally:os.unlink(temporary,dir_fd=directory)
                fd=os.open(name,os.O_RDONLY|flags,dir_fd=directory)
                try:
                    metadata=os.fstat(fd)
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink!=1:
                        raise PermissionError('Encryption key must be a private regular file')
                    os.fchmod(fd,0o600)
                    with os.fdopen(fd,'rb',closefd=False) as stream:key=stream.read(4097).strip()
                finally:os.close(fd)
            finally:os.close(lock)
        finally:os.close(directory)

        # Invalid/missing key material is a startup error, never a blank credential.
        self.cipher = Fernet(key)

    def encrypt(self, value):
        if value is None:
            return ""
        if isinstance(value,bytes):
            # SSH credentials are text; reject invalid UTF-8 before persisting.
            value.decode('utf-8')
            raw=value
        elif isinstance(value,str):raw=value.encode('utf-8')
        else:raise TypeError('Credential must be str, UTF-8 bytes or None')
        return self.cipher.encrypt(raw).decode('ascii')

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
