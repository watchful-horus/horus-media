import logging
import os
import sqlite3
import threading
from pathlib import Path

from .migrations import MIGRATIONS

log = logging.getLogger(__name__)

# The poller thread and the first web request both call migrate() at startup; without this one
# of them can find a table half-created by the other.
_MIGRATE_LOCK = threading.Lock()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def owner_only(path: Path) -> None:
    """Only the account Horus runs as may read the database (password hashes, encrypted
    tokens) or look into its folder (the key, backups and log live there too). SQLite gives its
    journal file the database's mode. Does nothing useful on Windows, and a folder Horus may
    not change (a mount, say) is left as it is."""
    if os.name == "nt":
        return
    for target, mode in ((path, 0o600), (path.parent, 0o700)):
        try:
            os.chmod(target, mode)
        except OSError as e:
            log.warning("could not make %s private (%s)", target.name, type(e).__name__)


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations; returns the resulting schema version."""
    with _MIGRATE_LOCK:
        return _migrate(conn)


def _migrate(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    current = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    for version, sql in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        with conn:
            for statement in sql.split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    return len(MIGRATIONS)
