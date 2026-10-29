import sqlite3
import zipfile
from datetime import UTC, datetime

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from horus_media import backup
from horus_media.crypto import KEY_FILENAME, TokenCipher
from horus_media.db import connect, migrate
from horus_media.main import create_app
from horus_media.migrations import MIGRATIONS

from .conftest import TOKEN, fake_servers

PLEX = {"type": "plex", "name": "Plex", "url": "http://plex:32400", "token": TOKEN}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    return TestClient(create_app(transport=httpx.MockTransport(fake_servers))), tmp_path


def make_zip(folder, name, build_db, key: bytes | None):
    """A hand-built backup: build_db(conn) fills a fresh database."""
    folder.mkdir(parents=True, exist_ok=True)
    db = folder / "x.db"
    conn = sqlite3.connect(db)
    build_db(conn)
    conn.commit()
    conn.close()
    with zipfile.ZipFile(folder / name, "w") as z:
        z.write(db, "horus.db")
        if key:
            z.writestr(KEY_FILENAME, key)
    db.unlink()


def schema(conn, version=None):
    migrate(conn)
    if version is not None:
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))


def test_restore_round_trip_with_safety_backup(app):
    client, tmp = app
    keep = client.post("/api/servers", json=PLEX).json()["id"]
    assert client.post("/api/backups").status_code == 201
    name = client.get("/api/backups").json()["items"][0]["name"]

    client.delete(f"/api/servers/{keep}")
    client.post("/api/servers", json={**PLEX, "name": "Other"})
    client.put("/api/settings", json={"retention_days": 45})

    r = client.post(f"/api/backups/{name}/restore")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["servers"] == 1 and body["safety_backup"].startswith("horus-")

    servers = client.get("/api/servers").json()
    assert [s["name"] for s in servers] == ["Plex"]
    assert client.get("/api/settings").json()["settings"]["retention_days"] == 0
    assert client.post(f"/api/servers/{servers[0]['id']}/test").json()["ok"] is True  # token works
    assert TOKEN not in r.text

    # The state from just before the restore was saved, so the restore can be undone.
    safety = tmp / "backups" / body["safety_backup"]
    with zipfile.ZipFile(safety) as z:
        z.extract("horus.db", tmp / "peek")
    names = [r[0] for r in sqlite3.connect(tmp / "peek" / "horus.db").execute(
        "SELECT name FROM servers")]
    assert names == ["Other"]


def test_restore_brings_back_the_backups_key(app):
    client, tmp = app
    db_file = tmp / "horus.db"
    client.get("/api/servers")  # creates db + key
    other_key = Fernet.generate_key()

    def build(conn):
        schema(conn)
        conn.execute(
            "INSERT INTO servers (type, name, url, token, created_at) VALUES (?,?,?,?,?)",
            ("plex", "FromBackup", "http://plex:32400",
             Fernet(other_key).encrypt(TOKEN.encode()).decode(), "2026-01-01"))

    make_zip(backup.backup_dir(db_file), "horus-20260101-000000.zip", build, other_key)
    r = client.post("/api/backups/horus-20260101-000000.zip/restore")
    assert r.status_code == 200, r.text
    assert (tmp / "horus.key").read_bytes().strip() == other_key
    sid = client.get("/api/servers").json()[0]["id"]
    assert client.post(f"/api/servers/{sid}/test").json()["ok"] is True


def test_mismatched_key_is_refused_and_nothing_changes(app):
    client, tmp = app
    db_file = tmp / "horus.db"
    client.post("/api/servers", json=PLEX)
    before = (tmp / "horus.key").read_bytes()

    def build(conn):  # tokens encrypted with a key that is not in the zip and not ours
        schema(conn)
        conn.execute(
            "INSERT INTO servers (type, name, url, token, created_at) VALUES (?,?,?,?,?)",
            ("plex", "X", "http://x", Fernet(Fernet.generate_key()).encrypt(b"t").decode(), "x"))

    make_zip(backup.backup_dir(db_file), "horus-20260102-000000.zip", build, None)
    r = client.post("/api/backups/horus-20260102-000000.zip/restore")
    assert r.status_code == 422 and "key" in r.json()["detail"]
    assert [s["name"] for s in client.get("/api/servers").json()] == ["Plex"]
    assert (tmp / "horus.key").read_bytes() == before
    assert len(backup.list_backups(db_file)) == 1  # no safety backup for a refused restore


