import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media.main import create_app

from .conftest import fake_servers

ACCOUNT_TOKEN = "ACCOUNT-TOKEN-zzz"
SERVER_TOKEN = "SERVER-TOKEN-yyy"
LOCAL = "http://192.168.1.5:32400"
REMOTE = "https://1-2-3-4.abc.plex.direct:32400"

RESOURCES = [
    {"name": "Home Plex", "clientIdentifier": "m1", "provides": "server", "owned": True,
     "accessToken": SERVER_TOKEN,
     "connections": [
         {"uri": REMOTE, "local": False, "relay": False},
         {"uri": "https://relay.plex.direct:8443", "local": False, "relay": True},
         {"uri": LOCAL, "local": True, "relay": False},
     ]},
    {"name": "Friend Plex", "clientIdentifier": "m2", "provides": "server", "owned": False,
     "accessToken": "FRIEND-TOKEN", "connections": [{"uri": "https://f.plex.direct:32400"}]},
    {"name": "A Phone", "clientIdentifier": "p1", "provides": "player", "connections": []},
]


class FakePlexTv:
    def __init__(self):
        self.authorised = False
        self.seen_tokens: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host != "plex.tv":
            return fake_servers(request)
        path = request.url.path
        if request.method == "POST" and path == "/api/v2/pins":
            return httpx.Response(201, json={"id": 77, "code": "ABCD"})
        if path == "/api/v2/pins/77":
            assert request.url.params["code"] == "ABCD"
            return httpx.Response(200, json={"authToken": ACCOUNT_TOKEN if self.authorised else None})
        if path == "/api/v2/resources":
            self.seen_tokens.append(request.headers.get("x-plex-token", ""))
            return httpx.Response(200, json=RESOURCES)
        return httpx.Response(404)


@pytest.fixture
def plextv():
    return FakePlexTv()


@pytest.fixture
def api(tmp_path, monkeypatch, plextv):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    return TestClient(create_app(transport=httpx.MockTransport(plextv)))


def test_flow_pending_then_done_then_add(api, plextv):
    start = api.post("/api/plex/link").json()
    assert start["pin_id"] == 77
    assert "app.plex.tv/auth" in start["auth_url"] and "code=ABCD" in start["auth_url"]

    assert api.get("/api/plex/link/77").json() == {"status": "pending", "servers": []}

    plextv.authorised = True
    done = api.get("/api/plex/link/77")
    body = done.json()
    assert body["status"] == "done"
    assert [(s["name"], s["owned"]) for s in body["servers"]] == [
        ("Home Plex", True), ("Friend Plex", False)]  # players filtered out
    urls = [c["url"] for c in body["servers"][0]["connections"]]
    assert urls == [LOCAL, REMOTE]  # local first, relay dropped
    assert ACCOUNT_TOKEN not in done.text and SERVER_TOKEN not in done.text
    assert plextv.seen_tokens == [ACCOUNT_TOKEN]

    added = api.post("/api/plex/link/77/add", json={"resource_id": "m1", "url": LOCAL})
    assert added.status_code == 201
    assert added.json()["name"] == "Home Plex"
    assert SERVER_TOKEN not in added.text and "token" not in added.text
    # Claiming one server leaves the others on the same sign-in available.
    left = api.get("/api/plex/link/77").json()
    assert [x["name"] for x in left["servers"]] == ["Friend Plex"]
    second = api.post("/api/plex/link/77/add",
                      json={"resource_id": "m2", "url": "https://f.plex.direct:32400"})
    assert second.status_code == 201
    assert api.get("/api/plex/link/77").json()["status"] == "expired"  # all claimed

    sid = added.json()["id"]
    assert api.get("/api/servers").json()[0]["url"] == LOCAL
    # The stored token is the per-server token Plex handed out, not the account token.
    from horus_media.config import db_path
    from horus_media.crypto import TokenCipher
    from horus_media.db import connect
    conn = connect(db_path())
    raw = conn.execute("SELECT token FROM servers WHERE id = ?", (sid,)).fetchone()[0]
    assert TokenCipher.for_db(db_path()).decrypt(raw) == SERVER_TOKEN


def test_rejects_address_not_advertised_by_plex(api, plextv):
    api.post("/api/plex/link")
    plextv.authorised = True
    api.get("/api/plex/link/77")
    r = api.post("/api/plex/link/77/add", json={"resource_id": "m1", "url": "http://evil.example"})
    assert r.status_code == 422
    assert api.get("/api/servers").json() == []


def test_add_before_sign_in_or_unknown_pin(api, plextv):
    api.post("/api/plex/link")
    assert api.post("/api/plex/link/77/add", json={"resource_id": "m1", "url": LOCAL}).status_code == 404
    assert api.post("/api/plex/link/5/add", json={"resource_id": "m1", "url": LOCAL}).status_code == 404
    assert api.get("/api/plex/link/5").json()["status"] == "expired"


def test_plex_tv_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    down = httpx.MockTransport(lambda r: httpx.Response(503))
    r = TestClient(create_app(transport=down)).post("/api/plex/link")
    assert r.status_code == 502
    assert r.json()["detail"] == "Could not reach plex.tv"


def test_client_id_stable_and_tokens_not_logged(api, plextv, caplog):
    caplog.set_level(logging.DEBUG)
    api.post("/api/plex/link")
    plextv.authorised = True
    api.get("/api/plex/link/77")
    api.post("/api/plex/link/77/add", json={"resource_id": "m1", "url": LOCAL})
    assert ACCOUNT_TOKEN not in caplog.text and SERVER_TOKEN not in caplog.text
    from horus_media.config import db_path
    from horus_media.db import connect
    from horus_media.plex_auth import client_identifier
    conn = connect(db_path())
    assert client_identifier(conn) == client_identifier(conn)


def test_pending_pins_expire(tmp_path, monkeypatch, plextv):
    from horus_media.plex_auth import PIN_TTL_SECONDS, PlexLinker
    now = [0.0]
    linker = PlexLinker(httpx.MockTransport(plextv), clock=lambda: now[0])
    pin_id, _ = linker.start("cid")
    now[0] = PIN_TTL_SECONDS + 1
    assert linker.poll("cid", pin_id) == ("expired", [])
