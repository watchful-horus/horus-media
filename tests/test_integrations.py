import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import notifications, poller
from horus_media.crypto import TokenCipher
from horus_media.db import connect, migrate
from horus_media.main import create_app
from horus_media.store import ServerStore

from .conftest import TOKEN
from .test_history import emby_session

HOOK = "https://hooks.example.com/secret-path-123"
SENT: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if request.url.host == "hooks.example.com" or request.url.host == "ntfy.example.com":
        SENT.append(request)
        return httpx.Response(500 if "fail" in path else 200)
    return httpx.Response(404)


@pytest.fixture
def api(tmp_path, monkeypatch):
    SENT.clear()
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    return TestClient(create_app(transport=httpx.MockTransport(handler)))


def channel(api, **over):
    body = {"name": "Phone", "kind": "webhook", "url": HOOK, "events": ["play_started"], **over}
    return api.post("/api/notifications", json=body)


def test_channel_address_is_never_returned_or_stored_in_clear(api, tmp_path):
    r = channel(api)
    assert r.status_code == 201 and r.json()["host"] == "hooks.example.com"
    listing = api.get("/api/notifications").text
    assert "secret-path-123" not in r.text and "secret-path-123" not in listing
    raw = connect(tmp_path / "horus.db").execute("SELECT url FROM notification_channels").fetchone()
    assert "secret-path-123" not in raw[0]


def test_channel_validation(api):
    assert channel(api, url="ftp://x").status_code == 422
    assert channel(api, kind="sms").status_code == 422
    assert channel(api, events=[]).status_code == 422
    assert channel(api, events=["nope"]).status_code == 422
    assert channel(api, name="  ").status_code == 422


def test_test_button_sends_and_reports_failures(api):
    cid = channel(api).json()["id"]
    assert api.post(f"/api/notifications/{cid}/test").json() == {"ok": True, "error": None}
    assert json.loads(SENT[0].content)["title"] == "Horus test"
    bad = channel(api, url="https://hooks.example.com/fail").json()["id"]
    out = api.post(f"/api/notifications/{bad}/test").json()
    assert out["ok"] is False and "500" in out["error"] and "fail" not in out["error"]


def test_each_kind_formats_its_message(api):
    for kind, url in (("discord", HOOK), ("ntfy", "https://ntfy.example.com/horus")):
        cid = channel(api, kind=kind, url=url).json()["id"]
        api.post(f"/api/notifications/{cid}/test")
    discord, ntfy = SENT
    assert "content" in json.loads(discord.content)
    assert ntfy.content == b"This is a test message from Horus." and ntfy.headers["title"]


def test_pause_edit_delete(api):
    cid = channel(api).json()["id"]
    assert api.patch(f"/api/notifications/{cid}", json={"enabled": False}).json()["enabled"] is False
    changed = api.patch(f"/api/notifications/{cid}", json={"events": ["server_down", "server_up"]})
    assert changed.json()["events"] == ["server_down", "server_up"]
    assert api.delete(f"/api/notifications/{cid}").status_code == 204
    assert api.delete(f"/api/notifications/{cid}").status_code == 404


def test_edit_changes_name_address_and_type_and_keeps_the_rest(api, tmp_path, caplog):
    cid = channel(api).json()["id"]
    path = f"/api/notifications/{cid}"

    # A new name alone: address, type and events stay.
    renamed = api.patch(path, json={"name": "  Living room  ", "url": ""}).json()
    assert renamed["name"] == "Living room" and renamed["host"] == "hooks.example.com"
    assert renamed["kind"] == "webhook" and renamed["events"] == ["play_started"]
    api.post(f"{path}/test")
    assert SENT[-1].url.path == "/secret-path-123"  # an empty address kept the stored one

    # A new address and type together; the address is stored encrypted and never returned or logged.
    with caplog.at_level("INFO", logger="horus_media"):
        moved = api.patch(path, json={"kind": "ntfy", "url": " https://ntfy.example.com/new-topic-9 ",
                                      "events": ["server_up", "server_down"]})
    assert moved.status_code == 200
    assert moved.json() == {"id": cid, "name": "Living room", "kind": "ntfy",
                            "host": "ntfy.example.com", "unreadable": False,
                            "events": ["server_down", "server_up"], "enabled": True}
    assert "new-topic-9" not in moved.text and "new-topic-9" not in caplog.text
    assert "notification channel changed: Living room (ntfy)" in caplog.text
    raw = connect(tmp_path / "horus.db").execute("SELECT url FROM notification_channels").fetchone()
    assert "new-topic-9" not in raw[0]
    api.post(f"{path}/test")
    assert SENT[-1].url.path == "/new-topic-9" and SENT[-1].headers["title"]  # sent the ntfy way


