"""Housekeeping that keeps Horus quick and its data safe: the wait for a busy database, doing
start-up work once, reusing a fresh live result, thinning old bandwidth samples, a restore that
survives being interrupted, limits on a server's details and a clear update-check message."""

import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from horus_media import activity, app_settings, backup, db, maintenance, updates
from horus_media.config import db_path
from horus_media.crypto import KEY_FILENAME, TokenCipher
from horus_media.db import connect, migrate
from horus_media.main import create_app
from horus_media.migrations import MIGRATIONS
from horus_media.stats import compute_stats

from .conftest import NETWORK_ATTEMPTS, PLEX_SESSIONS, TOKEN, fake_servers
from .test_auth import add_user, local, secured, set_up, sign_in  # noqa: F401

PLEX = {"type": "plex", "name": "Plex", "url": "http://plex:32400", "token": TOKEN}
NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    c = connect(tmp_path / "horus.db")
    migrate(c)
    with c:
        for name in ("Plex", "Emby"):
            c.execute("INSERT INTO servers (type, name, url, token, created_at) "
                      "VALUES ('plex', ?, 'http://x', '', '2026-01-01T00:00:00+00:00')", (name,))
    yield c
    c.close()


# --- the database: wait, migrate once, key read once ----------------------------------------

def test_a_busy_database_is_waited_for(tmp_path):
    c = connect(tmp_path / "horus.db")
    # Python's default is 5 seconds; SQLite reports the wait in milliseconds.
    assert c.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_SECONDS * 1000 == 30000
    c.close()


def test_migrations_are_checked_once_per_database_file(tmp_path, monkeypatch):
    calls = []
    real = db._migrate
    monkeypatch.setattr(db, "_migrate", lambda c: calls.append(1) or real(c))
    path = tmp_path / "horus.db"
    for _ in range(3):
        c = connect(path)
        assert migrate(c) == len(MIGRATIONS)
        c.close()
    assert len(calls) == 1
    other = connect(tmp_path / "other.db")  # another file is another matter
    migrate(other)
    other.close()
    assert len(calls) == 2
    raw = sqlite3.connect(path)  # a plain connection (a restore uses one) is always checked
    migrate(raw)
    raw.close()
    assert len(calls) == 3


def test_a_database_swapped_in_from_outside_is_migrated(tmp_path):
    path = tmp_path / "horus.db"
    c = connect(path)
    migrate(c)
    c.close()
    path.unlink()
    # Created right after the delete, so on Linux it is usually handed the same file id.
    raw = sqlite3.connect(path)
    raw.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    raw.commit()
    raw.close()
    c = connect(path)
    migrate(c)
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == len(MIGRATIONS)
    c.close()


def test_an_older_database_written_into_the_same_file_is_migrated(tmp_path):
    """What a restore does: the file stays, its contents become an older layout."""
    path = tmp_path / "horus.db"
    c = connect(path)
    migrate(c)
    c.close()
    older = sqlite3.connect(tmp_path / "older.db")
    older.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    older.commit()
    live = sqlite3.connect(path)
    older.backup(live)
    live.close()
    older.close()
    c = connect(path)
    migrate(c)
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == len(MIGRATIONS)
    c.close()


def test_the_key_file_is_read_once_until_it_changes(tmp_path, monkeypatch):
    path = tmp_path / "horus.db"
    first = TokenCipher.for_db(path)  # creates the key
    again = TokenCipher.for_db(path)
    assert TokenCipher.for_db(path) is again
    assert again.decrypt(first.encrypt("secret")) == "secret"
    sealed = again.encrypt("secret")
    new_key = Fernet.generate_key()
    staged = tmp_path / "new.key"
    staged.write_bytes(new_key)
    staged.replace(tmp_path / KEY_FILENAME)  # what a restore does
    changed = TokenCipher.for_db(path)
    assert changed is not again
    assert changed.decrypt(Fernet(new_key).encrypt(b"other").decode()) == "other"
    with pytest.raises(Exception):  # noqa: B017 - the old key's tokens no longer open
        changed.decrypt(sealed)


# --- live activity is reused for a few seconds ----------------------------------------------

