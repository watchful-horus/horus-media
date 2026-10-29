"""People can change their own username and display name; only admins set a Plex/Emby name."""

import pytest
from fastapi.testclient import TestClient

from horus_media.main import create_app

PASSWORD = "correct horse battery"


@pytest.fixture
def secured(tmp_path, monkeypatch):
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))


def client() -> TestClient:
    return TestClient(create_app(), client=("127.0.0.1", 50000))


def admin() -> TestClient:
    c = client()
    assert c.post("/api/auth/setup", json={"username": "owner", "password": PASSWORD}).status_code == 200
    return c


def signed_in(name: str) -> TestClient:
    c = client()
    assert c.post("/api/auth/login", json={"username": name, "password": PASSWORD}).status_code == 200
    return c


def add_user(boss: TestClient, name: str, **extra) -> dict:
    r = boss.post("/api/users", json={"username": name, "password": PASSWORD, "role": "user", **extra})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_normal_user_can_rename_themselves_and_stays_signed_in(secured):
    boss = admin()
    add_user(boss, "carol")
    carol = signed_in("carol")
    r = carol.patch("/api/auth/me", json={"username": "carol.b", "display_name": "  Carol   B  "})
    assert r.status_code == 200
    assert r.json()["username"] == "carol.b" and r.json()["display_name"] == "Carol B"
    assert carol.get("/api/auth/status").json()["user"]["username"] == "carol.b"  # same session
    assert client().post("/api/auth/login", json={"username": "carol", "password": PASSWORD}).status_code == 401
    signed_in("carol.b")


def test_a_normal_user_cannot_set_their_plex_or_emby_name(secured):
    boss = admin()
    add_user(boss, "carol")
    carol = signed_in("carol")
    r = carol.patch("/api/auth/me", json={"media_username": "someone-else"})
    assert r.status_code == 403
    assert next(u for u in boss.get("/api/users").json() if u["username"] == "carol")["media_username"] == ""


def test_an_admin_can_set_their_own_plex_or_emby_name(secured):
    boss = admin()
    r = boss.patch("/api/auth/me", json={"media_username": "owner-on-plex", "display_name": "The Owner"})
    assert r.status_code == 200
    assert r.json()["media_username"] == "owner-on-plex" and r.json()["display_name"] == "The Owner"


def test_usernames_must_be_valid_and_unique(secured):
    boss = admin()
    add_user(boss, "carol")
    carol = signed_in("carol")
    assert carol.patch("/api/auth/me", json={"username": "OWNER"}).status_code == 409  # not case-sensitive
    assert carol.patch("/api/auth/me", json={"username": "x"}).status_code == 422
    assert carol.patch("/api/auth/me", json={"display_name": "n" * 61}).status_code == 422
    assert carol.get("/api/auth/status").json()["user"]["username"] == "carol"


def test_an_admin_can_edit_another_persons_details(secured):
    boss = admin()
    uid = add_user(boss, "carol")["id"]
    r = boss.patch(f"/api/users/{uid}", json={"username": "carol.new", "display_name": "Carol", "media_username": "carol-plex"})
    assert r.status_code == 200
    assert (r.json()["username"], r.json()["display_name"], r.json()["media_username"]) == ("carol.new", "Carol", "carol-plex")
    assert boss.patch(f"/api/users/{uid}", json={"username": "owner"}).status_code == 409


def test_a_normal_user_cannot_edit_others_and_a_control_key_cannot_rename(secured):
    boss = admin()
    uid = add_user(boss, "carol")["id"]
    carol = signed_in("carol")
    assert carol.patch(f"/api/users/{uid}", json={"username": "x-x-x"}).status_code == 403
    assert boss.put("/api/settings", json={"control_keys_enabled": True}).status_code == 200
    key = boss.post("/api/keys", json={"name": "t", "scope": "control", "expires_in_days": 7}).json()["key"]
    r = client().patch("/api/auth/me", json={"display_name": "hacked"}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403