def test_edit_is_checked_like_a_new_channel_and_a_refused_edit_changes_nothing(api):
    cid = channel(api).json()["id"]
    path = f"/api/notifications/{cid}"
    before = api.get("/api/notifications").json()["channels"]
    for bad in ({"name": "  "}, {"name": "x" * 61}, {"url": "ftp://x"}, {"url": "not an address"},
                {"kind": "sms", "url": HOOK}, {"kind": "discord"},  # a new type needs a new address
                {"events": []}, {"events": ["nope"]},
                {"name": "Fine", "url": "ftp://x"}, {"name": "Fine", "events": []}):
        r = api.patch(path, json=bad)
        assert r.status_code == 422, bad
    assert "address too" in api.patch(path, json={"kind": "discord"}).json()["detail"]
    assert api.get("/api/notifications").json()["channels"] == before
    assert api.patch(path, json={"kind": "webhook"}).status_code == 200  # the same type is no change
    assert api.patch("/api/notifications/999", json={"name": "x"}).status_code == 404


def test_a_new_address_repairs_an_unreadable_channel(api, tmp_path):
    cid = channel(api, name="Old").json()["id"]
    lose_the_key(tmp_path)
    path = f"/api/notifications/{cid}"
    assert api.get("/api/notifications").json()["channels"][0]["unreadable"] is True
    assert api.patch(path, json={"kind": "discord"}).status_code == 422
    fixed = api.patch(path, json={"url": HOOK}).json()
    assert fixed["unreadable"] is False and fixed["host"] == "hooks.example.com"
    assert fixed["name"] == "Old" and fixed["events"] == ["play_started"]
    assert api.post(f"{path}/test").json() == {"ok": True, "error": None}


def test_streams_already_playing_at_the_first_read_do_not_notify_a_start(tmp_path):
    SENT.clear()
    conn = connect(tmp_path / "h.db")
    migrate(conn)
    cipher = TokenCipher(tmp_path / "h.key")
    ServerStore(conn, cipher).add("emby", "Emby", "http://emby", TOKEN)
    notifications.add_channel(conn, cipher, "All", "webhook", HOOK,
                              ["play_started", "play_ended"])
    sessions = [emby_session("s1", "i1", "Film A")]
    state = {"up": False}

    def fake(request):
        if request.url.host == "hooks.example.com":
            SENT.append(request)
            return httpx.Response(200)
        return httpx.Response(200, json=sessions) if state["up"] else httpx.Response(503)

    transport = httpx.MockTransport(fake)
    t0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    real = notifications.notify
    notifications.notify = lambda *a, **k: real(*a, **{**k, "wait": True})

    def events():
        return [json.loads(r.content)["event"] for r in SENT]

    try:
        # The server does not answer when Horus starts: the first read is the one that works.
        poller.poll_once(conn, cipher, transport, now=t0)
        state["up"] = True
        poller.poll_once(conn, cipher, transport, now=t0 + timedelta(minutes=1))
        assert events() == []  # Film A was already playing
        assert conn.execute("SELECT COUNT(*) FROM history").fetchone()[0] == 1  # still recorded
        sessions.append(emby_session("s2", "i2", "Film B"))
        poller.poll_once(conn, cipher, transport, now=t0 + timedelta(minutes=2))
        assert events() == ["play_started"]  # Film B started while Horus was watching
        sessions.clear()
        poller.poll_once(conn, cipher, transport, now=t0 + timedelta(minutes=3))
        assert events() == ["play_started", "play_ended", "play_ended"]

        # After a restore the database is another one, so the next read is a first read again.
        poller.forget()
        sessions.append(emby_session("s3", "i3", "Film C"))
        poller.poll_once(conn, cipher, transport, now=t0 + timedelta(minutes=4))
        assert events().count("play_started") == 1
    finally:
        notifications.notify = real


