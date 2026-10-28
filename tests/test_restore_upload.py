"""Deleting backups, restoring from an uploaded file, and starting a fresh install from one."""

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
    export = backup.write_safe_export(db_path(), src)
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
