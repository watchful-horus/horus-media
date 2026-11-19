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


# The poller, a backup and web requests share the one file. SQLite lets one of them write at a
# time; the others wait this long for their turn before giving up with "database is locked".
BUSY_SECONDS = 30

# Database files this process has already brought up to date, by (device, file id), so the
# check is made once and not on every request. A restore writes into the same file and migrates
# it itself; a file swapped in from outside has another id and is checked again.
_UP_TO_DATE: set[tuple[int, int]] = set()


class _Connection(sqlite3.Connection):
    horus_path: Path | None = None


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=BUSY_SECONDS, check_same_thread=False,
                           factory=_Connection)
    conn.horus_path = path
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _file_id(conn: sqlite3.Connection) -> tuple[int, int] | None:
    path = getattr(conn, "horus_path", None)
    try:
        found = path.stat() if path else None
    except OSError:
        return None
    return (found.st_dev, found.st_ino) if found and found.st_ino else None


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
    """Apply pending migrations; returns the resulting schema version. For a connection from
    `connect` the work is done once per database file and process; later calls return at once."""
    file_id = _file_id(conn)
    if file_id in _UP_TO_DATE:
        return len(MIGRATIONS)
    with _MIGRATE_LOCK:
        version = _migrate(conn)
        if file_id:
            _UP_TO_DATE.add(file_id)
        return version


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
