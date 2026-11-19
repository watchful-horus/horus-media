"""Deleting backups, restoring from an uploaded file, and starting a fresh install from one."""

import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import backup
from horus_media.crypto import KEY_FILENAME
from horus_media.main import create_app

from .conftest import TOKEN, fake_servers
from .test_auth import PASSWORD, set_up

PLEX = {"type": "plex", "name": "Plex", "url": "http://plex:32400", "token": TOKEN}
HEADERS = {"Content-Type": "application/octet-stream"}


def new_app(tmp_path, monkeypatch, secured=False):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    if secured:
        monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    else:
        monkeypatch.setenv("HORUS_AUTH_DISABLED", "1")
    return create_app(transport=httpx.MockTransport(fake_servers))


@pytest.fixture
def source(tmp_path, monkeypatch):
    """A source install with an admin and one server, returning its full backup and its
    token-free export as bytes."""
    src = tmp_path / "src"
    app = new_app(src, monkeypatch, secured=True)
    admin = TestClient(app, client=("127.0.0.1", 50000))
    set_up(admin)
    assert admin.post("/api/servers", json=PLEX).status_code == 201
    from horus_media.config import db_path
    full = backup.write_full_backup(db_path())
    export = src / "horus-no-tokens.db"  # a bare database file with the server tokens blanked
    backup._copy_db(db_path(), export)
    conn = sqlite3.connect(export)
    with conn:
        conn.execute("UPDATE servers SET token = ''")
    conn.close()
    return full.read_bytes(), export.read_bytes()


def test_delete_a_backup(tmp_path, monkeypatch):
    c = TestClient(new_app(tmp_path, monkeypatch))
    c.post("/api/backups")
    name = c.get("/api/backups").json()["items"][0]["name"]
    assert c.delete(f"/api/backups/{name}").status_code == 204
    assert c.get("/api/backups").json()["items"] == []
    assert c.delete(f"/api/backups/{name}").status_code == 404
    assert c.delete("/api/backups/..%2FhoRus.db").status_code in (404, 405)


def test_upload_a_full_backup(tmp_path, monkeypatch, source):
    full, _ = source
    c = TestClient(new_app(tmp_path / "dest", monkeypatch))
    r = c.post("/api/backups/upload", content=full, headers=HEADERS)
    assert r.status_code == 200 and r.json()["servers"] == 1
    assert [s["name"] for s in c.get("/api/servers").json()] == ["Plex"]
    # The server token still works: the backup's own key came with it.
    assert c.post("/api/servers/1/test").json()["ok"] is True
    assert not list((tmp_path / "dest" / "backups").glob("upload-*"))  # no temp file left


def test_upload_a_token_free_export(tmp_path, monkeypatch, source):
    _, export = source
    c = TestClient(new_app(tmp_path / "dest", monkeypatch))
    r = c.post("/api/backups/upload", content=export, headers=HEADERS)
    assert r.status_code == 200 and r.json()["servers"] == 1
    assert [s["name"] for s in c.get("/api/servers").json()] == ["Plex"]
    assert c.post("/api/servers/1/test").json()["ok"] is False  # tokens were removed


def test_upload_rejects_junk_and_keeps_data(tmp_path, monkeypatch):
    c = TestClient(new_app(tmp_path, monkeypatch))
    c.post("/api/servers", json=PLEX)
    for junk in (b"not a backup", b"SQLite format 3\x00" + b"\x00" * 100):
        r = c.post("/api/backups/upload", content=junk, headers=HEADERS)
        assert r.status_code == 422
    assert len(c.get("/api/servers").json()) == 1
    assert not list((tmp_path / "backups").glob("upload-*"))


def test_upload_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "MAX_UPLOAD", 1000)
    c = TestClient(new_app(tmp_path, monkeypatch))
    r = c.post("/api/backups/upload", content=b"x" * 5000, headers=HEADERS)
    assert r.status_code == 413
    assert not list((tmp_path / "backups").glob("upload-*"))


