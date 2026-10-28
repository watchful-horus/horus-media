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
    if request.url.host == "seerr":
        assert request.method == "GET"
        if request.headers.get("x-api-key") != "SEERR-KEY":
            return httpx.Response(403)
        if path == "/api/v1/status":
            return httpx.Response(200, json={"version": "2.1.0"})
        if path == "/api/v1/request":
            return httpx.Response(200, json={"results": [
                {"id": 1, "status": 1, "type": "movie", "createdAt": "2026-10-01T10:00:00Z",
                 "media": {"tmdbId": 11, "mediaType": "movie", "status": 2},
                 "requestedBy": {"displayName": "alice"}},
                {"id": 2, "status": 2, "type": "tv", "createdAt": "2026-10-02T10:00:00Z",
                 "media": {"tmdbId": 22, "mediaType": "tv", "status": 5},
                 "requestedBy": {"username": "bob"}},
                {"id": 3, "status": 3, "type": "movie", "createdAt": "2026-10-03T10:00:00Z",
                 "media": {"tmdbId": 33, "mediaType": "movie", "status": 1},
                 "requestedBy": {}},
            ]})
        if path == "/api/v1/movie/11":
            return httpx.Response(200, json={"title": "Big Movie"})
        if path == "/api/v1/tv/22":
            return httpx.Response(200, json={"name": "Long Show"})
        return httpx.Response(404)
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

    cfg = app_settings.update(conn, {"report_schedule": "weekly"})
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


def seerr_setup(api, key="SEERR-KEY"):
    return api.put("/api/seerr", json={"url": "http://seerr:5055", "api_key": key})


def test_seerr_key_is_never_returned_and_is_encrypted(api, tmp_path):
    r = seerr_setup(api)
    assert r.json() == {"configured": True, "url": "http://seerr:5055"}
    assert "SEERR-KEY" not in api.get("/api/seerr").text
    raw = connect(tmp_path / "horus.db").execute("SELECT api_key FROM seerr").fetchone()[0]
    assert "SEERR-KEY" not in raw


def test_seerr_requests_are_listed_with_titles_and_status(api):
    seerr_setup(api)
    assert api.post("/api/seerr/test").json() == {"ok": True, "version": "2.1.0"}
    rows = api.get("/api/seerr/requests").json()
    assert [(r["title"], r["status"], r["requested_by"]) for r in rows] == [
        ("Big Movie", "Pending", "alice"),
        ("Long Show", "Available", "bob"),
        ("TMDB 33", "Declined", "Unknown"),
    ]


def test_seerr_wrong_key_and_missing_setup(api):
    assert api.get("/api/seerr/requests").status_code == 404
    seerr_setup(api, key="wrong")
    assert api.post("/api/seerr/test").json()["error"] == "API key not accepted"
    assert api.get("/api/seerr/requests").status_code == 502


def test_seerr_address_change_keeps_the_key(api):
    seerr_setup(api)
    api.put("/api/seerr", json={"url": "http://seerr:5056"})
    assert api.get("/api/seerr").json()["url"] == "http://seerr:5056"
    assert api.put("/api/seerr", json={"url": "nope"}).status_code == 422
    api.delete("/api/seerr")
    assert api.get("/api/seerr").json()["configured"] is False
    assert api.put("/api/seerr", json={"url": "http://x"}).status_code == 422  # no key left
