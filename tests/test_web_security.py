"""Browser-facing protections: changes only from Horus's own pages, security headers on every
response, posters that can only ever be pictures, and no generated API pages."""

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media.clients import EmbyClient, PlexClient
from horus_media.main import content_security_policy, create_app, from_another_origin

from .conftest import TOKEN, fake_servers
from .test_auth import OWNER, PASSWORD, set_up

HOST = "testserver"  # what the test client sends as Host
EMBY = {"type": "emby", "name": "E", "url": "http://e.test", "token": TOKEN}


@pytest.fixture
def admin(tmp_path, monkeypatch):
    """A signed-in admin on an install with the login switched on."""
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    c = TestClient(create_app(transport=httpx.MockTransport(fake_servers)), client=("127.0.0.1", 50000))
    set_up(c)
    return c


# --- changes only from Horus's own pages -------------------------------------------------------

@pytest.mark.parametrize("headers", [
    {"Origin": "http://evil.example"},
    {"Origin": f"http://{HOST}:9000"},            # another web app on the same host, other port
    {"Origin": f"https://{HOST}"},                # same name, other scheme and so other port
    {"Origin": f"http://sub.{HOST}"},
    {"Origin": "null"},                           # a sandboxed page or a local file
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},              # same site is not same origin
    {"Sec-Fetch-Site": "cross-site", "Origin": f"http://{HOST}"},  # the browser's verdict wins
])
def test_changes_from_another_origin_are_refused(admin, headers):
    assert admin.post("/api/servers", json=EMBY, headers=headers).status_code == 403
    assert admin.post("/api/backups", headers=headers).status_code == 403       # no body needed
    assert admin.post("/api/shutdown", headers=headers).status_code == 403
    assert admin.put("/api/settings", json={"log_level": "DEBUG"}, headers=headers).status_code == 403
    assert admin.delete("/api/logs", headers=headers).status_code == 403
    upload = admin.post("/api/backups/upload", content=b"PK", headers={
        **headers, "Content-Type": "application/octet-stream"})
    assert upload.status_code == 403
    assert admin.get("/api/servers").json() == []          # nothing was added
    assert admin.get("/api/backups").json()["items"] == []  # and no backup was made
    assert admin.get("/api/settings").json()["settings"]["log_level"] == "INFO"


@pytest.mark.parametrize("headers", [
    {},                                           # not a web page: curl, scripts, API keys
    {"Origin": f"http://{HOST}"},                 # Horus's own page over http
    {"Origin": f"http://{HOST.upper()}"},
    {"Sec-Fetch-Site": "same-origin"},            # Horus's own page over https or on localhost
    {"Sec-Fetch-Site": "same-origin", "Origin": "https://horus.example"},  # behind a proxy
    {"Sec-Fetch-Site": "none"},
])
def test_changes_from_horus_itself_are_accepted(admin, headers):
    assert admin.post("/api/servers", json=EMBY, headers=headers).status_code == 201
    assert admin.post("/api/backups", headers=headers).status_code == 201


