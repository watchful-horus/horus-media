"""Sign-in limits that do not lock everybody out, the visitor's real address behind a trusted
reverse proxy, the Secure cookie flag over https, and what a normal user may read on Graphs."""

import logging

import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from horus_media import auth, listen
from horus_media.main import create_app

from .test_auth import OWNER, PASSWORD, add_user, set_up, sign_in

WRONG = "not the password"


@pytest.fixture
def app(tmp_path, monkeypatch):
    """One running Horus with the login on, an admin (owner) and a normal user (viewer)."""
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    made = create_app()
    admin = TestClient(made, client=("127.0.0.1", 50000))
    assert set_up(admin).status_code == 200
    add_user(admin)
    return made


def browser(app, address: str) -> TestClient:
    return TestClient(app, client=(address, 50000))


# --- what a normal user may read on Graphs ------------------------------------------------------

def test_a_normal_user_gets_every_graph(app):
    viewer = browser(app, "192.168.1.20")
    assert sign_in(viewer, "viewer", "viewer password").status_code == 200
    assert viewer.get("/api/stats").status_code == 200
    assert viewer.get("/api/stats", params={"hourly": "true", "start": "2026-01-01", "end": "2026-01-02"}).status_code == 200
    monthly = viewer.get("/api/stats/monthly")
    assert monthly.status_code == 200 and "months" in monthly.json()
    assert viewer.get("/api/activity").status_code == 200
    assert viewer.get("/api/settings").status_code == 403  # still not an admin


# --- sign-in limits -----------------------------------------------------------------------------

def test_wrong_passwords_block_that_username_only(app):
    shared = browser(app, "10.0.0.2")  # e.g. a reverse proxy: everyone arrives from this address
    for _ in range(5):
        assert sign_in(shared, OWNER, WRONG).status_code == 401
    assert sign_in(shared, OWNER).status_code == 429          # even the right password, for a minute
    assert sign_in(shared, OWNER.upper()).status_code == 429  # usernames ignore case
    assert sign_in(shared, f"  {OWNER} ").status_code == 429
    # Someone else at the same address is not locked out with them.
    assert sign_in(shared, "viewer", "viewer password").status_code == 200
    # And the owner can still sign in from another address.
    assert sign_in(browser(app, "10.0.0.3"), OWNER).status_code == 200


def test_a_right_password_clears_the_count(app):
    c = browser(app, "10.0.0.2")
    for _ in range(4):
        assert sign_in(c, OWNER, WRONG).status_code == 401
    assert sign_in(c, OWNER).status_code == 200
    for _ in range(4):
        assert sign_in(c, OWNER, WRONG).status_code == 401
    assert sign_in(c, OWNER).status_code == 200


def test_guessing_across_many_usernames_is_stopped_per_address(app):
    guesser = browser(app, "203.0.113.9")
    codes = [sign_in(guesser, f"name{i}", WRONG).status_code for i in range(31)]
    assert codes[:30] == [401] * 30 and codes[30] == 429
    assert sign_in(guesser, OWNER).status_code == 429          # that address is shut out for a minute
    assert sign_in(browser(app, "203.0.113.10"), OWNER).status_code == 200  # nobody else is


def test_the_limiter_forgets_old_failures_and_does_not_grow():
    now = [0.0]
    limiter = auth.LoginLimiter(limit=2, window=60, clock=lambda: now[0])
    assert limiter.blocked("a") is False and limiter._failures == {}  # asking leaves no trace
    limiter.fail("a")
    limiter.fail("a")
    limiter.fail(("b", "name"))
    assert limiter.blocked("a") is True and limiter.blocked(("b", "name")) is False
    now[0] = 61.0
    assert limiter.blocked("a") is False and limiter.blocked(("b", "name")) is False
    assert limiter._failures == {}
    limiter.SWEEP_ABOVE = 10
    for i in range(50):
        limiter.fail(i)
        now[0] += 30  # each one is stale two failures later
    assert len(limiter._failures) <= 12


# --- the visitor's real address behind a trusted proxy -------------------------------------------