def test_poller_notifies_on_play_start_end_and_server_changes(tmp_path):
    SENT.clear()
    conn = connect(tmp_path / "h.db")
    migrate(conn)
    cipher = TokenCipher(tmp_path / "h.key")
    store = ServerStore(conn, cipher)
    sid = store.add("emby", "Emby", "http://emby", TOKEN).__getitem__("id")
    notifications.add_channel(conn, cipher, "All", "webhook", HOOK,
                              ["play_started", "play_ended", "server_down", "server_up"])
    notifications.add_channel(conn, cipher, "OnlyEnds", "discord", HOOK, ["play_ended"])
    sessions: list = []
    state = {"up": True}

    def fake(request):
        if request.url.host == "hooks.example.com":
            SENT.append(request)
            return httpx.Response(200)
        return httpx.Response(200, json=sessions) if state["up"] else httpx.Response(503)

    transport = httpx.MockTransport(fake)
    t0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    poller._server_up.clear()
    # synchronous sends so the test can look at them straight away
    real = notifications.notify
    notifications.notify = lambda *a, **k: real(*a, **{**k, "wait": True})
    try:
        poller.poll_once(conn, cipher, transport, now=t0 - timedelta(minutes=1))
        sessions.append(emby_session("s1", "i1", "Film A"))
        poller.poll_once(conn, cipher, transport, now=t0)
        sessions.clear()
        poller.poll_once(conn, cipher, transport, now=t0 + timedelta(minutes=1))
        state["up"] = False
        poller.poll_once(conn, cipher, transport, now=t0 + timedelta(minutes=2))
        poller.poll_once(conn, cipher, transport, now=t0 + timedelta(minutes=3))  # still down
        state["up"] = True
        poller.poll_once(conn, cipher, transport, now=t0 + timedelta(minutes=4))
    finally:
        notifications.notify = real
    webhook = [json.loads(r.content)["event"] for r in SENT if b'"event"' in r.content]
    assert webhook == ["play_started", "play_ended", "server_down", "server_up"]
    assert sum(1 for r in SENT if b'"content"' in r.content) == 1  # the discord one, only the end
    assert sid


def test_optional_alerts_fire_once_per_crossing(tmp_path):
    from horus_media import app_settings

    SENT.clear()
    conn = connect(tmp_path / "h.db")
    migrate(conn)
    cipher = TokenCipher(tmp_path / "h.key")
    ServerStore(conn, cipher).add("emby", "Emby", "http://emby", TOKEN)
    notifications.add_channel(conn, cipher, "All", "webhook", HOOK,
                              ["transcodes_high", "bandwidth_high", "new_user"])
    sessions: list = []

    def fake(request):
        if request.url.host == "hooks.example.com":
            SENT.append(request)
            return httpx.Response(200)
        return httpx.Response(200, json=sessions)

    transport = httpx.MockTransport(fake)
    t0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    poller._server_up.clear()
    poller._alert_active.clear()

    def poll(minutes):
        poller.poll_once(conn, cipher, transport, now=t0 + timedelta(minutes=minutes))

    def sent():
        return [json.loads(r.content)["event"] for r in SENT]

    real = notifications.notify
    notifications.notify = lambda *a, **k: real(*a, **{**k, "wait": True})
    try:
        sessions[:] = [emby_session("s1", "i1", "A", method="Transcode"),
                       emby_session("s2", "i2", "B", method="Transcode", user="dave")]
        poll(0)
        assert sent() == []  # everything is off by default

        app_settings.update(conn, {"alert_transcodes_over": 1, "alert_new_user": True})
        poll(1)
        poll(2)  # still over the limit: not repeated
        assert sorted(sent()) == ["transcodes_high"]  # carol and dave are already in history

        sessions[:] = [emby_session("s3", "i3", "C", user="erin")]
        poll(3)
        assert sorted(sent()) == ["new_user", "transcodes_high"]

        sessions[:] = [emby_session("s1", "i1", "A", method="Transcode"),
                       emby_session("s2", "i2", "B", method="Transcode")]
        poll(4)  # dropped below, then crossed again
        assert sent().count("transcodes_high") == 2
    finally:
        notifications.notify = real


def test_report_text_csv_and_scheduled_send(tmp_path):
    from horus_media import app_settings, maintenance, reports

    SENT.clear()
    db = tmp_path / "h.db"
    conn = connect(db)
    migrate(conn)
    cipher = TokenCipher.for_db(db)
    ServerStore(conn, cipher).add("emby", "Emby", "http://emby", TOKEN)
    notifications.add_channel(conn, cipher, "All", "webhook", HOOK, ["report"])
    transport = httpx.MockTransport(lambda r: (SENT.append(r), httpx.Response(200))[1])
    t0 = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)
    with conn:
        conn.execute(
            "INSERT INTO history (server_id, session_key, user, title, media_type, device, "
            "platform, play_method, started_at, last_seen_at, ended_at) VALUES "
            "(1, 'k', 'carol', 'Film', 'movie', 'TV', 'Roku', 'transcode', ?, ?, ?)",
            (t0.isoformat(),) * 3)
    start, end = t0.date() - timedelta(days=2), t0.date() + timedelta(days=2)
    built = reports.build(conn, start, end, UTC)
    assert built["total_plays"] == 1 and built["transcode_percent"] == 100
    assert "carol 1" in reports.to_text(built)
    csv_text = reports.to_csv(built)
    assert csv_text.splitlines()[0] == "date,Emby,total"
    assert "2026-03-10,1,1" in csv_text

    cfg = app_settings.update(conn, {"report_schedule": "weekly", "update_check": False})
    maintenance.run(conn, db, cfg, now=t0, transport=transport)
    assert SENT == []  # switching it on does not send at once
    maintenance.run(conn, db, cfg, now=t0 + timedelta(days=3), transport=transport)
    assert SENT == []  # not due yet
    maintenance.run(conn, db, cfg, now=t0 + timedelta(days=7), transport=transport)
    assert len(SENT) == 1
    assert json.loads(SENT[0].content)["event"] == "report"
    maintenance.run(conn, db, cfg, now=t0 + timedelta(days=8), transport=transport)
    assert len(SENT) == 1


