"""Jellyfin: the key header, sessions, stop, history import and the servers table rebuild.
Stand-in responses only, shaped after Jellyfin's own API description."""

import sqlite3
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from horus_media.clients import JellyfinClient, make_client
from horus_media.db import connect, migrate
from horus_media.main import create_app
from horus_media.migrations import MIGRATIONS

from .conftest import TOKEN

HEADER = f'MediaBrowser Token="{TOKEN}"'
USER = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
PLAYING = [
    {"Id": "jf1", "UserName": "erin", "DeviceName": "Firefox", "Client": "Jellyfin Web",
     "RemoteEndPoint": "192.168.1.20",
     "NowPlayingItem": {
         "Id": "aaaabbbbccccddddeeeeffff00001111", "Name": "Pilot", "SeriesName": "Some Show",
         "Type": "Episode", "ParentIndexNumber": 2, "IndexNumber": 3,
         "SeriesId": "9999bbbbccccddddeeeeffff00001111", "SeriesPrimaryImageTag": "t",
         "RunTimeTicks": 10_000_000_000, "Container": "mkv",
         "MediaStreams": [
             {"Type": "Video", "Index": 0, "Codec": "hevc", "Height": 2160, "BitRate": 20_000_000,
              "RealFrameRate": 24.0},
             {"Type": "Audio", "Index": 1, "Codec": "eac3", "Channels": 6, "Language": "eng",
              "BitRate": 640_000}]},
     "PlayState": {"PlayMethod": "Transcode", "PositionTicks": 2_500_000_000, "IsPaused": False,
                   "AudioStreamIndex": 1},
     "TranscodingInfo": {"VideoCodec": "h264", "AudioCodec": "aac", "Container": "ts",
                         "IsVideoDirect": False, "IsAudioDirect": False, "Bitrate": 8_000_000,
                         "Framerate": 3.0, "CompletionPercentage": 30.0,
                         "TranscodeReasons": ["VideoCodecNotSupported",
                                              "VideoRangeTypeNotSupported"]}},
    {"Id": "jf2", "UserName": "idle", "DeviceName": "TV", "Client": "Jellyfin Android TV"},
]


class Jellyfin:
    """A stand-in Jellyfin that only accepts the documented key header."""

    def __init__(self, plugin: bool = True):
        self.plugin = plugin
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.headers.get("authorization") != HEADER or "x-emby-token" in request.headers:
            return httpx.Response(401)
        if request.method != "GET":
            self.calls.append((request.method, path))
            return httpx.Response(204)
        if path == "/System/Info":
            return httpx.Response(200, json={"ServerName": "JellyBox", "Version": "10.10.3"})
        if path == "/Sessions":
            return httpx.Response(200, json=PLAYING)
        if not self.plugin:
            return httpx.Response(404)
        if path == "/user_usage_stats/user_list":
            return httpx.Response(200, json=[{"name": "erin", "id": USER, "in_list": False}])
        if path == "/user_usage_stats/type_filter_list":
            return httpx.Response(200, json=["Movie", "Episode"])
        if path == "/user_usage_stats/PlayActivity":
            assert request.url.params["dataType"] == "count"
            assert request.url.params["filter"] == "Movie,Episode"
            return httpx.Response(200, json=[
                {"user_id": "labels_user", "user_name": "labels_user",
                 "user_usage": {"2026-03-01": 0, "2026-03-02": 0}},
                {"user_id": USER, "user_name": "erin",
                 "user_usage": {"2026-03-01": 0, "2026-03-02": 2}}])
        if path == f"/user_usage_stats/{USER}/2026-03-02/GetItems":
            return httpx.Response(200, json=[
                {"Time": "3:05 PM", "Id": "i1", "Name": "A Film", "Type": "Movie",
                 "Client": "Jellyfin Web", "Method": "DirectPlay", "Device": "Firefox",
                 "Duration": "5400", "RowId": "7"},
                {"Time": "afternoon", "Id": "i2", "Name": "Some Show - Pilot", "Type": "Episode",
                 "Client": "Jellyfin Android TV", "Method": "Transcode (v:h264 a:aac)",
                 "Device": "TV", "Duration": "1200", "RowId": "8"}])
        return httpx.Response(404)


def client_for(fake) -> JellyfinClient:
    made = make_client("jellyfin", "J", "http://jellyfin.test:8096", TOKEN,
                       transport=httpx.MockTransport(fake))
    assert isinstance(made, JellyfinClient)
    return made


def app(fake, tmp_path, monkeypatch) -> tuple[TestClient, int]:
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    c = TestClient(create_app(transport=httpx.MockTransport(fake)))
    r = c.post("/api/servers", json={"type": "jellyfin", "name": "Jelly",
                                     "url": "http://jellyfin.test:8096", "token": TOKEN})
    assert r.status_code in (200, 201), r.text
    return c, r.json()["id"]


def test_key_goes_in_the_authorization_header_and_a_wrong_key_fails():
    r = client_for(Jellyfin()).test_connection()
    assert (r.server_name, r.version) == ("JellyBox", "10.10.3")
    wrong = make_client("jellyfin", "J", "http://j", "nope", transport=httpx.MockTransport(Jellyfin()))
    try:
        wrong.test_connection()
    except httpx.HTTPStatusError as e:
        assert e.response.status_code == 401
    else:
        raise AssertionError("a wrong key passed the test")


