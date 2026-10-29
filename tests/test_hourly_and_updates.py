from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import app_settings, maintenance
from horus_media.config import DEFAULT_UPDATE_URL
from horus_media.crypto import TokenCipher
from horus_media.db import connect, migrate
from horus_media.main import create_app
from horus_media.stats import HOURLY_MAX_DAYS, compute_stats
from horus_media.store import ServerStore

from .conftest import TOKEN


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    path = tmp_path / "horus.db"
    conn = connect(path)
    migrate(conn)
    store = ServerStore(conn, TokenCipher.for_db(path))
    store.add("plex", "Plex", "http://plex", TOKEN)
    store.add("emby", "Emby", "http://emby", TOKEN)
    return path, conn


def add_play(conn, server_id, started: datetime, method="direct_play"):
    conn.execute(
        "INSERT INTO history (server_id, session_key, user, title, media_type, device, platform, "
        "play_method, started_at, last_seen_at, ended_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (server_id, "k", "u", "t", "movie", "d", "p", method, started.isoformat(),
         started.isoformat(), started.isoformat()))


# --- hourly stats ---------------------------------------------------------------------------

def test_hourly_series_cover_every_hour_and_count_plays(db):
    _, conn = db
    utc = ZoneInfo("UTC")
    with conn:
        add_play(conn, 1, datetime(2026, 3, 10, 14, 5, tzinfo=UTC))
        add_play(conn, 1, datetime(2026, 3, 10, 14, 55, tzinfo=UTC), "transcode")
        add_play(conn, 2, datetime(2026, 3, 10, 20, 0, tzinfo=UTC), "direct_stream")
    data = compute_stats(conn, date(2026, 3, 10), date(2026, 3, 11), utc, hourly=True)
    hours = data["plays_per_hour"]["hours"]
    assert len(hours) == 48 and len(set(hours)) == 48  # two whole days, no gaps, no repeats
    assert hours[0].startswith("2026-03-10T00:00") and hours[-1].startswith("2026-03-11T23:00")
    plex, emby = data["plays_per_hour"]["series"]
    assert plex["server"] == "Plex" and plex["data"][14] == 2 and sum(plex["data"]) == 2
    assert emby["data"][20] == 1 and sum(emby["data"]) == 1
    by_method = {m["name"]: m["data"] for m in data["methods_per_hour"]}
    assert by_method["transcode"][14] == 1 and by_method["direct_play"][14] == 1
    assert by_method["direct_stream"][20] == 1
    assert sum(sum(m["data"]) for m in data["methods_per_hour"]) == data["total_plays"] == 3


def test_hourly_data_only_when_asked_for(db):
    _, conn = db
    data = compute_stats(conn, date(2026, 3, 10), date(2026, 3, 10), ZoneInfo("UTC"))
    assert "plays_per_hour" not in data and "methods_per_hour" not in data


def test_hours_follow_the_time_zone_through_daylight_saving(db):
    _, conn = db
    la = ZoneInfo("America/Los_Angeles")
    # 2026-11-01 has 25 hours in Los Angeles: the clocks go back at 2am.
    with conn:
        add_play(conn, 1, datetime(2026, 11, 1, 9, 30, tzinfo=UTC))   # 02:30 PST, the second 1am hour
        add_play(conn, 1, datetime(2026, 11, 1, 8, 30, tzinfo=UTC))   # 01:30 PDT, the first 1am hour
    data = compute_stats(conn, date(2026, 11, 1), date(2026, 11, 1), la, hourly=True)
    hours = data["plays_per_hour"]["hours"]
    assert len(hours) == 25 and len(set(hours)) == 25  # the repeated 1am hour is two buckets
    counts = data["plays_per_hour"]["series"][0]["data"]
    assert sum(counts) == 2 and max(counts) == 1  # each play lands in its own bucket


