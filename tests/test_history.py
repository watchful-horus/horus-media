from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from horus_media.crypto import TokenCipher
from horus_media.db import connect, migrate
from horus_media.poller import close_stale, poll_once
from horus_media.stats import compute_stats
from horus_media.store import ServerStore

from .conftest import TOKEN

T0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def emby_session(sid, item_id, name, kbps=4_000_000, method="DirectStream", user="carol"):
    return {
        "Id": sid, "UserName": user, "DeviceName": "TV", "Client": "Emby Web",
        "NowPlayingItem": {"Id": item_id, "Name": name, "Type": "Movie", "Bitrate": kbps},
        "PlayState": {"PlayMethod": method},
    }


@pytest.fixture
def env(tmp_path):
    conn = connect(tmp_path / "h.db")
    migrate(conn)
    store = ServerStore(conn, TokenCipher(tmp_path / "h.key"))
    store.add("emby", "Emby", "http://emby", TOKEN)
    sessions: list = []
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=sessions))
    return conn, store, sessions, transport


def poll(env, minutes):
    conn, store, _, transport = env
    poll_once(conn, store._cipher, transport, now=T0 + timedelta(minutes=minutes))


def test_play_lifecycle(env):
    conn, _, sessions, _ = env
    sessions.append(emby_session("s1", "i1", "Film A"))
    poll(env, 0)
    poll(env, 1)
    rows = conn.execute("SELECT * FROM history").fetchall()
    assert len(rows) == 1 and rows[0]["ended_at"] is None
    assert rows[0]["started_at"] == T0.isoformat()
    assert rows[0]["last_seen_at"] == (T0 + timedelta(minutes=1)).isoformat()

    sessions.clear()
    poll(env, 2)
    row = conn.execute("SELECT * FROM history").fetchone()
    assert row["ended_at"] == row["last_seen_at"]  # ended when last seen, not at detection


def test_new_title_same_session_is_new_play(env):
    conn, _, sessions, _ = env
    sessions.append(emby_session("s1", "i1", "Film A"))
    poll(env, 0)
    sessions[0] = emby_session("s1", "i2", "Film B")
    poll(env, 1)
    rows = conn.execute("SELECT title, ended_at FROM history ORDER BY id").fetchall()
    assert [r["title"] for r in rows] == ["Film A", "Film B"]
    assert rows[0]["ended_at"] is not None and rows[1]["ended_at"] is None


def test_unreachable_server_leaves_play_open(env):
    conn, store, sessions, _ = env
    sessions.append(emby_session("s1", "i1", "Film A"))
    poll(env, 0)
    down = httpx.MockTransport(lambda r: httpx.Response(500))
    poll_once(conn, store._cipher, down, now=T0 + timedelta(minutes=1))
    assert conn.execute("SELECT ended_at FROM history").fetchone()["ended_at"] is None
    close_stale(conn)  # simulates a restart
    assert conn.execute("SELECT ended_at FROM history").fetchone()["ended_at"] is not None


def test_peak_bandwidth_and_samples(env):
    conn, _, sessions, _ = env
    sessions.append(emby_session("s1", "i1", "Film A", kbps=2_000_000))
    poll(env, 0)
    sessions[0] = emby_session("s1", "i1", "Film A", kbps=6_000_000)
    poll(env, 1)
    sessions.clear()
    poll(env, 2)
    assert conn.execute("SELECT bandwidth_kbps FROM history").fetchone()[0] == 6000
    assert [r[0] for r in conn.execute("SELECT kbps FROM bandwidth_samples ORDER BY id")] == [
        2000, 6000, 0]


