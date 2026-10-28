from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media.main import create_app

from .conftest import TOKEN

VIEWED = int(datetime(2026, 3, 1, 20, 0, tzinfo=UTC).timestamp())

PLEX_ACCOUNTS = {"MediaContainer": {"Account": [{"id": 7, "name": "alice"}]}}
PLEX_DEVICES = {"MediaContainer": {"Device": [{"id": 3, "name": "Living Room", "platform": "Roku"}]}}
PLEX_HISTORY = {"MediaContainer": {"totalSize": 2, "Metadata": [
    {"historyKey": "/status/sessions/history/1", "ratingKey": "10", "type": "episode",
     "title": "Pilot", "grandparentTitle": "Some Show", "viewedAt": VIEWED,
     "accountID": 7, "deviceID": 3},
    {"historyKey": "/status/sessions/history/2", "ratingKey": "11", "type": "movie",
     "title": "A Film", "viewedAt": VIEWED + 3600, "accountID": 99, "deviceID": 42},
]}}

EMBY_USERS = [{"name": "carol", "id": "u1", "in_list": True}]
EMBY_PLAYS = [
    {"rowid": 5, "DateCreated": "2026-03-02T19:00:00.0000000Z", "ItemName": "Ep 1",
     "ItemType": "Episode", "PlaybackMethod": "DirectPlay", "ClientName": "Emby Web",
     "DeviceName": "Chrome", "PlayDuration": 1800, "user_name": "carol"},
    {"rowid": 6, "DateCreated": "2026-03-03T19:00:00", "ItemName": "Film",
     "ItemType": "Movie", "PlaybackMethod": "Transcode (v:h264)", "PlayDuration": 600},
]


def make(tmp_path, monkeypatch, handler):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    return TestClient(create_app(transport=httpx.MockTransport(handler)))


def plex_handler(request):
    path = request.url.path
    if path == "/accounts":
        return httpx.Response(200, json=PLEX_ACCOUNTS)
    if path == "/devices":
        return httpx.Response(200, json=PLEX_DEVICES)
    if path == "/status/sessions/history/all":
        assert request.method == "GET"
        return httpx.Response(200, json=PLEX_HISTORY)
    return httpx.Response(404)


def emby_handler(request):
    path = request.url.path
    if path == "/user_usage_stats/user_list":
        return httpx.Response(200, json=EMBY_USERS)
    if path == "/user_usage_stats/UserPlaylist":
        assert request.url.params["aggregate_data"] == "false"
        return httpx.Response(200, json=EMBY_PLAYS)
    return httpx.Response(404)


def add(client, kind):
    r = client.post("/api/servers", json={"type": kind, "name": kind, "url": "http://s",
                                          "token": TOKEN})
    return r.json()["id"]


def plays(client):
    return client.get("/api/v1/history?from=2026-01-01&to=2026-12-31&limit=100").json()


def test_plex_import_adds_plays_with_names(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, plex_handler)
    sid = add(c, "plex")
    r = c.post(f"/api/servers/{sid}/import-history")
    assert r.json() == {"found": 2, "added": 2, "skipped": 0}
    rows = {p["title"]: p for p in plays(c)}
    assert rows["Some Show - Pilot"]["user"] == "alice"
    assert rows["Some Show - Pilot"]["platform"] == "Roku"
    assert rows["A Film"]["user"] == "Unknown"  # account not in the list: still counted


def test_import_twice_does_not_duplicate(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, plex_handler)
    sid = add(c, "plex")
    c.post(f"/api/servers/{sid}/import-history")
    again = c.post(f"/api/servers/{sid}/import-history").json()
    assert again["added"] == 0 and again["skipped"] == 2
    assert len(plays(c)) == 2


def test_emby_import_reads_playback_reporting(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, emby_handler)
    sid = add(c, "emby")
    assert c.post(f"/api/servers/{sid}/import-history").json()["added"] == 2
    rows = {p["title"]: p for p in plays(c)}
    assert rows["Ep 1"]["play_method"] == "direct_play"
    assert rows["Ep 1"]["duration_seconds"] == 1800
    assert rows["Film"]["play_method"] == "transcode"
    assert rows["Film"]["user"] == "carol"  # falls back to the plugin's user name


def test_emby_without_plugin_explains(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, lambda r: httpx.Response(404))
    sid = add(c, "emby")
    r = c.post(f"/api/servers/{sid}/import-history")
    assert r.status_code == 422 and "Playback Reporting" in r.json()["detail"]


def test_live_recorded_play_is_not_counted_twice(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, plex_handler)
    sid = add(c, "plex")
    from horus_media.config import db_path
    from horus_media.db import connect
    conn = connect(db_path())
    started = datetime.fromtimestamp(VIEWED + 30, UTC).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO history (server_id, session_key, user, title, media_type, device, "
            "platform, play_method, started_at, last_seen_at, ended_at) "
            "VALUES (?, 'k', 'alice', 'Some Show - Pilot', 'episode', 'd', 'p', 'direct_play', "
            "?, ?, ?)", (sid, started, started, started))
    conn.close()
    result = c.post(f"/api/servers/{sid}/import-history").json()
    assert result["added"] == 1 and result["skipped"] == 1


def test_unknown_server_is_404(tmp_path, monkeypatch):
    c = make(tmp_path, monkeypatch, plex_handler)
    assert c.post("/api/servers/99/import-history").status_code == 404


@pytest.mark.parametrize("kind", ["plex", "emby"])
def test_import_only_issues_get_requests(tmp_path, monkeypatch, kind):
    methods = []

    def handler(request):
        methods.append(request.method)
        return (plex_handler if kind == "plex" else emby_handler)(request)

    c = make(tmp_path, monkeypatch, handler)
    c.post(f"/api/servers/{add(c, kind)}/import-history")
    assert methods and set(methods) == {"GET"}