def test_stats_endpoint_hourly_limits(db):
    client = TestClient(create_app())
    today = date.today()
    ok = client.get("/api/stats", params={"start": (today - timedelta(days=89)).isoformat(),
                                          "end": today.isoformat(), "hourly": "true"})
    assert ok.status_code == 200 and len(ok.json()["plays_per_hour"]["hours"]) >= 90 * 24 - 2
    too_long = client.get("/api/stats", params={"start": (today - timedelta(days=90)).isoformat(),
                                                "end": today.isoformat(), "hourly": "true"})
    assert too_long.status_code == 422 and str(HOURLY_MAX_DAYS) in too_long.json()["detail"]
    # The same long range is fine without the hourly view.
    assert client.get("/api/stats", params={"start": (today - timedelta(days=200)).isoformat(),
                                            "end": today.isoformat()}).status_code == 200
    assert "plays_per_hour" not in client.get("/api/stats").json()


# --- update address -------------------------------------------------------------------------

RELEASE = {"tag_name": "v9.1.0", "html_url": "https://example.test/releases/9.1.0"}


def release_app(seen):
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=RELEASE)

    return TestClient(create_app(transport=httpx.MockTransport(handler)))


def test_update_address_is_validated_and_saved(db, monkeypatch):
    monkeypatch.delenv("HORUS_UPDATE_URL", raising=False)
    client = TestClient(create_app())
    assert client.get("/api/settings").json()["settings"]["update_url"] == DEFAULT_UPDATE_URL
    client.put("/api/settings", json={"update_url": ""})
    assert client.get("/api/update-check").json() == {"configured": False}
    for bad in ("ftp://example.test/x", "javascript:alert(1)", "example.test/latest",
                "http://has space.test", "https://" + "a" * 600):
        assert client.put("/api/settings", json={"update_url": bad}).status_code == 422, bad
    r = client.put("/api/settings", json={"update_url": "  https://example.test/latest  "})
    assert r.status_code == 200
    body = client.get("/api/settings").json()
    assert body["settings"]["update_url"] == "https://example.test/latest"  # trimmed
    assert body["info"]["update_source_configured"] is True
    assert body["info"]["update_url_from_env"] is False


def test_check_for_updates_uses_the_saved_address(db, monkeypatch):
    monkeypatch.delenv("HORUS_UPDATE_URL", raising=False)
    seen: list[str] = []
    client = release_app(seen)
    client.put("/api/settings", json={"update_url": ""})
    assert client.get("/api/update-check").json() == {"configured": False}
    assert seen == []  # nothing is contacted until an address is set
    client.put("/api/settings", json={"update_url": "https://example.test/latest"})
    result = client.get("/api/update-check").json()
    assert result["latest"] == "9.1.0" and result["newer"] is True
    assert seen == ["https://example.test/latest"]
    assert client.get("/api/settings").json()["info"]["update_latest"] == "9.1.0"


def test_environment_address_wins_over_the_saved_one(db, monkeypatch):
    monkeypatch.setenv("HORUS_UPDATE_URL", "https://env.example.test/latest")
    seen: list[str] = []
    client = release_app(seen)
    client.put("/api/settings", json={"update_url": "https://saved.example.test/latest"})
    assert client.get("/api/settings").json()["info"]["update_url_from_env"] is True
    client.get("/api/update-check")
    assert seen == ["https://env.example.test/latest"]


def test_daily_check_uses_the_saved_address_and_only_when_opted_in(db, monkeypatch):
    path, conn = db
    monkeypatch.delenv("HORUS_UPDATE_URL", raising=False)
    calls: list[str] = []
    transport = httpx.MockTransport(lambda r: calls.append(str(r.url)) or httpx.Response(
        200, json={"tag_name": "2.0"}))
    now = datetime(2026, 3, 10, tzinfo=UTC)
    cfg = app_settings.update(conn, {"update_url": "https://example.test/latest"})
    maintenance.run(conn, path, cfg, now, transport)
    assert calls == []  # an address alone is not consent to a daily check
    cfg = app_settings.update(conn, {"update_check": True})
    maintenance.run(conn, path, cfg, now, transport)
    assert calls == ["https://example.test/latest"]