@pytest.fixture
def counted(tmp_path, monkeypatch):
    """An app whose fake Plex counts how often it is asked for its sessions."""
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status/sessions":
            asked.append(1)
        return fake_servers(request)

    client = TestClient(create_app(transport=httpx.MockTransport(handler)))
    assert client.post("/api/servers", json=PLEX).status_code == 201
    return client, asked


def test_pages_api_and_metrics_share_one_fresh_answer(counted, monkeypatch):
    client, asked = counted
    monkeypatch.setattr(activity, "LIVE_MAX_AGE", 5.0)
    first = client.get("/api/activity").json()
    assert len(first["sessions"]) == 2 and len(asked) == 1
    assert client.get("/api/activity").json() == first
    assert len(client.get("/api/v1/activity").json()) == 2
    assert "horus_streams" in client.get("/metrics").text
    assert len(asked) == 1  # four readers, one call to the media server


def test_an_answer_older_than_the_limit_is_fetched_again(counted, monkeypatch):
    client, asked = counted
    monkeypatch.setattr(activity, "LIVE_MAX_AGE", 5.0)
    clock = [1000.0]
    monkeypatch.setattr(activity.time, "monotonic", lambda: clock[0])
    client.get("/api/activity")
    clock[0] += 4.9
    client.get("/api/activity")
    assert len(asked) == 1
    clock[0] += 0.2
    client.get("/api/activity")
    assert len(asked) == 2


