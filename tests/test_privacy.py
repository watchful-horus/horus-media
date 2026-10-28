"""Hiding other people's activity from normal users, and API keys that belong to a person."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media.main import create_app

from .conftest import TOKEN, fake_servers
from .test_auth import PASSWORD, add_user, set_up

NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Admin client + a normal user 'viewer' who is Plex user alice, with two people's plays."""
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    app = create_app(transport=httpx.MockTransport(fake_servers))
    admin = TestClient(app, client=("127.0.0.1", 50000))
    assert set_up(admin).status_code == 200
    sid = admin.post("/api/servers", json={"type": "plex", "name": "Plex", "url": "http://p",
                                           "token": TOKEN}).json()["id"]
    viewer = add_user(admin, "viewer", "viewer password")
    from horus_media.config import db_path
    from horus_media.db import connect
    conn = connect(db_path())
    with conn:
        for who, title in (("alice", "Alice Film"), ("bob", "Bob Film")):
            t = NOW - timedelta(hours=2)
            conn.execute(
                "INSERT INTO history (server_id, session_key, user, title, media_type, device, "
                "platform, play_method, started_at, last_seen_at, ended_at) "
                "VALUES (?, 'k', ?, ?, 'movie', 'd', 'p', 'direct_play', ?, ?, ?)",
                (sid, who, title, t.isoformat(), t.isoformat(), t.isoformat()))
    conn.close()
    user = TestClient(app, client=("10.0.0.5", 1))
    assert user.post("/api/auth/login", json={"username": "viewer",
                                              "password": "viewer password"}).status_code == 200
    return admin, user, app, viewer


def link(admin, viewer, name="alice"):
    r = admin.patch(f"/api/users/{viewer['id']}", json={"media_username": name})
    assert r.status_code == 200 and r.json()["media_username"] == name


def hide(admin, on=True):
    assert admin.put("/api/settings", json={"hide_others_activity": on}).status_code == 200


def names(user):
    return sorted(u["name"] for u in user.get("/api/stats").json()["by_user"])


def test_everyone_sees_everything_by_default(world):
    _, user, _, _ = world
    assert names(user) == ["alice", "bob"]
    assert {s["user"] for s in user.get("/api/activity").json()["sessions"]} == {"alice", "bob"}


def test_hidden_activity_shows_only_the_linked_name(world):
    admin, user, _, viewer = world
    link(admin, viewer)
    hide(admin)
    assert names(user) == ["alice"]
    assert {s["user"] for s in user.get("/api/activity").json()["sessions"]} == {"alice"}
    stats = user.get("/api/stats").json()
    assert stats["total_plays"] == 1 and all(not b["points"] for b in stats["bandwidth"])


def test_linked_name_ignores_case(world):
    admin, user, _, viewer = world
    link(admin, viewer, "ALICE")
    hide(admin)
    assert names(user) == ["alice"]


def test_unlinked_user_sees_nothing_when_hidden(world):
    admin, user, _, _ = world
    hide(admin)
    assert names(user) == []
    assert user.get("/api/activity").json()["sessions"] == []


def test_admins_are_never_restricted(world):
    admin, _, _, _ = world
    hide(admin)
    assert names(admin) == ["alice", "bob"]


def test_turning_it_off_restores_everything(world):
    admin, user, _, viewer = world
    link(admin, viewer)
    hide(admin)
    hide(admin, False)
    assert names(user) == ["alice", "bob"]


def test_a_persons_key_is_limited_like_the_person(world):
    admin, user, app, viewer = world
    link(admin, viewer)
    hide(admin)
    key = user.post("/api/my-keys", json={"name": "Grafana"}).json()["key"]
    anon = TestClient(app, client=("10.0.0.9", 1))
    h = {"X-API-Key": key}
    hist = anon.get("/api/v1/history?from=2020-01-01&to=2026-12-31", headers=h).json()
    assert [p["user"] for p in hist] == ["alice"]
    asking = anon.get("/api/v1/history?from=2020-01-01&to=2026-12-31&user=bob", headers=h).json()
    assert [p["user"] for p in asking] == ["alice"]  # asking for someone else doesn't widen it
    plays = anon.get("/api/v1/plays?from=2020-01-01&to=2026-12-31", headers=h).json()
    assert sum(p["plays"] for p in plays) == 1
    assert anon.get("/api/v1/bandwidth", headers=h).json() == []
    assert {r["user"] for r in anon.get("/api/v1/activity", headers=h).json()} == {"alice"}


def test_a_persons_key_is_unlimited_when_the_setting_is_off(world):
    _, user, app, _ = world
    key = user.post("/api/my-keys", json={"name": "k"}).json()["key"]
    anon = TestClient(app, client=("10.0.0.9", 1))
    hist = anon.get("/api/v1/history?from=2020-01-01&to=2026-12-31",
                    headers={"X-API-Key": key}).json()
    assert len(hist) == 2


def test_people_manage_only_their_own_keys(world):
    admin, user, _, _ = world
    mine = user.post("/api/my-keys", json={"name": "mine"}).json()
    theirs = admin.post("/api/keys", json={"name": "admins"}).json()
    assert [k["name"] for k in user.get("/api/my-keys").json()] == ["mine"]
    assert user.delete(f"/api/my-keys/{theirs['id']}").status_code == 404
    assert user.get("/api/keys").status_code == 403  # the full list stays admin-only
    owners = {k["name"]: k["owner"] for k in admin.get("/api/keys").json()}
    assert owners == {"mine": "viewer", "admins": "owner"}
    assert user.delete(f"/api/my-keys/{mine['id']}").status_code == 204


def test_deleting_a_person_removes_their_keys(world):
    admin, user, app, viewer = world
    key = user.post("/api/my-keys", json={"name": "k"}).json()["key"]
    assert admin.delete(f"/api/users/{viewer['id']}").status_code == 204
    anon = TestClient(app, client=("10.0.0.9", 1))
    assert anon.get("/api/v1/servers", headers={"X-API-Key": key}).status_code == 401


def test_new_user_can_be_linked_when_created(world):
    admin, _, _, _ = world
    r = admin.post("/api/users", json={"username": "carol1", "password": PASSWORD,
                                       "role": "user", "media_username": "carol"})
    assert r.json()["media_username"] == "carol"
