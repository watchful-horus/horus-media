import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import __version__, logging_setup, poller
from horus_media.crypto import TokenCipher
from horus_media.db import connect, migrate
from horus_media.logging_setup import scrub
from horus_media.main import create_app
from horus_media.store import ServerStore

from .conftest import TOKEN, fake_servers
from .test_history import T0, emby_session
from .test_plex_link import ACCOUNT_TOKEN, SERVER_TOKEN, FakePlexTv

PLEX = {"type": "plex", "name": "Plex", "url": "http://plex:32400", "token": TOKEN}


@pytest.fixture(autouse=True)
def clean_logging():
    yield
    logging_setup.shutdown()
    poller._server_up.clear()


@pytest.fixture
def running(tmp_path, monkeypatch):
    """The real app with its startup/shutdown, so the log file is actually written."""
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    monkeypatch.setenv("HORUS_POLL_SECONDS", "0")  # no background polling noise in these tests
    plextv = FakePlexTv()
    with TestClient(create_app(transport=httpx.MockTransport(plextv))) as client:
        yield client, tmp_path, plextv


def log_text(tmp_path):
    return logging_setup.log_path(tmp_path / "horus.db").read_text(encoding="utf-8")


# --- scrubbing ------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, secret", [
    ("GET /status?X-Plex-Token=abc123XYZ&x=1", "abc123XYZ"),
    ("headers {'X-Plex-Token': 'abc123XYZ'}", "abc123XYZ"),
    ("X-Emby-Token: abc123XYZ", "abc123XYZ"),
    ('{"api_key": "abc123XYZ"}', "abc123XYZ"),
    ("connecting to http://admin:abc123XYZ@192.168.1.5:32400/x", "abc123XYZ"),
    ("Authorization: Bearer abc123XYZ", "abc123XYZ"),
    ("password=abc123XYZ", "abc123XYZ"),
    ("authToken=abc123XYZ", "abc123XYZ"),
])
def test_scrub_removes_secrets(raw, secret):
    assert secret not in scrub(raw)
    assert "***" in scrub(raw)


def test_scrub_leaves_ordinary_text_alone():
    for text in ["play started: alice - The Film on Plex (Chrome, direct_play)",
                 "Authentication failed (check token)", "server added: plex Home (http://h:32400)"]:
        assert scrub(text) == text


def test_tracebacks_are_scrubbed_and_indented(tmp_path):
    path = logging_setup.configure(tmp_path / "horus.db")
    try:
        raise RuntimeError("boom token=SECRETVALUE")
    except RuntimeError:
        logging.getLogger("horus_media.test").exception("failed")
    text = path.read_text(encoding="utf-8")
    assert "SECRETVALUE" not in text and "RuntimeError" in text
    assert "\n    Traceback" in text  # continuation lines are indented under their event


# --- file, levels, reading ------------------------------------------------------------------

def test_file_format_and_level_filtering(tmp_path):
    path = logging_setup.configure(tmp_path / "horus.db", "WARNING")
    log = logging.getLogger("horus_media.test")
    log.info("quiet")
    log.warning("loud")
    logging_setup.set_level("DEBUG")
    log.debug("detail")
    text = path.read_text(encoding="utf-8")
    assert "quiet" not in text and "loud" in text and "detail" in text
    entries = logging_setup.read_entries(tmp_path / "horus.db")
    assert [e["message"] for e in entries] == ["loud", "detail"]
    assert entries[0]["level"] == "WARNING" and entries[0]["ts"].endswith("Z")
    assert [e["message"] for e in logging_setup.read_entries(
        tmp_path / "horus.db", min_level="WARNING")] == ["loud"]
    assert [e["message"] for e in logging_setup.read_entries(
        tmp_path / "horus.db", search="DET")] == ["detail"]
    assert len(logging_setup.read_entries(tmp_path / "horus.db", limit=1)) == 1