def test_fresh_install_can_start_from_a_backup(tmp_path, monkeypatch, source):
    full, _ = source
    app = new_app(tmp_path / "fresh", monkeypatch, secured=True)
    c = TestClient(app, client=("127.0.0.1", 50000))
    assert c.get("/api/auth/status").json()["setup_required"] is True
    assert c.post("/api/auth/restore", content=full, headers=HEADERS).status_code == 200
    status = c.get("/api/auth/status").json()
    assert status["setup_required"] is False and status["authenticated"] is False
    # The accounts came with it: the old admin signs in with the old password.
    assert c.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 200
    assert [s["name"] for s in c.get("/api/servers").json()] == ["Plex"]


def test_first_run_restore_works_from_another_computer_without_any_code(tmp_path, monkeypatch, source):
    full, _ = source
    app = new_app(tmp_path / "fresh", monkeypatch, secured=True)
    remote = TestClient(app, client=("10.0.0.5", 1))
    assert remote.post("/api/auth/restore", content=full, headers=HEADERS).status_code == 200


def test_first_run_restore_closes_once_an_admin_exists(tmp_path, monkeypatch, source):
    full, _ = source
    app = new_app(tmp_path / "set", monkeypatch, secured=True)
    c = TestClient(app, client=("127.0.0.1", 50000))
    set_up(c)
    assert c.post("/api/auth/restore", content=full, headers=HEADERS).status_code == 409
    other = TestClient(app, client=("10.0.0.5", 1))  # and not usable by a stranger either
    assert other.post("/api/auth/restore", content=full, headers=HEADERS).status_code == 409


def test_uploading_needs_an_admin(tmp_path, monkeypatch, source):
    full, _ = source
    app = new_app(tmp_path / "dest", monkeypatch, secured=True)
    assert KEY_FILENAME  # (the key file is part of a full backup)
    anon = TestClient(app, client=("10.0.0.5", 1))
    assert anon.post("/api/backups/upload", content=full, headers=HEADERS).status_code == 401


# --- a zip that unpacks to far more than it weighs ------------------------------------------------

def test_a_zip_that_unpacks_large_is_not_read_into_memory(tmp_path, monkeypatch):
    import io
    import tracemalloc
    import zipfile

    c = TestClient(new_app(tmp_path, monkeypatch))
    c.post("/api/servers", json=PLEX)
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("horus.db", b"\0" * (64 * 1024 * 1024))  # 64 MB of zeros, a few KB zipped
    assert len(bomb.getvalue()) < 200_000
    tracemalloc.start()
    r = c.post("/api/backups/upload", content=bomb.getvalue(), headers=HEADERS)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert r.status_code == 422  # unpacked to disk, looked at, and it is not a database
    assert peak < 16 * 1024 * 1024  # a piece at a time, never the whole 64 MB at once
    assert c.get("/api/servers").json()[0]["name"] == "Plex"  # nothing was replaced
    assert not list((tmp_path / "backups").glob("upload-*"))
    assert not [p for p in (tmp_path / "backups").iterdir() if p.is_dir()]  # no unpacked copy left

    monkeypatch.setattr(backup, "MAX_UPLOAD", 4 * 1024 * 1024)  # as if the limit were 4 MB
    r = c.post("/api/backups/upload", content=bomb.getvalue(), headers=HEADERS)
    assert r.status_code == 422 and "too large" in r.json()["detail"]


def test_an_oversized_key_file_is_refused(tmp_path, monkeypatch, source):
    import io
    import zipfile

    full, _ = source
    c = TestClient(new_app(tmp_path / "dest", monkeypatch))
    swollen = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(full)) as good, zipfile.ZipFile(swollen, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("horus.db", good.read("horus.db"))
        z.writestr(KEY_FILENAME, b"A" * (8 * 1024 * 1024))
    r = c.post("/api/backups/upload", content=swollen.getvalue(), headers=HEADERS)
    assert r.status_code == 422 and "key file" in r.json()["detail"]


def test_a_damaged_zip_is_refused_cleanly(tmp_path, monkeypatch, source):
    full, _ = source
    c = TestClient(new_app(tmp_path / "dest", monkeypatch))
    broken = bytearray(full)
    middle = len(broken) // 2
    broken[middle:middle + 40] = b"\xff" * 40  # corrupt the packed database
    r = c.post("/api/backups/upload", content=bytes(broken), headers=HEADERS)
    assert r.status_code == 422
