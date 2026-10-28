"""Control keys: API keys that can run Horus as their admin owner (off unless an admin allows
them, always expiring, and never able to touch users, keys or sign-in)."""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from horus_media.main import create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def secured(tmp_path, monkeypatch):
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    return tmp_path


def local() -> TestClient:
    return TestClient(create_app(), client=("127.0.0.1", 50000))


def admin() -> TestClient:
    c = local()
    assert c.post("/api/auth/setup", json={"username": "owner", "password": PASSWORD}).status_code == 200
    return c


def switch(c: TestClient, on: bool = True) -> None:
    assert c.put("/api/settings", json={"control_keys_enabled": on}).status_code == 200


def control_key(c: TestClient, name: str = "tester", days: int = 7, path: str = "/api/keys") -> dict:
    r = c.post(path, json={"name": name, "scope": "control", "expires_in_days": days})
    assert r.status_code == 201, r.text
    return r.json()


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def test_control_keys_are_off_until_an_admin_turns_them_on(secured):
    c = admin()
    refused = c.post("/api/keys", json={"name": "t", "scope": "control", "expires_in_days": 7})
    assert refused.status_code == 409 and "switched off" in refused.json()["detail"]
    switch(c)
    assert control_key(c)["scope"] == "control"


def test_a_control_key_needs_an_expiry_from_the_allowed_list(secured):
    c = admin()
    switch(c)
    for days in (None, 0, 2, 365):
        r = c.post("/api/keys", json={"name": "t", "scope": "control", "expires_in_days": days})
        assert r.status_code == 422, days
    made = control_key(c, days=1)
    expires = datetime.fromisoformat(made["expires_at"])
    assert timedelta(hours=23) < expires - datetime.now(UTC) < timedelta(hours=25)


def test_a_control_key_runs_the_system_like_its_admin(secured):
    c = admin()
    switch(c)
    robot = local()  # no login cookie: only the key
    headers = bearer(control_key(c)["key"])
    assert robot.get("/api/settings", headers=headers).status_code == 200
    assert robot.get("/api/servers", headers=headers).status_code == 200
    assert robot.get("/api/logs", headers=headers).status_code == 200
    assert robot.put("/api/settings", json={"log_level": "INFO"}, headers=headers).status_code == 200
    made = robot.post("/api/backups", headers=headers)
    assert made.status_code == 201
    assert made.json()["name"] in [b["name"] for b in robot.get("/api/backups", headers=headers).json()["items"]]
    assert robot.get("/api/v1/servers", headers=headers).status_code == 200  # the data API too
    assert robot.get("/api/settings", headers={"X-API-Key": headers["Authorization"][7:]}).status_code == 200


def test_a_control_key_cannot_manage_users_keys_or_sign_in(secured):
    c = admin()
    switch(c)
    robot = local()
    headers = bearer(control_key(c)["key"])
    blocked = [
        ("GET", "/api/users", None), ("POST", "/api/users", {"username": "x", "password": "long enough pw"}),
        ("PATCH", "/api/users/1", {"role": "user"}), ("DELETE", "/api/users/1", None),
        ("GET", "/api/keys", None), ("POST", "/api/keys", {"name": "more"}),
        ("DELETE", "/api/keys/1", None), ("GET", "/api/my-keys", None),
        ("POST", "/api/my-keys", {"name": "more"}),
        ("POST", "/api/auth/password", {"current": PASSWORD, "new": "another long password"}),
    ]
    for method, path, body in blocked:
        r = robot.request(method, path, json=body, headers=headers)
        assert r.status_code == 403, (method, path)
        assert "users or keys" in r.json()["detail"], (method, path)
    assert [u["username"] for u in c.get("/api/users").json()] == ["owner"]  # nothing changed


def test_a_control_key_cannot_switch_control_keys_off_or_on(secured):
    c = admin()
    switch(c)
    robot = local()
    headers = bearer(control_key(c)["key"])
    r = robot.put("/api/settings", json={"control_keys_enabled": False}, headers=headers)
    assert r.status_code == 403
    assert c.get("/api/settings").json()["settings"]["control_keys_enabled"] is True


