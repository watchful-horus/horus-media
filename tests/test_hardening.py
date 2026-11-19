"""Password hashing strength, the host check while there is no login, signing out everywhere,
addresses hidden from normal users, and file permissions."""

import copy
import hashlib
import os
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import auth, listen
from horus_media.config import db_path
from horus_media.db import connect, migrate, owner_only
from horus_media.main import create_app

from .conftest import PLEX_SESSIONS, TOKEN, fake_servers
from .test_auth import PASSWORD, add_user, local, secured, set_up, sign_in  # noqa: F401

# --- password hashing -----------------------------------------------------------------------

REAL = {"n": 2**15, "r": 8, "p": 3}


def old_hash(password: str) -> str:
    """A hash as Horus made them up to 0.4.6."""
    salt = b"0123456789abcdef"
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${auth._b64(salt)}${auth._b64(digest)}"


def test_new_hashes_use_the_stronger_settings_and_verify(monkeypatch):
    monkeypatch.setattr(auth, "_SCRYPT", REAL)
    stored = auth.hash_password("a good password")
    assert stored.startswith("scrypt$32768$8$3$")
    assert auth.verify_password("a good password", stored)
    assert not auth.verify_password("a bad password", stored)
    assert not auth.needs_rehash(stored)


def test_an_old_hash_still_works_and_is_renewed_at_the_next_sign_in(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "_SCRYPT", REAL)
    conn = connect(tmp_path / "horus.db")
    migrate(conn)
    with conn:
        conn.execute("INSERT INTO users (username, password_hash, role, created_at) "
                     "VALUES ('owner', ?, 'admin', '2026-01-01T00:00:00+00:00')",
                     (old_hash(PASSWORD),))
    assert auth.authenticate(conn, "owner", "not the password") is None
    stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("scrypt$16384$8$1$")  # a wrong password renews nothing
    assert auth.authenticate(conn, "owner", PASSWORD)
    stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("scrypt$32768$8$3$")
    assert auth.authenticate(conn, "owner", PASSWORD)  # and the renewed hash works
    conn.close()


def test_at_most_two_passwords_are_hashed_at_the_same_time(monkeypatch):
    """Each hash holds 32 MB, so a burst of sign-ins must queue instead of adding up."""
    lock, running, most = threading.Lock(), 0, 0

    def slow_scrypt(*args, **kwargs):
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        time.sleep(0.02)
        with lock:
            running -= 1
        return b"x" * kwargs["dklen"]

    monkeypatch.setattr(hashlib, "scrypt", slow_scrypt)
    with ThreadPoolExecutor(12) as pool:
        hashes = list(pool.map(auth.hash_password, ["a password"] * 12))
        list(pool.map(lambda h: auth.verify_password("a password", h), hashes))
    assert most == 2


def test_passwords_have_a_maximum_length(secured):  # noqa: F811
    c = local()
    too_long = "x" * (auth.MAX_PASSWORD + 1)
    r = set_up(c, password=too_long)
    assert r.status_code == 422 and "at most" in r.json()["detail"]
    assert set_up(c, password="x" * auth.MAX_PASSWORD).status_code == 200
    assert sign_in(local(), password=too_long).status_code == 401
    assert sign_in(local(), password="x" * auth.MAX_PASSWORD).status_code == 200


# --- host check while there is no login -----------------------------------------------------

def test_which_hosts_are_known(monkeypatch):
    monkeypatch.setenv("HORUS_ALLOWED_HOSTS", " Horus.LAN , media.example.com ")
    for host in ("192.168.1.10:8000", "192.168.1.10", "[::1]:8000", "[fd00::5]", "localhost:8000",
                 "app.localhost", "horus.lan:8000", "HORUS.lan", "media.example.com", ""):
        assert listen.known_host(host), host
    for host in ("evil.example", "evil.example:8000", "horus.lan.evil.example", "192.168.1.10.nip.io",
                 "localhost.evil.example"):
        assert not listen.known_host(host), host