def direct(asked):
    """Server rows and a counting transport for calling `fetch_all` itself."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/status/sessions":
            asked.append(1)
        return fake_servers(request)

    return httpx.MockTransport(handler)


def test_a_reused_answer_is_a_copy(conn):
    asked = []
    transport = direct(asked)
    rows = [(r, TOKEN) for r in conn.execute("SELECT * FROM servers WHERE name = 'Plex'")]
    first = activity.fetch_all(rows, transport, 5.0)
    first[0].sessions[0].location = "changed by one caller"  # hiding an address, say
    first[0].sessions.pop()
    second = activity.fetch_all(rows, transport, 5.0)
    assert len(asked) == 1
    assert len(second[0].sessions) == 2 and second[0].sessions[0].location != "changed by one caller"


def test_a_changed_server_list_or_token_is_asked_afresh(conn):
    asked = []
    transport = direct(asked)
    one = [(r, TOKEN) for r in conn.execute("SELECT * FROM servers WHERE name = 'Plex'")]
    both = [(r, TOKEN) for r in conn.execute("SELECT * FROM servers")]
    activity.fetch_all(one, transport, 5.0)
    assert len(activity.fetch_all(both, transport, 5.0)) == 2 and len(asked) == 3
    other_token = [(one[0][0], "another token")]
    assert activity.fetch_all(other_token, transport, 5.0)[0].ok is False  # asked, and refused
    assert len(asked) == 4
    activity.fetch_all(other_token, transport, 5.0)
    assert len(asked) == 4  # the same question again within five seconds: remembered
    activity.forget_live()
    activity.fetch_all(other_token, transport, 5.0)
    assert len(asked) == 5


def test_the_poller_always_asks_the_servers(conn):
    asked = []
    transport = direct(asked)
    rows = [(r, TOKEN) for r in conn.execute("SELECT * FROM servers WHERE name = 'Plex'")]
    activity.fetch_all(rows, transport, 5.0)
    activity.fetch_all(rows, transport)  # no max_age: how the poller calls it
    activity.fetch_all(rows, transport)
    assert len(asked) == 3


def test_callers_arriving_together_make_one_call(tmp_path):
    hits = []
    lock = threading.Lock()

    def slow(request: httpx.Request) -> httpx.Response:
        with lock:
            hits.append(1)
        threading.Event().wait(0.05)
        return httpx.Response(200, json=PLEX_SESSIONS)

    c = connect(tmp_path / "horus.db")
    migrate(c)
    with c:
        c.execute("INSERT INTO servers (type, name, url, token, created_at) "
                  "VALUES ('plex', 'Plex', 'http://p', '', '2026-01-01T00:00:00+00:00')")
    rows = [(r, TOKEN) for r in c.execute("SELECT * FROM servers")]
    transport = httpx.MockTransport(slow)
    activity.forget_live()
    threads = [threading.Thread(target=activity.fetch_all, args=(rows, transport, 5.0))
               for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    c.close()
    assert len(hits) == 1


# --- old bandwidth samples are thinned ------------------------------------------------------

def add_samples(c, server_id, start, count, kbps, every=15):
    with c:
        c.executemany(
            "INSERT INTO bandwidth_samples (server_id, ts, kbps, sessions) VALUES (?, ?, ?, ?)",
            [(server_id, (start + timedelta(seconds=i * every)).isoformat(), kbps(i), 1 + i % 3)
             for i in range(count)])


def rows_of(c):
    return c.execute("SELECT server_id, ts, kbps, peak_kbps, sessions FROM bandwidth_samples "
                     "ORDER BY ts, server_id").fetchall()


def test_samples_older_than_30_days_become_five_minute_averages(conn):
    old = NOW - timedelta(days=40)
    add_samples(conn, 1, old, 40, lambda i: 1000 if i < 20 else 3000)  # 10 minutes at 15 s
    add_samples(conn, 2, old, 20, lambda i: 100 * i)                   # 5 minutes
    recent = NOW - timedelta(days=2)
    add_samples(conn, 1, recent, 40, lambda i: 500)
    assert maintenance.thin_samples(conn, NOW) == (60, 3)
    thinned = [tuple(r) for r in rows_of(conn) if r["ts"] < recent.isoformat()]
    assert thinned == [
        (1, old.isoformat(), 1000, 1000, 3),
        (2, old.isoformat(), 950, 1900, 3),
        (1, (old + timedelta(minutes=5)).isoformat(), 3000, 3000, 3),
    ]
    fresh = [r for r in rows_of(conn) if r["ts"] >= recent.isoformat()]
    assert len(fresh) == 40 and all(r["peak_kbps"] is None for r in fresh)  # untouched


def test_thinning_again_changes_nothing_and_picks_up_where_it_left_off(conn):
    old = NOW - timedelta(days=40)
    add_samples(conn, 1, old, 400, lambda i: 1000 + i)
    first = maintenance.thin_samples(conn, NOW)
    after_first = [tuple(r) for r in rows_of(conn)]
    assert first[0] == 400 and maintenance.thin_samples(conn, NOW) == (0, 0)
    assert [tuple(r) for r in rows_of(conn)] == after_first
    # Ten days later ten more days have aged past the edge; only those are worked on.
    add_samples(conn, 1, NOW - timedelta(days=25), 40, lambda i: 2000)
    assert maintenance.thin_samples(conn, NOW + timedelta(days=10)) == (40, 2)
    assert [tuple(r) for r in rows_of(conn)][:len(after_first)] == after_first


def test_the_edge_never_cuts_five_minutes_in_half(conn):
    odd_now = NOW + timedelta(minutes=3, seconds=20)  # 30 days before this is not on a boundary
    edge = (odd_now - timedelta(days=30)).replace(minute=0, second=0)
    add_samples(conn, 1, edge - timedelta(minutes=5), 40, lambda i: 1000)  # 5 min before + 5 after
    maintenance.thin_samples(conn, odd_now)
    kept = rows_of(conn)
    assert len(kept) == 1 + 20  # the earlier five minutes are one row, the later are still raw
    assert kept[0]["peak_kbps"] == 1000 and all(r["peak_kbps"] is None for r in kept[1:])


def test_the_first_thinning_writes_a_backup_first_and_later_ones_do_not(conn, tmp_path, caplog):
    db_file = tmp_path / "horus.db"
    maintenance.thin_old_samples(conn, db_file, NOW)  # nothing old: nothing to protect
    assert backup.list_backups(db_file) == []
    app_settings.set_state(conn, "samples_thinned_until", "")  # as if it had never run
    add_samples(conn, 1, NOW - timedelta(days=45), 80, lambda i: 4000)
    with caplog.at_level("INFO"):
        maintenance.thin_old_samples(conn, db_file, NOW)
    assert len(backup.list_backups(db_file)) == 1
    assert "80 became 4" in caplog.text and "backup written before" in caplog.text
    add_samples(conn, 1, NOW - timedelta(days=28), 80, lambda i: 4000)
    maintenance.thin_old_samples(conn, db_file, NOW + timedelta(days=5))
    assert len(backup.list_backups(db_file)) == 1


def test_housekeeping_thins_once_a_day(conn, tmp_path):
    cfg = app_settings.load(conn)
    offline = httpx.MockTransport(lambda r: httpx.Response(404))  # for the daily update check
    add_samples(conn, 1, NOW - timedelta(days=45), 40, lambda i: 4000)
    maintenance.run(conn, tmp_path / "horus.db", cfg, now=NOW, transport=offline)
    assert len(rows_of(conn)) == 2
    # These turn 30 days old during the next day.
    add_samples(conn, 1, NOW - timedelta(days=29, hours=12), 40, lambda i: 4000)
    maintenance.run(conn, tmp_path / "horus.db", cfg, now=NOW + timedelta(hours=1), transport=offline)
    assert len(rows_of(conn)) == 42  # not due again yet
    maintenance.run(conn, tmp_path / "horus.db", cfg, now=NOW + timedelta(days=1, minutes=1),
                    transport=offline)
    assert len(rows_of(conn)) == 4


def test_graphs_keep_the_peak_of_thinned_samples(conn):
    old = NOW - timedelta(days=40)
    add_samples(conn, 1, old, 20, lambda i: 9000 if i == 7 else 1000)
    add_samples(conn, 2, old, 20, lambda i: 2000)
    def plex(data):
        return next(b for b in data["bandwidth"] if b["server"] == "Plex")["points"]

    before = compute_stats(conn, old.date(), old.date(), UTC)  # five one-minute points
    assert max(p[2] for p in plex(before)) == 9000
    assert max(p[2] for p in before["bandwidth_total"]) == 11000  # 9000 + 2000 at that moment
    maintenance.thin_samples(conn, NOW)
    after = compute_stats(conn, old.date(), old.date(), UTC)
    assert [p[1:] for p in plex(after)] == [[1400, 9000]]  # the average, and the peak survives
    assert [p[1:] for p in after["bandwidth_total"]] == [[3400, 11000]]


def test_the_data_api_reports_the_kept_peak(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    client = TestClient(create_app(transport=httpx.MockTransport(fake_servers)))
    sid = client.post("/api/servers", json=PLEX).json()["id"]
    c = connect(tmp_path / "horus.db")
    old = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=40)
    add_samples(c, sid, old, 20, lambda i: 9000 if i == 7 else 1000)
    assert maintenance.thin_samples(c, datetime.now(UTC)) == (20, 1)
    c.close()
    # Dates in the address are read in the server's time zone and `old` is in UTC, so ask for
    # a day either side: near midnight UTC the two calendars are a day apart.
    before, after = old.date() - timedelta(days=1), old.date() + timedelta(days=2)
    r = client.get(f"/api/v1/bandwidth?from={before}&to={after}&interval=5m")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [(r["avg_kbps"], r["peak_kbps"]) for r in rows] == [(1400, 9000)]


# --- backups and restore --------------------------------------------------------------------

def test_two_backups_in_the_same_second_are_both_kept(conn, tmp_path):
    db_file = tmp_path / "horus.db"
    first = backup.write_full_backup(db_file, NOW)
    second = backup.write_full_backup(db_file, NOW)
    third = backup.write_full_backup(db_file, NOW)
    names = [first.name, second.name, third.name]
    assert names == ["horus-20261119-120000.zip", "horus-20261119-120001.zip",
                     "horus-20261119-120002.zip"]
    assert [b["name"] for b in backup.list_backups(db_file)] == sorted(names, reverse=True)


@pytest.fixture
def two_states(tmp_path, monkeypatch):
    """A running copy with server "After" and its own key, and a saved backup that holds server
    "Before" encrypted with another key. Returns (db file, backup name, the backup's key)."""
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    db_file = tmp_path / "horus.db"
    client = TestClient(create_app(transport=httpx.MockTransport(fake_servers)))
    client.post("/api/servers", json={**PLEX, "name": "Before"})
    old_key = (tmp_path / KEY_FILENAME).read_bytes()
    name = backup.write_full_backup(db_file, NOW).name
    # Now a different present: new key, new server.
    c = connect(db_file)
    with c:
        c.execute("DELETE FROM servers")
    c.close()
    (tmp_path / KEY_FILENAME).write_bytes(Fernet.generate_key())
    client.post("/api/servers", json={**PLEX, "name": "After"})
    return db_file, name, old_key


def state(db_file):
    """(server names, whether the key on disk opens their tokens)."""
    c = sqlite3.connect(db_file)
    found = c.execute("SELECT name, token FROM servers").fetchall()
    c.close()
    key = Fernet((db_file.parent / KEY_FILENAME).read_bytes().strip())
    try:
        opens = all(key.decrypt(t.encode()).decode() == TOKEN for _, t in found)
    except Exception:  # noqa: BLE001
        opens = False
    return [n for n, _ in found], opens


def leftovers(db_file):
    return sorted(p.name for p in db_file.parent.iterdir()
                  if p.name.endswith(".restore") or p.name == "restore.pending")


def test_a_restore_replaces_database_and_key_together_and_cleans_up(two_states):
    db_file, name, old_key = two_states
    assert state(db_file) == (["After"], True)
    backup.restore(db_file, name)
    assert state(db_file) == (["Before"], True)
    assert (db_file.parent / KEY_FILENAME).read_bytes().strip() == old_key.strip()
    assert leftovers(db_file) == []


@pytest.mark.parametrize("dies_at", ["database", "key", "cleanup"])
def test_a_restore_cut_short_after_its_marker_is_finished_at_the_next_start(
        two_states, monkeypatch, dies_at):
    db_file, name, _ = two_states

    def boom(*args, **kwargs):
        raise KeyboardInterrupt("power cut")

    with monkeypatch.context() as broken:
        if dies_at == "database":
            broken.setattr(backup, "_apply_staged", boom)
        elif dies_at == "key":
            broken.setattr(backup.os, "replace", boom)
        else:
            broken.setattr(backup, "_discard_staged",
                           lambda f: boom() if backup._marker(f).exists() else None)
        with pytest.raises(KeyboardInterrupt):
            backup.restore(db_file, name)
    assert "restore.pending" in leftovers(db_file)
    if dies_at == "key":  # the half-done state this is all about: new database, old key
        assert state(db_file) == (["Before"], False)

    with TestClient(create_app(transport=httpx.MockTransport(fake_servers))) as client:  # a start
        assert [s["name"] for s in client.get("/api/servers").json()] == ["Before"]
    assert state(db_file) == (["Before"], True)
    assert leftovers(db_file) == []


def test_a_restore_cut_short_before_its_marker_changes_nothing(two_states, monkeypatch):
    db_file, name, _ = two_states
    real = backup._write_private

    def dies_on_marker(path, data):
        if path.name == "restore.pending":
            raise KeyboardInterrupt("power cut")
        real(path, data)

    with monkeypatch.context() as broken:
        broken.setattr(backup, "_write_private", dies_on_marker)
        with pytest.raises(KeyboardInterrupt):
            backup.restore(db_file, name)
    assert leftovers(db_file) == ["horus.db.restore", "horus.key.restore"]
    assert backup.finish_restore(db_file) is False
    assert leftovers(db_file) == []
    assert state(db_file) == (["After"], True)


# --- a server's details have limits ---------------------------------------------------------

@pytest.mark.parametrize("change", [
    {"name": "x" * 81}, {"name": "   "}, {"url": "ftp://plex:32400"}, {"url": "plex:32400"},
    {"url": "http://"}, {"url": "javascript:alert(1)"}, {"url": "http://plex :32400"},
    {"url": "http://" + "a" * 2050}, {"token": ""}, {"token": "t" * 4097},
])
def test_a_new_server_with_bad_details_is_refused(client, change):
    assert client.post("/api/servers", json={**PLEX, **change}).status_code == 422
    assert client.get("/api/servers").json() == []
    if "url" in change or "token" in change:
        test = {"type": "plex", "url": PLEX["url"], "token": TOKEN, **change}
        assert client.post("/api/servers/test", json=test).status_code == 422


def test_a_new_servers_name_and_address_are_trimmed(client):
    r = client.post("/api/servers", json={**PLEX, "name": "  Living room  ",
                                          "url": "  HTTPS://plex.example.com:32400/  "})
    assert r.status_code == 201
    assert (r.json()["name"], r.json()["url"]) == ("Living room", "HTTPS://plex.example.com:32400")


# --- the update check says when it is being limited -----------------------------------------

@pytest.mark.parametrize(("code", "headers", "limited"), [
    (403, {"x-ratelimit-remaining": "0"}, True),
    (429, {}, True),
    (403, {"x-ratelimit-remaining": "41"}, False),
    (403, {}, False),
    (500, {}, False),
])
def test_update_check_explains_rate_limiting(code, headers, limited):
    transport = httpx.MockTransport(lambda r: httpx.Response(code, headers=headers, json={}))
    with pytest.raises(updates.UpdateCheckError) as e:
        updates.check("https://api.github.com/repos/x/y/releases/latest", transport)
    assert ("limiting requests" in str(e.value)) is limited
    if not limited:
        assert str(e.value) == "Could not check for updates"


def test_the_suite_cannot_reach_the_network(conn, tmp_path):
    """Housekeeping with default settings wants to check for updates; in tests that call is
    stopped before it leaves, and Horus treats it as a failed check."""
    before = len(NETWORK_ATTEMPTS)
    maintenance.run(conn, tmp_path / "horus.db", app_settings.load(conn), now=NOW)
    assert NETWORK_ATTEMPTS[before:] == ["api.github.com"]
    with pytest.raises(updates.UpdateCheckError):
        updates.check("https://api.github.com/repos/x/y/releases/latest")
    assert app_settings.get_state(conn, "update_latest") is None


# --- what a normal user gets on every Graphs request ----------------------------------------

def test_a_normal_user_gets_every_graphs_request_and_only_their_own_when_limited(secured):  # noqa: F811
    app = create_app(transport=httpx.MockTransport(fake_servers))
    admin = local(app)
    set_up(admin)
    sid = admin.post("/api/servers", json=PLEX).json()["id"]
    viewer_id = add_user(admin)["id"]
    c = connect(db_path())
    recent = datetime.now(UTC) - timedelta(hours=3)
    with c:
        for who in ("alice", "bob"):
            c.execute(
                "INSERT INTO history (server_id, session_key, user, title, media_type, device, "
                "platform, play_method, started_at, last_seen_at, ended_at) "
                "VALUES (?, 'k', ?, 'Film', 'movie', 'd', 'p', 'direct_play', ?, ?, ?)",
                (sid, who, recent.isoformat(), recent.isoformat(), recent.isoformat()))
    add_samples(c, sid, recent, 4, lambda i: 5000)
    c.close()
    viewer = local(app)
    sign_in(viewer, "viewer", "viewer password")
    graphs = ["/api/stats", "/api/stats?hourly=true", "/api/stats/monthly"]

    def users_seen(path):
        r = viewer.get(path)
        assert r.status_code == 200, path
        return r.json()

    for path in graphs:
        users_seen(path)
    everyone = users_seen("/api/stats")
    assert sorted(u["name"] for u in everyone["by_user"]) == ["alice", "bob"]
    assert everyone["bandwidth_total"]

    admin.put("/api/settings", json={"hide_others_activity": True})
    admin.patch(f"/api/users/{viewer_id}", json={"media_username": "alice"})
    for path in graphs[:2]:
        mine = users_seen(path)
        assert [u["name"] for u in mine["by_user"]] == ["alice"], path
        assert mine["bandwidth"] == [] or all(not b["points"] for b in mine["bandwidth"]), path
        assert mine["bandwidth_total"] == [], path  # server-wide, so it would show others' use
    monthly = users_seen("/api/stats/monthly")
    assert monthly["total"] == 1  # alice's one play, not bob's
    # And what a normal user may never open, whatever the setting.
    for path in ("/api/settings", "/api/backups", "/api/logs", "/api/users", "/api/keys", "/metrics"):
        assert viewer.get(path).status_code == 403, path