def test_configure_twice_does_not_duplicate_lines(tmp_path):
    logging_setup.configure(tmp_path / "horus.db")
    path = logging_setup.configure(tmp_path / "horus.db")
    logging.getLogger("horus_media.test").info("once")
    assert path.read_text(encoding="utf-8").count("once") == 1


# --- end to end: events are logged, secrets are not -----------------------------------------

def test_events_logged_and_no_secret_ever_reaches_the_file(running):
    client, tmp, plextv = running
    client.put("/api/settings", json={"log_level": "DEBUG"})  # the most verbose setting
    sid = client.post("/api/servers", json=PLEX).json()["id"]
    client.post(f"/api/servers/{sid}/test")
    client.post("/api/servers/test", json={**PLEX, "token": "WRONG-TOKEN-999"})
    client.get("/api/activity")
    client.post("/api/plex/link")
    plextv.authorised = True
    client.get("/api/plex/link/77")
    client.post("/api/plex/link/77/add", json={
        "resource_id": "m1", "url": "http://192.168.1.5:32400"})
    client.post("/api/backups")
    client.delete(f"/api/servers/{sid}")

    text = log_text(tmp)
    for expected in (f"Horus {__version__} starting", "settings changed: log_level=DEBUG",
                     "server added: plex Plex", "connection test OK", "connection test failed",
                     "Plex sign-in started", "server added via Plex sign-in: Home Plex",
                     "backup written: horus-", "server removed"):
        assert expected in text, expected
    for secret in (TOKEN, "WRONG-TOKEN-999", ACCOUNT_TOKEN, SERVER_TOKEN, "FRIEND-TOKEN"):
        assert secret not in text, secret
    key = (tmp / "horus.key").read_text().strip()
    assert key not in text  # the encryption key itself is never logged


