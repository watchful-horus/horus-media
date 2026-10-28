import logging
import sqlite3

from horus_media.crypto import TokenCipher
from horus_media.db import connect, migrate

from .conftest import TOKEN

PLEX = {"type": "plex", "name": "Plex", "url": "http://plex:32400/", "token": TOKEN}


def test_add_list_delete_never_returns_token(client):
    r = client.post("/api/servers", json=PLEX)
    assert r.status_code == 201
    assert r.json()["url"] == "http://plex:32400"
    sid = r.json()["id"]
    listing = client.get("/api/servers")
    for body in (r.text, listing.text):
        assert TOKEN not in body
        assert "token" not in body
    assert len(listing.json()) == 1
    assert client.delete(f"/api/servers/{sid}").status_code == 204
    assert client.delete(f"/api/servers/{sid}").status_code == 404
    assert client.get("/api/servers").json() == []


def test_rename_server(client):
    sid = client.post("/api/servers", json=PLEX).json()["id"]
    r = client.patch(f"/api/servers/{sid}", json={"name": "  Living room  "})
    assert r.status_code == 200
    assert r.json()["name"] == "Living room"
    assert "token" not in r.text
    assert client.get("/api/servers").json()[0]["name"] == "Living room"
    assert client.patch(f"/api/servers/{sid}", json={"name": "   "}).status_code == 422
    assert client.patch("/api/servers/999", json={"name": "x"}).status_code == 404


def test_token_encrypted_in_db(client, tmp_path):
    client.post("/api/servers", json=PLEX)
    raw = sqlite3.connect(tmp_path / "horus.db").execute("SELECT token FROM servers").fetchone()[0]
    assert raw != TOKEN
    assert TOKEN not in (tmp_path / "horus.db").read_bytes().decode("latin-1")
    assert TokenCipher.for_db(tmp_path / "horus.db").decrypt(raw) == TOKEN


def test_test_saved_server(client):
    sid = client.post("/api/servers", json=PLEX).json()["id"]
    r = client.post(f"/api/servers/{sid}/test")
    assert r.json() == {"ok": True, "server_name": "Plex", "version": "1.41.0", "error": None}
    assert TOKEN not in r.text
    assert client.post("/api/servers/999/test").status_code == 404


def test_test_unsaved_ok_and_bad_token(client):
    emby = {"type": "emby", "url": "http://emby:8096", "token": TOKEN}
    assert client.post("/api/servers/test", json=emby).json()["server_name"] == "EmbyBox"
    bad = client.post("/api/servers/test", json={**emby, "token": "nope"})
    assert bad.json()["ok"] is False
    assert "nope" not in bad.text


def test_invalid_type_rejected(client):
    assert client.post("/api/servers", json={**PLEX, "type": "jellyfin"}).status_code == 422


def test_token_never_logged(client, caplog):
    caplog.set_level(logging.DEBUG)
    sid = client.post("/api/servers", json=PLEX).json()["id"]
    client.post(f"/api/servers/{sid}/test")
    client.post("/api/servers/test", json={**PLEX, "token": "other-secret-xyz"})
    assert TOKEN not in caplog.text
    assert "other-secret-xyz" not in caplog.text


def test_migrations_idempotent(tmp_path):
    conn = connect(tmp_path / "a.db")
    latest = migrate(conn)
    assert migrate(conn) == latest
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == latest


def test_activity_merges_servers_and_isolates_failures(client):
    client.post("/api/servers", json=PLEX)
    client.post("/api/servers", json={"type": "emby", "name": "Emby",
                                      "url": "http://emby:8096", "token": TOKEN})
    client.post("/api/servers", json={**PLEX, "name": "BadKey", "token": "wrong"})
    r = client.get("/api/activity")
    assert r.status_code == 200
    data = r.json()
    assert [s["ok"] for s in data["servers"]] == [True, True, False]
    assert data["servers"][2]["error"].startswith("Authentication failed")
    assert len(data["sessions"]) == 3  # 2 Plex + 1 Emby
    assert {s["server"] for s in data["sessions"]} == {"Plex", "Emby"}
    assert TOKEN not in r.text and "wrong" not in r.text


def test_activity_empty(client):
    assert client.get("/api/activity").json() == {"servers": [], "sessions": []}


def test_ui_served_without_shadowing_api(client):
    assert client.get("/api/health").json()["status"] == "ok"
    r = client.get("/")
    if r.status_code == 200:  # only when the web UI has been built
        assert "<div id=\"app\">" in r.text


def test_poster_proxy(tmp_path, monkeypatch):
    import httpx
    from fastapi.testclient import TestClient

    from horus_media.main import create_app

    def handler(request):
        assert request.headers["x-emby-token"] == TOKEN
        return httpx.Response(200, content=b"IMG", headers={"content-type": "image/jpeg"})

    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    c = TestClient(create_app(transport=httpx.MockTransport(handler)))
    sid = c.post("/api/servers", json={"type": "emby", "name": "E", "url": "http://emby",
                                       "token": TOKEN}).json()["id"]
    r = c.get(f"/api/servers/{sid}/poster", params={"path": "/Items/abc/Images/Primary"})
    assert r.status_code == 200 and r.content == b"IMG"
    assert r.headers["content-type"] == "image/jpeg"
    assert TOKEN not in str(r.headers)
    assert c.get(f"/api/servers/{sid}/poster", params={"path": "/System/Info"}).status_code == 404
    assert c.get("/api/servers/99/poster", params={"path": "/Items/a/Images/Primary"}
                 ).status_code == 404