def test_switching_control_keys_off_stops_them_everywhere(secured):
    c = admin()
    switch(c)
    robot = local()
    headers = bearer(control_key(c)["key"])
    assert robot.get("/api/settings", headers=headers).status_code == 200
    switch(c, False)
    assert robot.get("/api/settings", headers=headers).status_code == 401
    assert robot.get("/api/v1/servers", headers=headers).status_code == 401
    switch(c)  # the key itself was not deleted: back on, it works again
    assert robot.get("/api/settings", headers=headers).status_code == 200


def test_an_expired_control_key_stops_working(secured):
    c = admin()
    switch(c)
    robot = local()
    made = control_key(c)
    headers = bearer(made["key"])
    assert robot.get("/api/settings", headers=headers).status_code == 200
    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with sqlite3.connect(secured / "horus.db") as db:
        db.execute("UPDATE api_keys SET expires_at = ? WHERE id = ?", (past, made["id"]))
    assert robot.get("/api/settings", headers=headers).status_code == 401
    assert robot.get("/api/v1/servers", headers=headers).status_code == 401


def test_ordinary_keys_are_unchanged(secured):
    c = admin()
    switch(c)
    made = c.post("/api/keys", json={"name": "Grafana"}).json()
    assert made["scope"] == "read" and made["expires_at"] is None
    robot = local()
    headers = bearer(made["key"])
    assert robot.get("/api/v1/servers", headers=headers).status_code == 200
    assert robot.get("/api/settings", headers=headers).status_code == 401  # read keys never control


def test_only_an_admin_can_make_a_control_key(secured):
    c = admin()
    switch(c)
    assert c.post("/api/users", json={"username": "viewer", "password": "viewer password"}).status_code == 201
    viewer = local()
    assert viewer.post("/api/auth/login", json={"username": "viewer", "password": "viewer password"}).status_code == 200
    r = viewer.post("/api/my-keys", json={"name": "t", "scope": "control", "expires_in_days": 7})
    assert r.status_code == 403


def test_a_control_key_stops_when_its_owner_is_no_longer_an_admin(secured):
    first = admin()
    switch(first)
    second_id = first.post("/api/users", json={"username": "second", "password": "second password",
                                               "role": "admin"}).json()["id"]
    second = local()
    assert second.post("/api/auth/login", json={"username": "second", "password": "second password"}).status_code == 200
    headers = bearer(control_key(second, path="/api/my-keys")["key"])
    robot = local()
    assert robot.get("/api/settings", headers=headers).status_code == 200
    assert first.patch(f"/api/users/{second_id}", json={"role": "user"}).status_code == 200
    refused = robot.get("/api/settings", headers=headers)
    assert refused.status_code == 403 and "admin" in refused.json()["detail"]


def test_listings_show_scope_and_expiry_but_never_the_key(secured):
    c = admin()
    switch(c)
    made = control_key(c, name="listed")
    listing = c.get("/api/keys")
    assert made["key"] not in listing.text and "key_hash" not in listing.text
    row = next(k for k in listing.json() if k["name"] == "listed")
    assert row["scope"] == "control" and row["expires_at"] == made["expires_at"]


def test_revoking_a_control_key_ends_it(secured):
    c = admin()
    switch(c)
    robot = local()
    made = control_key(c)
    headers = bearer(made["key"])
    assert robot.get("/api/settings", headers=headers).status_code == 200
    assert c.delete(f"/api/keys/{made['id']}").status_code == 204
    assert robot.get("/api/settings", headers=headers).status_code == 401


def test_every_use_of_a_control_key_is_logged_without_the_key(secured, caplog):
    c = admin()
    switch(c)
    robot = local()
    made = control_key(c, name="audit-me")
    with caplog.at_level(logging.INFO, logger="horus_media"):
        robot.get("/api/settings", headers=bearer(made["key"]))
        robot.get("/api/users", headers=bearer(made["key"]))
    text = caplog.text
    assert "control key audit-me (owner): GET /api/settings -> 200" in text
    assert "audit-me refused on GET /api/users" in text
    assert made["key"] not in text


def test_control_keys_need_a_signed_in_admin_account(secured, monkeypatch):
    monkeypatch.setenv("HORUS_AUTH_DISABLED", "1")  # login turned off: keys belong to nobody
    c = local()
    r = c.post("/api/keys", json={"name": "t", "scope": "control", "expires_in_days": 7})
    assert r.status_code == 409 and "signed-in admin" in r.json()["detail"]
