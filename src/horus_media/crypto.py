import contextlib
import logging
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet

KEY_FILENAME = "horus.key"


log = logging.getLogger(__name__)
_LOCK = threading.Lock()
_cached: dict = {}  # the last cipher made by TokenCipher.for_db, with the key file's stamp


def _load_or_create_key(key_path: Path) -> bytes:
    if key_path.exists():
        return key_path.read_bytes().strip()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key()
    # O_EXCL so two first-runs cannot clobber each other's key.
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    with contextlib.suppress(OSError):
        os.chmod(key_path, 0o600)  # no-op on most Windows filesystems
    log.info("created a new encryption key at %s", key_path)
    return key


class TokenCipher:
    """Encrypts server tokens at rest. The key file lives next to the database."""

    def __init__(self, key_path: Path):
        self._fernet = Fernet(_load_or_create_key(key_path))

    @classmethod
    def for_db(cls, db_file: Path) -> "TokenCipher":
        """The cipher for this database's key file. It is kept and reused until the key file
        changes (a restore can replace it), so the file is not read on every request."""
        key_path = db_file.parent / KEY_FILENAME
        try:
            found = key_path.stat()
            stamp = (str(key_path), found.st_ino, found.st_mtime_ns, found.st_size)
        except OSError:
            return cls(key_path)  # no key yet: this creates it; it is remembered next time
        with _LOCK:
            if _cached.get("stamp") != stamp:
                _cached.update(stamp=stamp, cipher=cls(key_path))
            return _cached["cipher"]

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()
