import csv
import io
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import app_settings
from horus_media.crypto import TokenCipher
from horus_media.db import connect, migrate
from horus_media.main import create_app
from horus_media.store import ServerStore

from .conftest import TOKEN, fake_servers

NOW = datetime.now(UTC).replace(microsecond=0)


def add_play(conn, server_id, user, platform, method, started, media="movie", title="Film",
             minutes=30, peak=5000):
    seen = started + timedelta(minutes=minutes)
    conn.execute(
        "INSERT INTO history (server_id, session_key, user, title, media_type, device, platform, "
        "play_method, bandwidth_kbps, started_at, last_seen_at, ended_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (server_id, "k", user, title, media, "Dev", platform, method, peak,
         started.isoformat(), seen.isoformat(), seen.isoformat()))


@pytest.fixture
def api(tmp_path, monkeypatch):
    """Two servers with a little history and bandwidth, and fake Plex/Emby for live data."""
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    path = tmp_path / "horus.db"
    conn = connect(path)
    migrate(conn)
    store = ServerStore(conn, TokenCipher.for_db(path))
    store.add("plex", "Plex", "http://plex:32400", TOKEN)
    store.add("emby", "Emby", "http://emby:8096", TOKEN)
    with conn:
        add_play(conn, 1, "alice", "Roku", "direct_play", NOW - timedelta(days=1, hours=2))
        add_play(conn, 1, "bob", "Chrome", "transcode", NOW - timedelta(days=1, hours=1),
                 media="episode", title="Show - Ep")
        add_play(conn, 2, "alice", "Chrome", "direct_stream", NOW - timedelta(days=3))
        add_play(conn, 2, "carol", "Roku", "direct_play", NOW - timedelta(days=40))
        for minute in range(0, 60, 5):  # one polling hour, both servers, same timestamps
            ts = (NOW - timedelta(hours=2, minutes=minute)).isoformat()
            conn.execute("INSERT INTO bandwidth_samples (server_id, ts, kbps, sessions) "
                         "VALUES (1, ?, 4000, 1)", (ts,))
            conn.execute("INSERT INTO bandwidth_samples (server_id, ts, kbps, sessions) "
                         "VALUES (2, ?, 1000, 1)", (ts,))
    conn.close()
    return TestClient(create_app(transport=httpx.MockTransport(fake_servers)))


# --- history --------------------------------------------------------------------------------

def test_history_rows_and_filters(api):
    rows = api.get("/api/v1/history", params={"from": (NOW - timedelta(days=7)).isoformat()}).json()
    assert len(rows) == 3  # the 40-day-old play is outside the default week
    first = rows[0]
    assert set(first) == {"id", "server", "user", "title", "media_type", "device", "platform",
                          "play_method", "peak_bandwidth_kbps", "started_at", "ended_at",
                          "duration_seconds"}
    assert first["duration_seconds"] == 1800
    assert rows[0]["started_at"] > rows[-1]["started_at"]  # newest first
    only_plex = api.get("/api/v1/history", params={"server": "Plex"}).json()
    assert {r["server"] for r in only_plex} == {"Plex"}
    assert {r["user"] for r in api.get("/api/v1/history", params={"user": "alice"}).json()} == {"alice"}
    assert len(api.get("/api/v1/history", params={"limit": 1}).json()) == 1
    assert len(api.get("/api/v1/history", params={"limit": 2, "offset": 2}).json()) == 1
    assert api.get("/api/v1/history", params={"limit": 0}).status_code == 422


def test_time_parameters_accept_dates_iso_and_grafana_epoch_millis(api):
    wide = {"from": (NOW - timedelta(days=60)).date().isoformat(), "to": NOW.date().isoformat()}
    assert len(api.get("/api/v1/history", params=wide).json()) == 4
    ms = {"from": str(int((NOW - timedelta(days=60)).timestamp() * 1000)),
          "to": str(int(NOW.timestamp() * 1000))}
    assert len(api.get("/api/v1/history", params=ms).json()) == 4
    assert len(api.get("/api/v1/history", params={"from": str(int((NOW - timedelta(days=60)).timestamp())),
                                                   "to": str(int(NOW.timestamp()))}).json()) == 4
    assert api.get("/api/v1/history", params={"from": "yesterday"}).status_code == 422
    assert api.get("/api/v1/history", params={"from": NOW.isoformat(),
                                              "to": (NOW - timedelta(days=1)).isoformat()}).status_code == 422
    assert api.get("/api/v1/history", params={"from": "2000-01-01", "to": "2026-01-01"}).status_code == 422