def test_trusted_proxies_setting(monkeypatch):
    monkeypatch.delenv("HORUS_TRUSTED_PROXIES", raising=False)
    assert listen.trusted_proxies() is None
    monkeypatch.setenv("HORUS_TRUSTED_PROXIES", " 10.0.0.5 , 172.18.0.0/16,fd00::/8 ,")
    assert listen.trusted_proxies() == "10.0.0.5,172.18.0.0/16,fd00::/8"
    monkeypatch.setenv("HORUS_TRUSTED_PROXIES", "*")
    assert listen.trusted_proxies() == "*"
    for bad in ("proxy.local", "10.0.0.300", "172.18.0.0/40", "10.0.0.5;10.0.0.6"):
        monkeypatch.setenv("HORUS_TRUSTED_PROXIES", bad)
        with pytest.raises(ValueError):
            listen.trusted_proxies()


def test_a_bad_trusted_proxies_value_stops_the_start(tmp_path, monkeypatch):
    from horus_media import __main__ as entry

    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    monkeypatch.setenv("HORUS_TRUSTED_PROXIES", "my-proxy")
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: pytest.fail("must not start"))
    with pytest.raises(SystemExit) as stopped:
        entry.serve()
    assert "HORUS_TRUSTED_PROXIES" in str(stopped.value)


def test_the_setting_reaches_the_web_server(tmp_path, monkeypatch):
    from horus_media import __main__ as entry

    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    monkeypatch.setattr(entry.listen, "wait_until_free", lambda host, port: True)
    started = {}
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: started.update(k))
    monkeypatch.setenv("HORUS_TRUSTED_PROXIES", "172.18.0.0/16")
    entry.serve()
    assert started["forwarded_allow_ips"] == "172.18.0.0/16"
    monkeypatch.delenv("HORUS_TRUSTED_PROXIES")
    entry.serve()
    assert started["forwarded_allow_ips"] is None  # the web server's own default: this machine only


def test_behind_a_trusted_proxy_each_visitor_is_counted_separately(app, caplog):
    """What the web server does with HORUS_TRUSTED_PROXIES=172.18.0.0/16."""
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="172.18.0.0/16")
    proxy = TestClient(proxied, client=("172.18.0.2", 40000))

    def visitor(address: str, password: str = PASSWORD):
        return proxy.post("/api/auth/login", json={"username": OWNER, "password": password},
                          headers={"X-Forwarded-For": address, "X-Forwarded-Proto": "https"})

    with caplog.at_level(logging.WARNING, logger="horus_media"):
        for _ in range(5):
            assert visitor("198.51.100.7", WRONG).status_code == 401
    assert "failed login from 198.51.100.7" in caplog.text and "172.18.0.2" not in caplog.text
    assert visitor("198.51.100.7").status_code == 429
    assert visitor("198.51.100.8").status_code == 200  # the same account from another visitor


def test_an_untrusted_address_cannot_pretend_to_be_someone_else(app, caplog):
    proxied = ProxyHeadersMiddleware(app, trusted_hosts="172.18.0.0/16")
    stranger = TestClient(proxied, client=("203.0.113.50", 40000))
    with caplog.at_level(logging.WARNING, logger="horus_media"):
        for i in range(5):
            r = stranger.post("/api/auth/login", json={"username": OWNER, "password": WRONG},
                              headers={"X-Forwarded-For": f"198.51.100.{i}"})
            assert r.status_code == 401
    assert "failed login from 203.0.113.50" in caplog.text  # its own address, not the claimed one
    blocked = stranger.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD},
                            headers={"X-Forwarded-For": "198.51.100.99"})
    assert blocked.status_code == 429  # changing the header does not reset the count


# --- the Secure flag on the session cookie -------------------------------------------------------

def cookie(response) -> str:
    return response.headers.get("set-cookie", "").lower()


def test_the_cookie_is_secure_when_the_visitor_used_https(app):
    plain = sign_in(browser(app, "10.0.0.2"))
    assert plain.status_code == 200 and "secure" not in cookie(plain)
    assert "httponly" in cookie(plain) and "samesite=lax" in cookie(plain)
    for value in ("https", "HTTPS", "https, http"):
        behind = browser(app, "10.0.0.2").post(
            "/api/auth/login", json={"username": OWNER, "password": PASSWORD},
            headers={"X-Forwarded-Proto": value})
        assert behind.status_code == 200 and "; secure" in cookie(behind), value
    for value in ("http", "http, https", ""):
        behind = browser(app, "10.0.0.2").post(
            "/api/auth/login", json={"username": OWNER, "password": PASSWORD},
            headers={"X-Forwarded-Proto": value})
        assert "secure" not in cookie(behind), value
    direct = TestClient(app, base_url="https://testserver", client=("10.0.0.2", 50000))
    assert "; secure" in cookie(sign_in(direct))
