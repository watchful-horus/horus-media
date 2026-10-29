import sqlite3
import zipfile
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import app_settings, backup, maintenance, updates
from horus_media.crypto import TokenCipher
from horus_media.db import connect, migrate
from horus_media.main import create_app
from horus_media.stats import compute_stats
from horus_media.store import ServerStore

from .conftest import TOKEN


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "horus.db"
    conn = connect(path)
    migrate(conn)
    ServerStore(conn, TokenCipher.for_db(path)).add("plex", "Plex", "http://plex", TOKEN)
    return path, conn


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    monkeypatch.delenv("HORUS_UPDATE_URL", raising=False)
    return TestClient(create_app())


# --- settings storage and API ---------------------------------------------------------------

def test_defaults_then_update_persists(api):
    first = api.get("/api/settings").json()
    assert first["settings"]["retention_days"] == 0
    assert first["settings"]["update_check"] is True  # on for new installs; it only looks
    assert first["info"]["port"]  # whatever port the browser used
    assert "America/New_York" in first["info"]["timezones"]

    r = api.put("/api/settings", json={"retention_days": 90, "timezone": "Europe/London"})
    assert r.status_code == 200
    again = api.get("/api/settings").json()["settings"]
    assert again["retention_days"] == 90 and again["timezone"] == "Europe/London"
    assert again["backup_keep"] == 7  # untouched keys keep their values


@pytest.mark.parametrize("bad", [
    {"timezone": "Mars/Base"}, {"poll_seconds": 2}, {"retention_days": -1},
    {"backup_keep": 0}, {"nonsense": 1}, {"update_check": "perhaps"}])
def test_invalid_settings_rejected(api, bad):
    assert api.put("/api/settings", json=bad).status_code == 422
    assert api.get("/api/settings").json()["settings"]["retention_days"] == 0


def test_poll_seconds_default_comes_from_env(db, monkeypatch):
    _, conn = db
    monkeypatch.setenv("HORUS_POLL_SECONDS", "45")
    assert app_settings.load(conn).poll_seconds == 45
    app_settings.update(conn, {"poll_seconds": 0})  # a saved value wins over the env default
    assert app_settings.load(conn).poll_seconds == 0


def test_corrupt_stored_value_falls_back(db):
    _, conn = db
    with conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('retention_days', '99999')")
        conn.execute("INSERT INTO settings (key, value) VALUES ('backup_keep', '5')")
    cfg = app_settings.load(conn)
    assert cfg.retention_days == 0 and cfg.backup_keep == 5


# --- time zone ------------------------------------------------------------------------------

def test_stats_use_configured_time_zone(db):
    _, conn = db
    started = datetime(2026, 3, 10, 3, 30, tzinfo=UTC)  # 20:30 on the 9th in Los Angeles
    with conn:
        conn.execute(
            "INSERT INTO history (server_id, session_key, user, title, media_type, device, "
            "platform, play_method, started_at, last_seen_at) "
            "VALUES (1, 'k', 'u', 't', 'movie', 'd', 'p', 'direct_play', ?, ?)",
            (started.isoformat(), started.isoformat()),
        )
    la = ZoneInfo("America/Los_Angeles")
    data = compute_stats(conn, date(2026, 3, 9), date(2026, 3, 10), la)
    assert data["total_plays"] == 1
    assert data["plays_per_day"]["series"][0]["data"] == [1, 0]  # counted on the 9th
    assert data["by_hour"][20] == 1
    utc = compute_stats(conn, date(2026, 3, 9), date(2026, 3, 10), ZoneInfo("UTC"))
    assert utc["plays_per_day"]["series"][0]["data"] == [0, 1]


# --- backups --------------------------------------------------------------------------------

