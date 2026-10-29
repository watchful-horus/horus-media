"""Stopping a stream: admins only, from a signed-in session, and only a stream that is playing now."""

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import auth
from horus_media.main import create_app

from .conftest import TOKEN

PLEX_PLAYING = {"MediaContainer": {"size": 1, "Metadata": [
    {"type": "movie", "title": "A Film", "sessionKey": "17", "duration": 1000, "viewOffset": 100,
     "User": {"title": "bob"}, "Player": {"title": "Phone", "platform": "Android", "state": "playing"},
     "Session": {"id": "plex-session-abc", "bandwidth": 3000}}]}}
EMBY_PLAYING = [{"Id": "emby1", "UserName": "carol", "DeviceName": "Chrome", "Client": "Emby Web",
                 "NowPlayingItem": {"Id": "item9", "Name": "Ep 1", "Type": "Episode"},
                 "PlayState": {"PlayMethod": "DirectPlay"}}]


class Recorder:
    """A fake Plex and Emby that remembers every call that is not a plain read."""

    def __init__(self, refuse: int | None = None):
        self.calls: list[tuple[str, str, dict]] = []
        self.refuse = refuse

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.headers.get("x-plex-token") != TOKEN and request.headers.get("x-emby-token") != TOKEN:
            return httpx.Response(401)
        if path == "/identity":
            return httpx.Response(200, json={"MediaContainer": {"machineIdentifier": "x", "version": "1"}})
        if path == "/System/Info/Public":
            return httpx.Response(200, json={"ServerName": "E", "Version": "4"})
        if path == "/status/sessions":
            return httpx.Response(200, json=PLEX_PLAYING)
        if path == "/Sessions":
            return httpx.Response(200, json=EMBY_PLAYING)
        if path == "/status/sessions/terminate" or request.method == "POST":
            self.calls.append((request.method, path, dict(request.url.params)))
            return httpx.Response(self.refuse or 200)
        return httpx.Response(404)


def add(c: TestClient, kind: str) -> int:
    url = "http://plex.test:32400" if kind == "plex" else "http://emby.test:8096"
    r = c.post("/api/servers", json={"type": kind, "name": kind, "url": url, "token": TOKEN})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


@pytest.fixture
def fake():
    return Recorder()


def app(fake: Recorder) -> TestClient:
    return TestClient(create_app(transport=httpx.MockTransport(fake)))


def test_plex_stream_is_stopped_by_its_own_session_id(client, tmp_path, fake):
    c = app(fake)
    sid = add(c, "plex")
    r = c.post("/api/activity/stop", json={"server_id": sid, "session_key": "17", "reason": " Time for bed "})
    assert r.status_code == 204
    assert fake.calls == [("GET", "/status/sessions/terminate",
                           {"sessionId": "plex-session-abc", "reason": "Time for bed"})]


def test_emby_stream_is_stopped_and_the_message_is_best_effort(client, tmp_path, fake):
    c = app(fake)
    sid = add(c, "emby")
    key = "emby1:item9"
    assert c.post("/api/activity/stop", json={"server_id": sid, "session_key": key, "reason": "Bye"}).status_code == 204
    assert [(m, p) for m, p, _ in fake.calls] == [("POST", "/Sessions/emby1/Message"),
                                                  ("POST", "/Sessions/emby1/Playing/Stop")]
    fake.calls.clear()
    assert c.post("/api/activity/stop", json={"server_id": sid, "session_key": key}).status_code == 204
    assert [p for _, p, _ in fake.calls] == ["/Sessions/emby1/Playing/Stop"]  # no message asked for


def test_only_a_stream_playing_right_now_can_be_stopped(client, tmp_path, fake):
    c = app(fake)
    sid = add(c, "plex")
    r = c.post("/api/activity/stop", json={"server_id": sid, "session_key": "not-playing"})
    assert r.status_code == 404 and "already ended" in r.json()["detail"]
    assert c.post("/api/activity/stop", json={"server_id": 999, "session_key": "17"}).status_code == 404
    assert fake.calls == []  # nothing was sent to the server


def test_a_refused_stop_explains_itself(client, tmp_path):
    fake = Recorder(refuse=403)
    c = app(fake)
    sid = add(c, "plex")
    r = c.post("/api/activity/stop", json={"server_id": sid, "session_key": "17"})
    assert r.status_code == 502 and "Plex Pass" in r.json()["detail"]


def test_the_server_side_session_id_is_never_sent_to_the_browser(client, tmp_path, fake):
    c = app(fake)
    add(c, "plex")
    body = c.get("/api/activity").json()
    assert "plex-session-abc" not in str(body) and "native_id" not in str(body)


def test_only_admins_may_stop_streams(client):
    assert auth.role_allows("admin", "POST", "/api/activity/stop")
    assert not auth.role_allows("user", "POST", "/api/activity/stop")
    assert auth.role_allows("user", "GET", "/api/activity")  # watching is still allowed


def test_a_control_key_can_never_stop_a_stream(tmp_path, monkeypatch, fake):
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    c = TestClient(create_app(transport=httpx.MockTransport(fake)), client=("127.0.0.1", 50000))
    assert c.post("/api/auth/setup", json={"username": "owner", "password": "correct horse battery"}).status_code == 200
    sid = add(c, "plex")
    assert c.put("/api/settings", json={"control_keys_enabled": True}).status_code == 200
    key = c.post("/api/keys", json={"name": "k", "scope": "control", "expires_in_days": 7}).json()["key"]
    other = TestClient(create_app(transport=httpx.MockTransport(fake)), client=("127.0.0.1", 50001))
    r = other.post("/api/activity/stop", json={"server_id": sid, "session_key": "17"},
                   headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403 and "signed-in admin" in r.json()["detail"]
    assert fake.calls == []
    assert c.post("/api/activity/stop", json={"server_id": sid, "session_key": "17"}).status_code == 204