def test_report_endpoints_and_csv(api):
    r = api.get("/api/report", params={"start": "2026-03-01", "end": "2026-03-07"})
    assert r.status_code == 200 and r.json()["total_plays"] == 0
    assert "Nothing was played" in r.json()["text"]
    assert api.get("/api/report", params={"start": "2026-03-07", "end": "2026-03-01"}
                   ).status_code == 422
    c = api.get("/api/report", params={"start": "2026-03-01", "end": "2026-03-02", "format": "csv"})
    assert c.headers["content-type"].startswith("text/csv")
    assert api.post("/api/report/send").status_code == 204


def test_unreachable_at_startup_is_not_a_notification(tmp_path):
    SENT.clear()
    conn = connect(tmp_path / "h.db")
    migrate(conn)
    cipher = TokenCipher(tmp_path / "h.key")
    ServerStore(conn, cipher).add("emby", "Emby", "http://emby", TOKEN)
    notifications.add_channel(conn, cipher, "All", "webhook", HOOK, ["server_down"])
    poller._server_up.clear()
    real = notifications.notify
    notifications.notify = lambda *a, **k: real(*a, **{**k, "wait": True})
    try:
        poller.poll_once(conn, cipher, httpx.MockTransport(
            lambda r: httpx.Response(503) if r.url.host == "emby" else (SENT.append(r) or
                                                                        httpx.Response(200))))
    finally:
        notifications.notify = real
    assert SENT == []


def test_a_stored_seerr_connection_is_forgotten_when_upgrading(tmp_path):
    from horus_media.migrations import MIGRATIONS

    conn = connect(tmp_path / "old.db")
    # An install from before the Seerr view was removed: only the migrations up to and including
    # the one that created the table (the one before the DROP), and a stored connection.
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    upto = next(n for n, sql in enumerate(MIGRATIONS, 1) if "DROP TABLE IF EXISTS seerr" in sql) - 1
    for version, sql in enumerate(MIGRATIONS[:upto], start=1):
        for statement in sql.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    conn.execute("INSERT INTO seerr VALUES (1, 'http://seerr:5055', 'encrypted-key')")
    conn.commit()
    migrate(conn)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'seerr'").fetchone() is None


# --- a channel whose address can no longer be read ------------------------------------------------

def lose_the_key(tmp_path):
    """As after restoring a database without its key: a new key that cannot read old addresses."""
    from cryptography.fernet import Fernet

    (tmp_path / "horus.key").write_bytes(Fernet.generate_key())
    notifications._warned.clear()


def test_an_unreadable_channel_is_marked_and_does_not_break_the_page(api, tmp_path, caplog):
    old = channel(api, name="Old").json()["id"]
    lose_the_key(tmp_path)
    new = channel(api, name="New").json()["id"]  # added with the current key
    with caplog.at_level("WARNING", logger="horus_media"):
        listing = api.get("/api/notifications")
        api.get("/api/notifications")
    assert listing.status_code == 200
    by_id = {c["id"]: c for c in listing.json()["channels"]}
    assert by_id[old]["unreadable"] is True and by_id[old]["host"] == ""
    assert by_id[new]["unreadable"] is False and by_id[new]["host"] == "hooks.example.com"
    assert caplog.text.count("its address cannot be read") == 1  # said once, not on every look

    tested = api.post(f"/api/notifications/{old}/test").json()
    assert tested["ok"] is False and "add it again" in tested["error"]
    assert api.post(f"/api/notifications/{new}/test").json() == {"ok": True, "error": None}
    # It can still be paused, changed and removed.
    assert api.patch(f"/api/notifications/{old}", json={"enabled": False}).json()["enabled"] is False
    assert api.patch(f"/api/notifications/{old}", json={"events": ["server_down"]}).status_code == 200
    assert api.delete(f"/api/notifications/{old}").status_code == 204


def test_an_unreadable_channel_does_not_stop_the_others_or_the_poll(api, tmp_path):
    channel(api, name="Old")
    lose_the_key(tmp_path)
    channel(api, name="New")
    conn = connect(tmp_path / "horus.db")
    migrate(conn)
    cipher = TokenCipher.for_db(tmp_path / "horus.db")
    SENT.clear()
    notifications.notify(conn, cipher, "play_started", "t", "m",
                         httpx.MockTransport(handler), wait=True)
    assert len(SENT) == 1  # the readable one got it; the other was skipped, not raised