def test_csv_output(api):
    r = api.get("/api/v1/history", params={"format": "csv", "from": (NOW - timedelta(days=7)).isoformat()})
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 3 and rows[0]["user"] and "duration_seconds" in rows[0]
    assert api.get("/api/v1/history", params={"format": "xml"}).status_code == 422


# --- plays ----------------------------------------------------------------------------------

def test_plays_grouping(api):
    week = {"from": (NOW - timedelta(days=7)).isoformat()}
    total = api.get("/api/v1/plays", params={**week, "interval": "month"}).json()
    assert sum(r["plays"] for r in total) == 3 and all(set(r) == {"time", "plays"} for r in total)
    by_server = api.get("/api/v1/plays", params={**week, "by": "server"}).json()
    counts = {}
    for r in by_server:
        counts[r["server"]] = counts.get(r["server"], 0) + r["plays"]
    assert counts == {"Plex": 2, "Emby": 1}
    by_method = api.get("/api/v1/plays", params={**week, "by": "play_method"}).json()
    assert {r["play_method"] for r in by_method} == {"direct_play", "transcode", "direct_stream"}
    assert {r["user"] for r in api.get("/api/v1/plays", params={**week, "by": "user"}).json()} == {
        "alice", "bob"}
    hourly = api.get("/api/v1/plays", params={**week, "interval": "hour"}).json()
    assert all(datetime.fromisoformat(r["time"]).minute == 0 for r in hourly)
    assert api.get("/api/v1/plays", params={"by": "password"}).status_code == 422
    assert api.get("/api/v1/plays", params={"interval": "second"}).status_code == 422


def test_plays_use_the_configured_time_zone(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    conn = connect(tmp_path / "horus.db")
    migrate(conn)
    ServerStore(conn, TokenCipher.for_db(tmp_path / "horus.db")).add("plex", "P", "http://p", TOKEN)
    with conn:
        add_play(conn, 1, "u", "p", "direct_play", datetime(2026, 3, 10, 3, 30, tzinfo=UTC))
    app_settings.update(conn, {"timezone": "America/Los_Angeles"})
    conn.close()
    rows = TestClient(create_app()).get("/api/v1/plays", params={
        "from": "2026-03-01", "to": "2026-03-31"}).json()
    assert [r["time"][:10] for r in rows] == ["2026-03-09"]  # 20:30 on the 9th, Los Angeles time


# --- bandwidth ------------------------------------------------------------------------------

def test_bandwidth_with_total(api):
    rows = api.get("/api/v1/bandwidth", params={"from": (NOW - timedelta(days=1)).isoformat()}).json()
    names = {r["server"] for r in rows}
    assert names == {"Plex", "Emby", "Total"}
    plex = next(r for r in rows if r["server"] == "Plex")
    total = next(r for r in rows if r["server"] == "Total")
    assert plex["avg_kbps"] == 4000 and plex["peak_kbps"] == 4000
    assert total["avg_kbps"] == 5000 and total["peak_kbps"] == 5000  # measured at the same instants
    assert set(plex) == {"time", "server", "avg_kbps", "peak_kbps"}
    coarse = api.get("/api/v1/bandwidth", params={"interval": "1d", "from": (NOW - timedelta(days=1)).isoformat()}).json()
    assert len(coarse) < len(rows)
    assert api.get("/api/v1/bandwidth", params={"interval": "7s"}).status_code == 422


# --- live data and safety -------------------------------------------------------------------

def test_activity_and_servers(api):
    servers = api.get("/api/v1/servers").json()
    assert servers == [{"id": 1, "name": "Plex", "type": "plex"},
                       {"id": 2, "name": "Emby", "type": "emby"}]
    rows = api.get("/api/v1/activity").json()
    assert {r["user"] for r in rows} >= {"alice", "bob", "carol"}  # fake servers have sessions
    assert {"server", "user", "title", "play_method", "bandwidth_kbps", "video"} <= set(rows[0])
    assert "poster" not in rows[0] and "session_key" not in rows[0]
    csv_text = api.get("/api/v1/activity", params={"format": "csv"}).text
    assert csv_text.splitlines()[0].startswith("server,user,title")


def test_nothing_secret_in_any_data_endpoint(api):
    for path in ("servers", "activity", "history", "plays", "bandwidth"):
        text = api.get(f"/api/v1/{path}", params={"format": "csv"}).text + \
            api.get(f"/api/v1/{path}").text
        assert TOKEN not in text and "token" not in text.lower(), path
        assert "plex:32400" not in text and "emby:8096" not in text, path  # no server addresses


def test_data_api_is_read_only(api):
    for method in ("post", "put", "delete", "patch"):
        assert getattr(api, method)("/api/v1/history").status_code == 405, method