def test_shutdown_is_logged_and_handlers_released(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    monkeypatch.setenv("HORUS_POLL_SECONDS", "0")
    with TestClient(create_app()):
        pass
    assert "Horus stopping" in log_text(tmp_path)
    assert not [h for h in logging.getLogger("horus_media").handlers
                if getattr(h, "_horus_handler", False)]


# --- the logs API ---------------------------------------------------------------------------

def test_logs_api_filters_and_download(running):
    client, _tmp, _ = running
    logging.getLogger("horus_media.test").warning("needle in a haystack")
    logging.getLogger("horus_media.test").info("just info")
    everything = client.get("/api/logs").json()
    assert everything["file"] == "horus.log"
    assert any("needle" in e["message"] for e in everything["entries"])
    warnings = client.get("/api/logs", params={"level": "WARNING"}).json()["entries"]
    assert all(e["level"] in ("WARNING", "ERROR") for e in warnings)
    found = client.get("/api/logs", params={"q": "needle"}).json()["entries"]
    assert [e["message"] for e in found] == ["needle in a haystack"]
    assert client.get("/api/logs", params={"level": "LOUD"}).status_code == 422
    download = client.get("/api/logs/download")
    assert download.status_code == 200 and "needle in a haystack" in download.text


def test_logs_can_be_cleared(running):
    client, tmp, _ = running
    logging.getLogger("horus_media.test").warning("old news")
    (tmp / "logs" / "horus.log.1").write_text("an older rotated file")
    assert client.delete("/api/logs").status_code == 204
    text = log_text(tmp)
    assert "old news" not in text
    assert "log cleared by" in text  # the file keeps working, and says who cleared it
    assert not (tmp / "logs" / "horus.log.1").exists()
    logging.getLogger("horus_media.test").warning("after the clear")
    assert "after the clear" in log_text(tmp)


def test_log_level_setting_applies_immediately(running):
    client, tmp, _ = running
    client.put("/api/settings", json={"log_level": "ERROR"})
    logging.getLogger("horus_media.test").warning("hidden warning")
    assert "hidden warning" not in log_text(tmp)
    assert client.put("/api/settings", json={"log_level": "CHATTY"}).status_code == 422


# --- poller events --------------------------------------------------------------------------

def test_play_lifecycle_and_server_outages_are_logged(tmp_path, caplog):
    path = tmp_path / "h.db"
    conn = connect(path)
    migrate(conn)
    cipher = TokenCipher.for_db(path)
    ServerStore(conn, cipher).add("emby", "Emby", "http://emby", TOKEN)
    sessions = [emby_session("s1", "i1", "Film A", user="carol")]
    up = httpx.MockTransport(lambda r: httpx.Response(200, json=sessions))
    down = httpx.MockTransport(lambda r: httpx.Response(500))
    caplog.set_level(logging.DEBUG, logger="horus_media")

    poller.poll_once(conn, cipher, up, now=T0)
    poller.poll_once(conn, cipher, down, now=T0 + timedelta(minutes=1))
    poller.poll_once(conn, cipher, down, now=T0 + timedelta(minutes=2))  # still down: no repeat
    sessions.clear()
    poller.poll_once(conn, cipher, up, now=T0 + timedelta(minutes=3))

    messages = [r.getMessage() for r in caplog.records]
    assert "play started: carol - Film A on Emby (TV, direct_stream)" in messages
    assert "play ended: carol - Film A on Emby" in messages
    assert sum("is unreachable" in m for m in messages) == 1
    assert sum("reachable again" in m for m in messages) == 1
    assert not any(TOKEN in m for m in messages)
    assert datetime.now(UTC)  # (clock used only to keep the import honest)


def test_unreachable_server_log_line_has_no_url_credentials(tmp_path, caplog):
    path = tmp_path / "h.db"
    conn = connect(path)
    migrate(conn)
    cipher = TokenCipher.for_db(path)
    ServerStore(conn, cipher).add("plex", "Home", "http://user:pw123@plex:32400", TOKEN)
    caplog.set_level(logging.DEBUG, logger="horus_media")
    poller.poll_once(conn, cipher, httpx.MockTransport(fake_servers), now=T0)
    poller.poll_once(conn, cipher, httpx.MockTransport(
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused http://user:pw123@plex"))),
        now=T0)
    formatter = logging_setup.ScrubbingFormatter("%(message)s")
    assert all("pw123" not in formatter.format(r) for r in caplog.records)


def test_unreachable_says_why_and_how_long(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    conn = connect(tmp_path / "horus.db")
    migrate(conn)
    cipher = TokenCipher.for_db(tmp_path / "horus.db")
    from horus_media.store import ServerStore
    ServerStore(conn, cipher).add("emby", "Emby", "http://emby", TOKEN)
    poller._server_up.clear()
    poller._down_since.clear()
    caplog.set_level(logging.INFO, logger="horus_media")

    def refuse(request):
        raise httpx.ConnectError("connection refused to http://emby secret", request=request)

    def slow(request):
        raise httpx.ReadTimeout("took too long", request=request)

    up = httpx.MockTransport(lambda r: httpx.Response(200, json=[]))
    poller.poll_once(conn, cipher, up, now=T0)
    poller.poll_once(conn, cipher, httpx.MockTransport(refuse), now=T0 + timedelta(minutes=1))
    poller.poll_once(conn, cipher, up, now=T0 + timedelta(minutes=2))
    poller.poll_once(conn, cipher, httpx.MockTransport(slow), now=T0 + timedelta(minutes=3))
    messages = [r.getMessage() for r in caplog.records]
    assert any("is unreachable: Could not connect: connection refused or host not reachable" in m
               for m in messages)
    assert any("is unreachable: Could not connect: timed out" in m for m in messages)
    assert any("reachable again (was unreachable for " in m for m in messages)
    assert not any("secret" in m or "http://emby" in m for m in messages)


def test_duration_wording():
    assert poller._duration(8) == "8 s"
    assert poller._duration(80) == "80 s"
    assert poller._duration(300) == "5 min"
    assert poller._duration(7200) == "2 h 0 min"