def test_login_off_refuses_an_unknown_name(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    monkeypatch.delenv("HORUS_ALLOWED_HOSTS")
    c = TestClient(create_app())
    for path in ("/api/servers", "/api/health", "/"):
        r = c.get(path, headers={"host": "evil.example"})
        assert r.status_code == 403, path
    assert "HORUS_ALLOWED_HOSTS=evil.example" in r.text
    assert r.headers["content-type"].startswith("text/plain")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "evil.example" in caplog.text
    assert c.post("/api/shutdown", headers={"host": "evil.example"}).status_code == 403
    assert c.get("/api/servers", headers={"host": "192.168.1.10:8000"}).status_code == 200
    assert c.get("/api/servers", headers={"host": "localhost:8000"}).status_code == 200
    monkeypatch.setenv("HORUS_ALLOWED_HOSTS", "horus.lan")
    assert c.get("/api/servers", headers={"host": "horus.lan"}).status_code == 200
    assert c.get("/api/servers", headers={"host": "evil.example"}).status_code == 403


def test_an_odd_host_header_is_not_repeated_back(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    r = TestClient(create_app()).get("/api/health", headers={"host": "<script>x</script>"})
    assert r.status_code == 403 and "script" not in r.text and "that name" in r.text


def test_first_run_refuses_an_unknown_name_until_an_admin_exists(secured, monkeypatch):  # noqa: F811
    monkeypatch.delenv("HORUS_ALLOWED_HOSTS")
    c = local()
    evil = {"host": "evil.example"}
    assert c.get("/api/auth/status", headers=evil).status_code == 403
    assert c.post("/api/auth/setup", headers=evil,
                  json={"username": "attacker", "password": PASSWORD}).status_code == 403
    by_ip = {"host": "127.0.0.1:8000"}
    assert c.post("/api/auth/setup", headers=by_ip,
                  json={"username": "owner", "password": PASSWORD}).status_code == 200
    # With a login in place any name works (a reverse proxy with its own domain, say).
    assert c.get("/api/auth/status", headers={"host": "horus.example.com"}).status_code == 200
    assert c.get("/api/servers", headers={"host": "horus.example.com"}).status_code == 200


# --- sign out everywhere --------------------------------------------------------------------

def test_sign_out_everywhere_else_keeps_only_this_browser(secured):  # noqa: F811
    app = create_app()
    first = local(app)
    assert set_up(first).status_code == 200
    second, third = local(app), local(app)
    sign_in(second)
    sign_in(third)
    assert first.post("/api/auth/sign-out-others").json() == {"ok": True, "ended": 2}
    assert first.get("/api/servers").status_code == 200
    assert second.get("/api/servers").status_code == 401
    assert third.get("/api/servers").status_code == 401
    assert first.post("/api/auth/sign-out-others").json()["ended"] == 0
    assert sign_in(local(app)).status_code == 200  # the password is unchanged


def test_a_normal_user_can_sign_out_their_own_other_browsers_only(secured):  # noqa: F811
    app = create_app()
    admin = local(app)
    set_up(admin)
    viewer = add_user(admin)
    a, b = local(app), local(app)
    sign_in(a, "viewer", "viewer password")
    sign_in(b, "viewer", "viewer password")
    assert a.post(f"/api/users/{viewer['id']}/sign-out").status_code == 403  # admins only
    assert a.post("/api/auth/sign-out-others").json()["ended"] == 1
    assert a.get("/api/activity").status_code == 200
    assert b.get("/api/activity").status_code == 401
    assert admin.get("/api/servers").status_code == 200  # nobody else is touched


def test_an_admin_can_sign_a_person_out_everywhere(secured, caplog):  # noqa: F811
    app = create_app()
    admin = local(app)
    set_up(admin)
    viewer = add_user(admin)
    a, b = local(app), local(app)
    sign_in(a, "viewer", "viewer password")
    sign_in(b, "viewer", "viewer password")
    with caplog.at_level("INFO"):
        r = admin.post(f"/api/users/{viewer['id']}/sign-out")
    assert r.json() == {"ok": True, "ended": 2}
    assert "viewer was signed out everywhere (2 sessions, by owner)" in caplog.text
    assert a.get("/api/activity").status_code == 401 and b.get("/api/activity").status_code == 401
    assert sign_in(a, "viewer", "viewer password").status_code == 200  # they can sign in again
    assert admin.post("/api/users/9999/sign-out").status_code == 404
    # On your own account the browser you are using stays signed in.
    other = local(app)
    sign_in(other)
    me = admin.get("/api/auth/status").json()["user"]["id"]
    assert admin.post(f"/api/users/{me}/sign-out").json()["ended"] == 1
    assert admin.get("/api/servers").status_code == 200
    assert other.get("/api/servers").status_code == 401


def test_a_control_key_cannot_sign_people_out(secured):  # noqa: F811
    admin = local()
    set_up(admin)
    viewer = add_user(admin)
    assert admin.put("/api/settings", json={"control_keys_enabled": True}).status_code == 200
    key = admin.post("/api/keys", json={"name": "bot", "scope": "control",
                                        "expires_in_days": 1}).json()["key"]
    bearer = {"Authorization": f"Bearer {key}"}
    script = TestClient(create_app())
    assert script.get("/api/servers", headers=bearer).status_code == 200
    assert script.post(f"/api/users/{viewer['id']}/sign-out", headers=bearer).status_code == 403
    assert script.post("/api/auth/sign-out-others", headers=bearer).status_code == 403


# --- addresses are for admins only ----------------------------------------------------------

def with_addresses(request: httpx.Request) -> httpx.Response:
    if request.url.path != "/status/sessions":
        return fake_servers(request)
    sessions = copy.deepcopy(PLEX_SESSIONS)
    first, second = sessions["MediaContainer"]["Metadata"]
    first["Player"].update(address="192.168.1.50", local=True)
    second["Player"].update(address="203.0.113.9", local=False)
    return httpx.Response(200, json=sessions)


@pytest.fixture
def watched(secured):  # noqa: F811
    """An admin and a normal user looking at two streams, one local and one remote."""
    app = create_app(transport=httpx.MockTransport(with_addresses))
    admin = local(app)
    set_up(admin)
    admin.post("/api/servers", json={"type": "plex", "name": "Plex", "url": "http://p", "token": TOKEN})
    add_user(admin)
    viewer = local(app)
    sign_in(viewer, "viewer", "viewer password")
    return admin, viewer, app


def test_admins_see_addresses_and_normal_users_only_lan_or_wan(watched):
    admin, viewer, _ = watched
    seen = [s["location"] for s in admin.get("/api/activity").json()["sessions"]]
    assert seen == ["LAN: 192.168.1.50", "WAN: 203.0.113.9"]
    r = viewer.get("/api/activity")
    assert [s["location"] for s in r.json()["sessions"]] == ["LAN", "WAN"]
    assert [s["location"] for s in r.json()["servers"][0]["sessions"]] == ["LAN", "WAN"]
    assert "192.168.1.50" not in r.text and "203.0.113.9" not in r.text


def test_a_normal_users_key_gets_no_addresses_either(watched):
    admin, viewer, app = watched
    script = TestClient(app)
    mine = viewer.post("/api/my-keys", json={"name": "mine"}).json()["key"]
    r = script.get("/api/v1/activity", headers={"X-API-Key": mine})
    assert [s["location"] for s in r.json()] == ["LAN", "WAN"]
    as_csv = script.get("/api/v1/activity?format=csv", headers={"X-API-Key": mine}).text
    assert "192.168.1.50" not in as_csv and "203.0.113.9" not in as_csv
    theirs = admin.post("/api/keys", json={"name": "grafana"}).json()["key"]
    r = script.get("/api/v1/activity", headers={"X-API-Key": theirs})
    assert [s["location"] for s in r.json()] == ["LAN: 192.168.1.50", "WAN: 203.0.113.9"]


def test_an_address_without_a_lan_or_wan_mark_is_dropped():
    assert auth.without_address("LAN: 10.0.0.2") == "LAN"
    assert auth.without_address("WAN") == "WAN"
    assert auth.without_address("player.example.com") is None  # Emby can report a bare host name
    assert auth.without_address("2001:db8::1") is None
    assert auth.without_address(None) is None


# --- file permissions -----------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="Windows has no owner-only file modes")
def test_the_database_and_its_folder_are_private_after_start(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("HORUS_DB_PATH", str(data / "horus.db"))
    data.mkdir(mode=0o755)
    conn = connect(db_path())
    migrate(conn)
    conn.close()
    os.chmod(db_path(), 0o644)
    with TestClient(create_app()) as c:  # entering runs the start-up code
        assert c.get("/api/health").status_code == 200
    assert stat.S_IMODE(db_path().stat().st_mode) == 0o600
    assert stat.S_IMODE(data.stat().st_mode) == 0o700


def test_owner_only_never_fails_the_start(tmp_path):
    owner_only(tmp_path / "missing" / "horus.db")  # nothing there: a warning at most