def test_reading_is_not_affected_by_the_origin(admin):
    for headers in ({"Origin": "http://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
        assert admin.get("/api/settings", headers=headers).status_code == 200
        assert admin.get("/api/health", headers=headers).status_code == 200


def test_the_refusal_says_why_and_is_logged(admin, caplog):
    with caplog.at_level(logging.WARNING, logger="horus_media"):
        r = admin.post("/api/shutdown", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and "did not come from Horus" in r.json()["detail"]
    assert "refused POST /api/shutdown" in caplog.text and "evil.example" in caplog.text


def test_signing_in_from_another_site_is_refused(admin):
    r = admin.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD},
                   headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_it_also_protects_a_copy_with_the_login_turned_off(client):
    """No cookie is needed there, so any page the person visits could otherwise change things."""
    r = client.post("/api/servers", json=EMBY, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert client.post("/api/servers", json=EMBY).status_code == 201


def test_an_api_key_is_not_a_browser_and_keeps_working(admin):
    admin.put("/api/settings", json={"control_keys_enabled": True})
    key = admin.post("/api/keys", json={"name": "k", "scope": "control", "expires_in_days": 1}).json()["key"]
    robot = TestClient(admin.app, client=("127.0.0.1", 50001))
    r = robot.post("/api/servers", json=EMBY, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 201


def test_from_another_origin_reads_the_headers_it_should():
    assert from_another_origin({}) is False
    assert from_another_origin({"host": "h:8000", "origin": "http://h:8000"}) is False
    assert from_another_origin({"host": "h:8000", "origin": "http://h:9000"}) is True
    assert from_another_origin({"host": "h:8000", "origin": "http://h"}) is True
    assert from_another_origin({"host": "[::1]:8000", "origin": "http://[::1]:8000"}) is False
    assert from_another_origin({"origin": "http://h"}) is True          # no Host to compare with
    # Same name, but http and https are different origins (and different ports).
    assert from_another_origin({"host": "h", "origin": "https://h"}) is True
    assert from_another_origin({"host": "h", "origin": "https://h"}, "https") is False
    assert from_another_origin({"host": "h", "origin": "http://h"}, "https") is True
    # An old browser behind a proxy that ends https: the proxy says how the request arrived.
    proxied = {"host": "horus.example", "origin": "https://horus.example", "x-forwarded-proto": "https"}
    assert from_another_origin(proxied) is False
    assert from_another_origin({**proxied, "x-forwarded-proto": "https, http"}) is False
    assert from_another_origin({**proxied, "origin": "https://evil.example"}) is True
    assert from_another_origin({"host": "h", "origin": ""}) is True


# --- bodies must say what they are --------------------------------------------------------------

def test_a_body_that_is_not_json_is_refused(admin):
    """A page on another site can send text or a form without the browser asking first."""
    raw = b'{"type":"emby","name":"E","url":"http://e.test","token":"t"}'
    for kind in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data"):
        r = admin.post("/api/servers", content=raw, headers={"Content-Type": kind})
        assert r.status_code == 415, kind
    bare = admin.build_request("POST", "/api/servers", content=raw)
    bare.headers.pop("content-type", None)
    assert admin.send(bare).status_code == 415
    assert admin.get("/api/servers").json() == []
    ok = admin.post("/api/servers", content=raw, headers={"Content-Type": "application/json; charset=utf-8"})
    assert ok.status_code == 201


def test_a_backup_upload_must_be_sent_as_a_file(admin):
    for kind in ("text/plain", "application/x-www-form-urlencoded", "application/json"):
        r = admin.post("/api/backups/upload", content=b"PK-not-a-backup", headers={"Content-Type": kind})
        assert r.status_code == 415, kind
    r = admin.post("/api/backups/upload", content=b"PK-not-a-backup",
                   headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 422  # accepted as a file, then refused because it is not a backup


def test_the_first_run_restore_must_be_sent_as_a_file_too(tmp_path, monkeypatch):
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    fresh = TestClient(create_app(), client=("127.0.0.1", 50000))
    assert fresh.post("/api/auth/restore", content=b"PK", headers={"Content-Type": "text/plain"}).status_code == 415
    assert fresh.post("/api/auth/restore", content=b"PK", headers={
        "Content-Type": "application/octet-stream", "Origin": "http://evil.example"}).status_code == 403


# --- headers --------------------------------------------------------------------------------------

def test_every_response_carries_the_security_headers(admin):
    anonymous = TestClient(admin.app, client=("127.0.0.1", 50002))
    answers = [
        admin.get("/api/settings"),                                   # data
        admin.get("/api/nothing-here"),                               # 404
        anonymous.get("/api/settings"),                               # 401 from the login gate
        admin.post("/api/shutdown", headers={"Origin": "http://evil.example"}),  # 403
        anonymous.get("/api/health"),
    ]
    for r in answers:
        assert r.headers["x-content-type-options"] == "nosniff", r.url
        assert r.headers["x-frame-options"] == "DENY", r.url
        assert r.headers["referrer-policy"] == "no-referrer", r.url
        policy = r.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in policy and "script-src 'self'" in policy, r.url
        assert "object-src 'none'" in policy and "base-uri 'self'" in policy, r.url


def test_the_policy_never_allows_inline_or_outside_scripts():
    policy = content_security_policy({"host": "horus.lan:8000"})
    scripts = next(p for p in policy.split("; ") if p.startswith("script-src"))
    assert scripts == "script-src 'self'"
    assert "unsafe-eval" not in policy and "*;" not in policy.replace(":*", "")
    # After a restart on another port the page looks for Horus on the same host.
    assert "connect-src 'self' http://horus.lan:* https://horus.lan:*;" in policy


@pytest.mark.parametrize("host", ["evil.example; script-src *", "a b", "x'y", "", "h/..", "h; default-src *:1"])
def test_a_strange_host_header_cannot_add_to_the_policy(host):
    policy = content_security_policy({"host": host})
    assert "connect-src 'self';" in policy
    assert "script-src *" not in policy and "default-src *" not in policy


def test_ipv6_and_portless_hosts_are_handled():
    assert "http://[::1]:*" in content_security_policy({"host": "[::1]:8000"})
    assert "http://[::1]:*" in content_security_policy({"host": "[::1]"})
    assert "http://horus.lan:*" in content_security_policy({"host": "horus.lan"})


# --- posters can only be pictures ---------------------------------------------------------------

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def serving(content_type: str, body: bytes = b"bytes") -> httpx.MockTransport:
    return httpx.MockTransport(
        lambda request: httpx.Response(200, content=body, headers={"content-type": content_type}))


@pytest.mark.parametrize("content_type", ["image/svg+xml", "image/svg+xml; charset=utf-8", "text/html",
                                          "image/x-icon", "image/tiff", "application/octet-stream", ""])
def test_a_poster_that_is_not_a_plain_picture_is_refused(content_type):
    for client, path in ((PlexClient("P", "http://x", TOKEN, transport=serving(content_type, SVG)),
                          "/library/metadata/1/thumb/5"),
                         (EmbyClient("E", "http://x", TOKEN, transport=serving(content_type, SVG)),
                          "/Items/s1/Images/Primary")):
        with pytest.raises(ValueError):
            client.get_image(path)


@pytest.mark.parametrize("content_type", ["image/jpeg", "image/png", "image/webp", "image/gif", "IMAGE/JPEG"])
def test_plain_pictures_are_passed_on(content_type):
    client = EmbyClient("E", "http://x", TOKEN, transport=serving(content_type))
    assert client.get_image("/Items/s1/Images/Primary")[0] == b"bytes"


def test_the_poster_address_is_sandboxed_and_refuses_svg(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    kind = {"type": "image/jpeg"}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=SVG, headers={"content-type": kind["type"]})

    c = TestClient(create_app(transport=httpx.MockTransport(handler)))
    sid = c.post("/api/servers", json=EMBY).json()["id"]
    good = c.get(f"/api/servers/{sid}/poster", params={"path": "/Items/abc/Images/Primary"})
    assert good.status_code == 200
    assert good.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert good.headers["x-content-type-options"] == "nosniff"
    kind["type"] = "image/svg+xml"
    bad = c.get(f"/api/servers/{sid}/poster", params={"path": "/Items/abc/Images/Primary"})
    assert bad.status_code == 404 and b"<script" not in bad.content


# --- no generated API pages ---------------------------------------------------------------------

def test_the_generated_api_pages_are_gone(admin, client):
    anonymous = TestClient(admin.app, client=("127.0.0.1", 50003))
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        for who in (admin, anonymous, client):
            r = who.get(path)
            assert r.status_code == 404, path
            assert "swagger" not in r.text.lower() and "openapi" not in r.text.lower(), path