def test_session_is_read_like_the_card_shows_it():
    sessions = client_for(Jellyfin()).get_sessions()
    assert len(sessions) == 1  # the idle device is not a stream
    s = sessions[0]
    assert (s.user, s.title, s.episode) == ("erin", "Some Show - Pilot", "S2:E3")
    assert (s.play_method, s.state, s.progress_percent) == ("transcode", "playing", 25.0)
    assert s.bandwidth_kbps == 8000
    assert s.poster == "/Items/9999bbbbccccddddeeeeffff00001111/Images/Primary"
    assert s.transcode_reason == "Video codec not supported, HDR not supported"
    assert "HEVC" in s.video and "H264" in s.video
    assert s.location == "LAN: 192.168.1.20"


def test_no_speed_is_claimed_so_a_waiting_conversion_is_not_called_slow():
    s = client_for(Jellyfin()).get_sessions()[0]  # 3 frames a second against a 24 fps file
    assert s.transcode_speed is None
    assert s.transcode_throttled is False


def test_add_show_and_stop_through_the_app(tmp_path, monkeypatch):
    fake = Jellyfin()
    c, sid = app(fake, tmp_path, monkeypatch)
    assert c.get("/api/servers").json()[0]["type"] == "jellyfin"
    assert c.post(f"/api/servers/{sid}/test").json()["server_name"] == "JellyBox"
    data = c.get("/api/activity").json()
    assert [s["user"] for s in data["sessions"]] == ["erin"]
    key = data["sessions"][0]["session_key"]
    r = c.post("/api/activity/stop", json={"server_id": sid, "session_key": key, "reason": "bye"})
    assert r.status_code == 204, r.text
    assert fake.calls == [("POST", "/Sessions/jf1/Message"), ("POST", "/Sessions/jf1/Playing/Stop")]


def test_history_comes_from_the_plugin_with_reads_only():
    fake = Jellyfin()
    records = client_for(fake).get_history()
    assert fake.calls == []
    film, episode = records
    assert (film.user, film.title, film.media_type, film.play_method) == \
        ("erin", "A Film", "movie", "direct_play")
    assert film.started_at == datetime(2026, 3, 2, 15, 5, tzinfo=UTC)
    assert (film.duration_seconds, film.date_only) == (5400, False)
    assert film.key == "7:2026-03-02T15:05:00+00:00"
    # A time that cannot be read keeps the play, on its day only.
    assert (episode.play_method, episode.date_only) == ("transcode", True)
    assert episode.started_at.date().isoformat() == "2026-03-02"


def test_import_through_the_app_and_twice_adds_nothing(tmp_path, monkeypatch):
    c, sid = app(Jellyfin(), tmp_path, monkeypatch)
    assert c.post(f"/api/servers/{sid}/import-history").json()["added"] == 2
    again = c.post(f"/api/servers/{sid}/import-history").json()
    assert (again["added"], again["skipped"]) == (0, 2)


def test_without_the_plugin_the_message_names_jellyfin(tmp_path, monkeypatch):
    c, sid = app(Jellyfin(plugin=False), tmp_path, monkeypatch)
    r = c.post(f"/api/servers/{sid}/import-history")
    assert r.status_code >= 400
    assert "Jellyfin has no Playback Reporting plugin" in r.text and "Emby" not in r.text


def test_servers_table_rebuild_keeps_servers_and_everything_that_points_at_them(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for version, sql in enumerate(MIGRATIONS[:14], start=1):
        old.executescript(sql)
        old.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    old.execute("INSERT INTO servers (id, type, name, url, token, created_at) "
                "VALUES (4, 'emby', 'E', 'http://e', 'tok', '2026-01-01')")
    old.execute("INSERT INTO history (server_id, session_key, user, title, media_type, device, "
                "platform, play_method, started_at, last_seen_at) "
                "VALUES (4, 'k', 'u', 't', 'movie', 'd', 'p', 'direct_play', '2026-01-01', '2026-01-01')")
    old.execute("INSERT INTO bandwidth_samples (server_id, ts, kbps, sessions) VALUES (4, '2026-01-01', 5, 1)")
    old.execute("INSERT INTO playback_problems (server_id, session_key, kind, user, title, device, "
                "platform, play_method, started_at, last_seen_at) "
                "VALUES (4, 'k', 'buffering', 'u', 't', 'd', 'p', 'direct_play', '2026-01-01', '2026-01-01')")
    old.commit()
    old.close()

    conn = connect(path)
    assert migrate(conn) == len(MIGRATIONS) >= 15
    assert dict(conn.execute("SELECT * FROM servers").fetchone())["id"] == 4
    for table in ("history", "bandwidth_samples", "playback_problems"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1, table
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.execute("INSERT INTO servers (type, name, url, token, created_at) "
                 "VALUES ('jellyfin', 'J', 'http://j', 'tok', '2026-01-02')")
    conn.commit()
    # Removing a server still takes its plays with it.
    conn.execute("DELETE FROM servers WHERE id = 4")
    conn.commit()
    assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 0
    conn.close()