def test_newer_schema_refused(app):
    client, tmp = app
    client.get("/api/servers")
    make_zip(backup.backup_dir(tmp / "horus.db"), "horus-20260103-000000.zip",
             lambda c: schema(c, version=len(MIGRATIONS) + 5), None)
    r = client.post("/api/backups/horus-20260103-000000.zip/restore")
    assert r.status_code == 422 and "newer version" in r.json()["detail"]


def test_older_schema_is_migrated_forward(app):
    client, tmp = app
    client.get("/api/servers")

    def build(conn):  # only the first migration, as an old install would have
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.executescript(MIGRATIONS[0])
        conn.execute("INSERT INTO schema_version (version) VALUES (1)")

    make_zip(backup.backup_dir(tmp / "horus.db"), "horus-20260104-000000.zip", build, None)
    assert client.post("/api/backups/horus-20260104-000000.zip/restore").status_code == 200
    version = sqlite3.connect(tmp / "horus.db").execute(
        "SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert version == len(MIGRATIONS)
    assert client.get("/api/stats").status_code == 200  # newer tables exist again


def test_open_plays_are_closed_after_restore(app):
    client, tmp = app
    client.get("/api/servers")
    key = Fernet.generate_key()

    def build(conn):
        schema(conn)
        conn.execute(
            "INSERT INTO servers (type, name, url, token, created_at) VALUES "
            "('plex','P','http://p',?,'x')", (Fernet(key).encrypt(b"t").decode(),))
        conn.execute(
            "INSERT INTO history (server_id, session_key, user, title, media_type, device, "
            "platform, play_method, started_at, last_seen_at) "
            "VALUES (1,'k','u','t','movie','d','p','direct_play','2026-01-01','2026-01-01')")

    make_zip(backup.backup_dir(tmp / "horus.db"), "horus-20260105-000000.zip", build, key)
    assert client.post("/api/backups/horus-20260105-000000.zip/restore").status_code == 200
    ended = sqlite3.connect(tmp / "horus.db").execute("SELECT ended_at FROM history").fetchone()[0]
    assert ended == "2026-01-01"


def test_damaged_and_unsafe_inputs(app):
    client, tmp = app
    client.get("/api/servers")
    folder = backup.backup_dir(tmp / "horus.db")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "horus-20260106-000000.zip").write_bytes(b"not a zip")
    assert client.post("/api/backups/horus-20260106-000000.zip/restore").status_code == 422

    with zipfile.ZipFile(folder / "horus-20260107-000000.zip", "w") as z:
        z.writestr("readme.txt", "no database")
    assert client.post("/api/backups/horus-20260107-000000.zip/restore").status_code == 422

    (folder / "horus-20260108-000000.zip").write_bytes(b"x")
    for bad in ("..%2F..%2Fhorus.db", "horus.db", "evil.zip", "horus-20260109-000000.zip"):
        assert client.post(f"/api/backups/{bad}/restore").status_code in (404, 405), bad


def test_restore_function_rejects_traversal_directly(tmp_path):
    db = tmp_path / "horus.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    TokenCipher.for_db(db)
    with pytest.raises(backup.RestoreError):
        backup.restore(db, "../horus.db")
    assert backup.NAME_RE.fullmatch(datetime(2026, 1, 1, tzinfo=UTC).strftime("horus-%Y%m%d-%H%M%S.zip"))


def test_find_backup_only_returns_files_in_the_backups_folder(tmp_path):
    db = tmp_path / "horus.db"
    folder = backup.backup_dir(db)
    folder.mkdir()
    (folder / "horus-20260101-000000.zip").write_bytes(b"x")
    (tmp_path / "horus-20260102-000000.zip").write_bytes(b"x")  # next to the database, not in backups
    assert backup.find_backup(db, "horus-20260101-000000.zip").parent == folder
    for name in ("horus-20260102-000000.zip", "../horus-20260102-000000.zip", "..\\horus.zip",
                 "/etc/passwd", "horus-20260101-000000.zip/", "horus-1.zip"):
        assert backup.find_backup(db, name) is None