def test_stats_aggregation(env):
    conn, _, sessions, _ = env
    sessions.append(emby_session("s1", "i1", "Film A", method="Transcode", user="carol"))
    poll(env, 0)
    sessions[0] = emby_session("s1", "i2", "Film B", user="dave")
    poll(env, 1)
    local_day = T0.astimezone().date()
    data = compute_stats(conn, local_day - timedelta(days=2), local_day + timedelta(days=1))
    assert data["total_plays"] == 2
    days = data["plays_per_day"]
    assert len(days["days"]) == 4
    assert sum(days["series"][0]["data"]) == 2
    assert days["series"][0]["data"][days["days"].index(local_day.isoformat())] == 2
    assert {u["name"] for u in data["by_user"]} == {"carol", "dave"}
    assert {m["name"]: m["count"] for m in data["play_methods"]} == {
        "transcode": 1, "direct_stream": 1}
    assert sum(data["by_hour"]) == 2 and sum(data["by_weekday"]["data"]) == 2
    assert data["bandwidth"][0]["points"]
    stacked = data["by_weekday"]["by_type"]  # the same plays, split by media type
    assert sum(sum(t["data"]) for t in stacked) == 2
    assert [sum(col) for col in zip(*(t["data"] for t in stacked), strict=True)] == data["by_weekday"]["data"]
    assert data["bandwidth_since"]  # when the first bandwidth sample was recorded
    assert data["earliest"] == local_day.isoformat()
    by_method = {m["name"]: m["data"] for m in data["methods_per_day"]}
    assert sum(by_method["transcode"]) == 1 and sum(by_method["direct_stream"]) == 1
    assert sum(by_method["direct_play"]) == 0


def test_stats_excludes_outside_range(env):
    conn, _, sessions, _ = env
    sessions.append(emby_session("s1", "i1", "Film A"))
    poll(env, 0)
    far = date(2020, 1, 1)
    assert compute_stats(conn, far, far)["total_plays"] == 0


def test_stats_endpoint(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert len(r.json()["plays_per_day"]["days"]) == 30
    assert client.get("/api/stats?start=2026-02-02&end=2026-01-01").status_code == 422


def test_bandwidth_buckets_follow_how_much_data_exists(env):
    conn, _, sessions, _ = env
    sessions.append(emby_session("s1", "i1", "Film A"))
    for minute in range(0, 31, 1):  # half an hour of data, polled every minute
        poll(env, minute)
    local_day = T0.astimezone().date()
    data = compute_stats(conn, local_day - timedelta(days=29), local_day)  # a 30-day range
    points = data["bandwidth"][0]["points"]
    assert len(points) >= 20  # roughly one point per minute, not a single hourly one


def test_bandwidth_total_adds_servers_per_poll(env):
    conn, store, sessions, _ = env
    store.add("emby", "Emby 2", "http://emby2", TOKEN)  # same fake sessions, so same bandwidth
    sessions.append(emby_session("s1", "i1", "Film A", kbps=4_000_000))
    poll(env, 0)
    poll(env, 1)
    local_day = T0.astimezone().date()
    data = compute_stats(conn, local_day, local_day)
    each = data["bandwidth"][0]["points"]
    total = data["bandwidth_total"]
    assert total and len(total) == len(each)
    assert total[0][1] == 2 * each[0][1]  # average adds up
    assert total[0][2] == 2 * each[0][2]  # and so does the peak, measured at the same instant


def test_monthly_totals_cover_twelve_months_per_server(tmp_path):
    import sqlite3
    from datetime import date

    from horus_media.stats import compute_monthly

    conn = sqlite3.connect(tmp_path / "m.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE servers (id INTEGER PRIMARY KEY, name TEXT);"
        "CREATE TABLE history (server_id INTEGER, user TEXT, started_at TEXT);"
        "INSERT INTO servers VALUES (1, 'A'), (2, 'B');"
        "INSERT INTO history VALUES (1, 'x', '2026-10-05T12:00:00+00:00'),"
        " (1, 'x', '2026-10-06T12:00:00+00:00'), (2, 'y', '2025-11-10T12:00:00+00:00'),"
        " (2, 'y', '2025-10-31T12:00:00+00:00');"  # one month too old: left out
    )
    out = compute_monthly(conn, None, today=date(2026, 10, 29))
    assert out["months"][0] == "2025-11-01" and out["months"][-1] == "2026-10-01"
    assert len(out["months"]) == 12
    assert out["series"][0] == {"server": "A", "data": [0] * 11 + [2]}
    assert out["series"][1]["data"][0] == 1
    assert out["total"] == 3
