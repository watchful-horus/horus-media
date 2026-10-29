"""Backups of the Horus database.

A full backup is a zip of the database plus its encryption key, saved to a `backups` folder next
to the database (the key already lives there, so this exposes nothing new). It is never served
over HTTP. The only downloadable export blanks the server tokens, so it is safe to hand around:
after restoring one you sign in with Plex / re-enter keys again.
"""

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .crypto import KEY_FILENAME, _load_or_create_key
from .db import migrate
from .migrations import MIGRATIONS

log = logging.getLogger(__name__)
NAME_RE = re.compile(r"^horus-\d{8}-\d{6}\.zip$")
MAX_UPLOAD = 2 * 1024**3  # a backup bigger than 2 GB is not a Horus backup


class RestoreError(Exception):
    """The backup cannot be restored. The message is safe to show."""


def backup_dir(db_file: Path) -> Path:
    return db_file.parent / "backups"


def _copy_db(db_file: Path, dest: Path) -> None:
    """Consistent snapshot using SQLite's online backup, safe while Horus is running."""
    src = sqlite3.connect(db_file)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


def write_full_backup(db_file: Path, now: datetime | None = None) -> Path:
    folder = backup_dir(db_file)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%S")
    target = folder / f"horus-{stamp}.zip"
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        snapshot = Path(tmp) / "horus.db"
        _copy_db(db_file, snapshot)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot, "horus.db")
            key = db_file.parent / KEY_FILENAME
            if key.exists():
                z.write(key, KEY_FILENAME)
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass  # not supported on every filesystem
    return target


def write_safe_export(db_file: Path, dest_dir: Path) -> Path:
    """A copy of the database with server tokens removed."""
    dest = dest_dir / "horus-no-tokens.db"
    _copy_db(db_file, dest)
    conn = sqlite3.connect(dest)
    try:
        with conn:
            conn.execute("UPDATE servers SET token = ''")
    finally:
        conn.close()
    return dest


def find_backup(db_file: Path, name: str) -> Path | None:
    """The saved backup called `name`, or None. It is found by listing the backups folder, never by
    building a path from the request, so a crafted name cannot point anywhere else."""
    folder = backup_dir(db_file)
    if not NAME_RE.fullmatch(name) or not folder.is_dir():
        return None
    return next((f for f in folder.iterdir() if f.name == name and f.is_file()), None)


def list_backups(db_file: Path) -> list[dict]:
    folder = backup_dir(db_file)
    if not folder.is_dir():
        return []
    items = []
    for f in folder.iterdir():
        if NAME_RE.fullmatch(f.name) and f.is_file():
            stat = f.stat()
            items.append({
                "name": f.name,
                "size": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            })
    return sorted(items, key=lambda i: i["name"], reverse=True)


def prune(db_file: Path, keep: int) -> None:
    for item in list_backups(db_file)[keep:]:
        (backup_dir(db_file) / item["name"]).unlink(missing_ok=True)


def _check_snapshot(snapshot: Path, key: bytes) -> int:
    """Refuse anything that is not a healthy Horus database this version understands, whose
    stored tokens the key can decrypt. Returns the number of servers in it."""
    conn = sqlite3.connect(snapshot)
    try:
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RestoreError("The backup's database is damaged")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"servers", "schema_version"} <= tables:
            raise RestoreError("This does not look like a Horus backup")
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        if version > len(MIGRATIONS):
            raise RestoreError("This backup was made by a newer version of Horus")
        fernet = Fernet(key)
        tokens = [r[0] for r in conn.execute("SELECT token FROM servers")]
        blank = sum(1 for t in tokens if not t)  # a token-free export: sign in to servers again
        tokens = [t for t in tokens if t]
        for token in tokens:
            try:
                fernet.decrypt(token.encode())
            except InvalidToken:
                raise RestoreError(
                    "The backup's encryption key does not match its server tokens"
                ) from None
        return len(tokens) + blank
    except sqlite3.DatabaseError:
        raise RestoreError("The backup's database could not be read") from None
    finally:
        conn.close()


def _read_source(source: Path, snapshot: Path) -> bytes | None:
    """Put the database from `source` at `snapshot`; return the backup's key if it has one.
    `source` is a full backup (zip) or a token-free export (a bare SQLite file)."""
    with source.open("rb") as f:
        head = f.read(16)
    if head.startswith(b"SQLite format 3"):
        shutil.copyfile(source, snapshot)
        return None
    try:
        with zipfile.ZipFile(source) as z:
            members = set(z.namelist())
            if "horus.db" not in members:
                raise RestoreError("This zip has no database in it")
            info = z.getinfo("horus.db")
            if info.file_size > MAX_UPLOAD:
                raise RestoreError("That backup is too large")
            snapshot.write_bytes(z.read("horus.db"))  # read by name: never extract paths
            return z.read(KEY_FILENAME).strip() if KEY_FILENAME in members else None
    except zipfile.BadZipFile:
        raise RestoreError("This is not a valid backup file") from None


def restore(db_file: Path, name: str) -> dict:
    """Replace the live database (and key, if the backup has one) with a saved backup.

    The current state is backed up first, so a restore can itself be undone. Call with the
    background poller paused. Only a zip from the backups folder is accepted, by name.
    """
    source = find_backup(db_file, name)
    if source is None:
        raise RestoreError("Backup not found")
    return restore_file(db_file, source, name)


def delete_backup(db_file: Path, name: str) -> bool:
    path = find_backup(db_file, name)
    if path is None:
        return False
    path.unlink()
    log.info("backup deleted: %s", name)
    return True


async def save_upload(chunks, db_file: Path) -> Path:
    """Write an uploaded file (an async iterator of bytes) into the backups folder, refusing
    anything over MAX_UPLOAD. The caller removes it when done."""
    folder = backup_dir(db_file)
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="upload-", suffix=".part", dir=folder)
    path, size = Path(name), 0
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise RestoreError("That file is too large to be a Horus backup")
                out.write(chunk)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def restore_file(db_file: Path, source: Path, label: str) -> dict:
    """Restore from any readable backup file (zip or token-free export) after checking it."""
    folder = backup_dir(db_file)
    folder.mkdir(parents=True, exist_ok=True)
    key_file = db_file.parent / KEY_FILENAME
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        snapshot = Path(tmp) / "horus.db"
        backup_key = _read_source(source, snapshot)
        key = backup_key or _load_or_create_key(key_file)
        servers = _check_snapshot(snapshot, key)

        safety = write_full_backup(db_file)  # undo point, made only once the backup checks out
        src = sqlite3.connect(snapshot)
        dest = sqlite3.connect(db_file, timeout=30)
        try:
            src.backup(dest)
        finally:
            dest.close()
            src.close()
        if backup_key and (not key_file.exists() or backup_key != key_file.read_bytes().strip()):
            staged = key_file.with_suffix(".key.new")
            fd = os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(backup_key)
            os.replace(staged, key_file)

    log.warning("restored backup %s: %d server(s); previous state saved as %s",
                label, servers, safety.name)
    conn = sqlite3.connect(db_file)
    try:
        migrate(conn)  # the backup may predate newer migrations
        with conn:  # plays that were "in progress" when it was made are not in progress now
            conn.execute("UPDATE history SET ended_at = last_seen_at WHERE ended_at IS NULL")
    finally:
        conn.close()
    return {"servers": servers, "safety_backup": safety.name}