def test_full_backup_contains_db_and_key_and_is_consistent(db):
    path, _ = db
    out = backup.write_full_backup(path, datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    assert out.name == "horus-20260102-030405.zip"
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["horus.db", "horus.key"]
        z.extract("horus.db", out.parent)
    copy = sqlite3.connect(out.parent / "horus.db")
    assert copy.execute("SELECT COUNT(*) FROM servers").fetchone()[0] == 1


def test_backups_pruned_to_keep_count(db):
    path, _ = db
    for i in range(5):
        backup.write_full_backup(path, datetime(2026, 1, 1 + i, tzinfo=UTC))
    backup.prune(path, 2)
    assert [b["name"] for b in backup.list_backups(path)] == [
        "horus-20260105-000000.zip", "horus-20260104-000000.zip"]


def test_full_backup_can_be_downloaded(api, tmp_path):
    api.post("/api/servers", json={"type": "plex", "name": "P", "url": "http://p", "token": TOKEN})
    assert api.post("/api/backups").status_code == 201
    name = api.get("/api/backups").json()["items"][0]["name"]
    r = api.get(f"/api/backups/{name}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    out = tmp_path / "x.zip"
    out.write_bytes(r.content)
    with zipfile.ZipFile(out) as z:
        assert sorted(z.namelist()) == ["horus.db", "horus.key"]
    assert TOKEN.encode() not in r.content  # tokens stay encrypted inside the database

    assert api.get("/api/backups/horus-19990101-000000.zip/download").status_code == 404
    assert api.get("/api/backups/..%2Fhorus.db/download").status_code == 404


def test_scheduled_backup_and_retention(db):
    path, conn = db
    now = datetime(2026, 3, 10, 12, tzinfo=UTC)
    old = (now - timedelta(days=100)).isoformat()
    with conn:
        conn.execute(
            "INSERT INTO history (server_id, session_key, user, title, media_type, device, "
            "platform, play_method, started_at, last_seen_at, ended_at) "
            "VALUES (1,'a','u','old','movie','d','p','direct_play',?,?,?)", (old, old, old))
        conn.execute("INSERT INTO bandwidth_samples (server_id, ts, kbps, sessions) "
                     "VALUES (1, ?, 5, 1)", (old,))
        conn.execute(
            "INSERT INTO history (server_id, session_key, user, title, media_type, device, "
            "platform, play_method, started_at, last_seen_at) "
            "VALUES (1,'b','u','playing now','movie','d','p','direct_play',?,?)", (old, old))
    cfg = app_settings.update(conn, {"retention_days": 30, "backup_enabled": True,
                                     "backup_interval_hours": 24})
    maintenance.run(conn, path, cfg, now)
    assert [r[0] for r in conn.execute("SELECT title FROM history")] == ["playing now"]  # open play kept
    assert conn.execute("SELECT COUNT(*) FROM bandwidth_samples").fetchone()[0] == 0
    assert len(backup.list_backups(path)) == 1

    maintenance.run(conn, path, cfg, now + timedelta(hours=1))  # not due yet
    assert len(backup.list_backups(path)) == 1
    maintenance.run(conn, path, cfg, now + timedelta(hours=25))
    assert len(backup.list_backups(path)) == 2


def test_nothing_runs_when_features_off(db):
    path, conn = db
    maintenance.run(conn, path, app_settings.load(conn), datetime(2026, 3, 10, tzinfo=UTC))
    assert backup.list_backups(path) == []


# --- update check ---------------------------------------------------------------------------

def test_version_comparison():
    assert updates.parse_version("v1.10.0") > updates.parse_version("1.9.9")
    assert updates.parse_version("0.0.1") == updates.parse_version("0.0.1-beta")


def test_update_check_unconfigured_contacts_nothing(api):
    api.put("/api/settings", json={"update_url": ""})
    assert api.get("/api/update-check").json() == {"configured": False}


def test_update_check_with_source(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    monkeypatch.setenv("HORUS_UPDATE_URL", "https://example.test/latest")
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"tag_name": "v9.0.0", "html_url": "https://x/r"})

    c = TestClient(create_app(transport=httpx.MockTransport(handler)))
    r = c.get("/api/update-check").json()
    assert r["latest"] == "9.0.0" and r["newer"] is True
    assert c.get("/api/settings").json()["info"]["update_latest"] == "9.0.0"
    assert set(seen[0].headers) >= {"user-agent"} and "x-plex-token" not in seen[0].headers
    down = TestClient(create_app(transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    assert down.get("/api/update-check").status_code == 502


def test_daily_check_runs_once_a_day_and_can_be_turned_off(db, monkeypatch):
    path, conn = db
    monkeypatch.setenv("HORUS_UPDATE_URL", "https://example.test/latest")
    calls = []
    t = httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={"tag_name": "2.0"}))
    now = datetime(2026, 3, 10, tzinfo=UTC)
    off = app_settings.update(conn, {"update_check": False})
    maintenance.run(conn, path, off, now, t)
    assert calls == []  # switched off
    cfg = app_settings.update(conn, {"update_check": True})
    maintenance.run(conn, path, cfg, now, t)
    maintenance.run(conn, path, cfg, now + timedelta(hours=2), t)
    assert len(calls) == 1  # once a day
