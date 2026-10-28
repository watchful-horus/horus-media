import sqlite3

import pytest
from fastapi.testclient import TestClient

from horus_media import auth
from horus_media.db import migrate
from horus_media.logging_setup import scrub
from horus_media.main import create_app
from horus_media.migrations import MIGRATIONS

OWNER, PASSWORD = "owner", "correct horse battery"


@pytest.fixture
def secured(tmp_path, monkeypatch):
    """The app with the login switched ON, as a user would run it."""
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    return tmp_path


def remote():  # a browser on another computer
    return TestClient(create_app())


def local(app=None):  # a browser on the same computer
    return TestClient(app or create_app(), client=("127.0.0.1", 50000))


def set_up(client, username=OWNER, password=PASSWORD, code=None):
    body = {"username": username, "password": password, **({"code": code} if code else {})}
    return client.post("/api/auth/setup", json=body)


def sign_in(client, username=OWNER, password=PASSWORD):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def admin_session():
    c = local()
    assert set_up(c).status_code == 200
    return c


def add_user(admin, username="viewer", password="viewer password", role="user"):
    r = admin.post("/api/users", json={"username": username, "password": password, "role": role})
    assert r.status_code == 201, r.text
    return r.json()


# --- first run ------------------------------------------------------------------------------

def test_nothing_is_reachable_before_setup(secured):
    c = remote()
    status = c.get("/api/auth/status").json()
    assert status["setup_required"] and status["needs_code"] and not status["authenticated"]
    assert status["user"] is None
    assert c.get("/api/servers").status_code == 401
    assert c.get("/api/activity").status_code == 401
    assert c.get("/api/health").status_code == 200  # the login screen itself must load


def test_setup_from_another_computer_needs_the_code(secured):
    c = remote()
    assert set_up(c).status_code == 403
    assert set_up(c, code="WRONG123").status_code == 403
    assert set_up(c, code=auth.setup_code().lower()).status_code == 200  # case does not matter
    assert c.get("/api/servers").status_code == 200  # logged in by the setup itself


def test_first_person_picks_their_username_and_becomes_admin(secured):
    c = local()
    assert c.get("/api/auth/status").json()["needs_code"] is False
    r = set_up(c, username="Dana_K")
    assert r.status_code == 200 and r.json()["user"]["username"] == "Dana_K"
    status = c.get("/api/auth/status").json()
    assert status["user"]["username"] == "Dana_K" and status["user"]["role"] == "admin"
    assert status["setup_required"] is False
    assert c.get("/api/users").json()[0]["last_login_at"] is not None  # setup counts as signing in


def test_setup_rules(secured):
    c = local()
    for bad in ("ab", "x" * 33, "has space", "semi;colon", "-leading", "", "ünïcode"):
        assert set_up(c, username=bad).status_code == 422, bad
    assert set_up(c, password="short").status_code == 422
    assert set_up(c).status_code == 200
    assert set_up(local(), username="someone").status_code == 409  # only once


# --- login ----------------------------------------------------------------------------------

def test_login_logout_cycle(secured):
    admin_session()
    c = local()
    assert c.get("/api/servers").status_code == 401
    assert sign_in(c, password="wrong password").status_code == 401
    ok = sign_in(c)
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert c.get("/api/servers").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/servers").status_code == 401


def test_usernames_ignore_case_and_errors_do_not_reveal_which_part_was_wrong(secured):
    admin_session()
    assert sign_in(local(), username=OWNER.upper()).status_code == 200
    wrong_user = sign_in(local(), username="nobody")
    wrong_pass = sign_in(local(), password="not the password")
    assert wrong_user.status_code == wrong_pass.status_code == 401
    assert wrong_user.json() == wrong_pass.json()  # same message either way


def test_brute_force_is_limited(secured):
    admin_session()
    c = local()
    for _ in range(5):
        assert sign_in(c, password="nope nope nope").status_code == 401
    # Even the right password is refused for a while: guessing gets nowhere.
    assert sign_in(c).status_code == 429


def test_passwords_and_sessions_are_not_stored_in_the_clear(secured):
    c = admin_session()
    token = c.cookies.get(auth.SESSION_COOKIE)
    db = sqlite3.connect(secured / "horus.db")
    stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
    assert stored.startswith("scrypt$") and PASSWORD not in stored
    hashes = [r[0] for r in db.execute("SELECT token_hash FROM sessions")]
    assert token and token not in hashes and len(hashes) == 1


def test_changing_your_own_password_signs_out_your_other_sessions(secured):
    first = admin_session()
    second = local()
    sign_in(second)
    assert second.get("/api/servers").status_code == 200
    change = "/api/auth/password"
    assert first.post(change, json={"current": "wrong", "new": "a new password!"}).status_code == 403
    assert first.post(change, json={"current": PASSWORD, "new": "short"}).status_code == 422
    assert first.post(change, json={"current": PASSWORD, "new": "a new password!"}).status_code == 200
    assert first.get("/api/servers").status_code == 200  # the session that changed it stays
    assert second.get("/api/servers").status_code == 401
    assert sign_in(local()).status_code == 401
    assert sign_in(local(), password="a new password!").status_code == 200


def test_login_can_be_disabled_for_reverse_proxy_setups(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_AUTH_DISABLED", "1")
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    c = TestClient(create_app())
    assert c.get("/api/servers").status_code == 200
    status = c.get("/api/auth/status").json()
    assert status["auth_disabled"] and status["authenticated"] and not status["setup_required"]
    assert c.get("/api/users").status_code == 409  # accounts mean nothing without a login


# --- users and roles ------------------------------------------------------------------------

def test_admin_creates_and_lists_users(secured):
    admin = admin_session()
    made = add_user(admin)
    assert made["username"] == "viewer" and made["role"] == "user"
    listing = admin.get("/api/users")
    assert [u["username"] for u in listing.json()] == ["owner", "viewer"]
    assert "password" not in listing.text and "scrypt" not in listing.text
    assert admin.post("/api/users", json={"username": "VIEWER", "password": "another password"}
                      ).status_code == 409  # usernames are unique ignoring case
    assert admin.post("/api/users", json={"username": "ab", "password": "another password"}
                      ).status_code == 422
    assert admin.post("/api/users", json={"username": "newbie", "password": "short"}).status_code == 422
    assert admin.post("/api/users", json={"username": "newbie", "password": "long enough pw",
                                          "role": "owner"}).status_code == 422
    viewer = local()
    assert sign_in(viewer, "viewer", "viewer password").status_code == 200
    assert viewer.get("/api/auth/status").json()["user"]["role"] == "user"
    assert admin.get("/api/users").json()[1]["last_login_at"] is not None


def test_a_normal_user_can_only_watch(secured):
    admin = admin_session()
    add_user(admin)
    viewer = local()
    sign_in(viewer, "viewer", "viewer password")

    # What they can use: Activity, Graphs, posters, and changing their own password.
    assert viewer.get("/api/activity").status_code == 200
    assert viewer.get("/api/stats").status_code == 200
    assert viewer.get("/api/servers/1/poster", params={"path": "/x"}).status_code == 404  # allowed in
    assert viewer.post("/api/auth/password", json={"current": "viewer password",
                                                   "new": "viewer new password"}).status_code == 200

    # Everything else needs an admin: reading or changing.
    for path in ("/api/servers", "/api/settings", "/api/keys", "/api/users", "/api/backups",
                 "/api/logs", "/api/logs/download", "/api/update-check", "/api/v1/history"):
        assert viewer.get(path).status_code == 403, path
    for path in ("/api/servers", "/api/keys", "/api/users", "/api/backups", "/api/restart",
                 "/api/plex/link"):
        assert viewer.post(path, json={}).status_code == 403, path
    assert viewer.put("/api/settings", json={}).status_code == 403
    assert viewer.delete("/api/users/1").status_code == 403
    assert viewer.patch("/api/users/1", json={"role": "user"}).status_code == 403
    # ...and the gate also stops them making themselves admin.
    assert viewer.patch("/api/users/2", json={"role": "admin"}).status_code == 403


def test_promoting_changes_what_someone_can_do(secured):
    admin = admin_session()
    user = add_user(admin)
    viewer = local()
    sign_in(viewer, "viewer", "viewer password")
    assert viewer.get("/api/servers").status_code == 403
    assert admin.patch(f"/api/users/{user['id']}", json={"role": "admin"}).json()["role"] == "admin"
    assert viewer.get("/api/servers").status_code == 200  # the existing session picks up the new role
    assert viewer.get("/api/users").status_code == 200


def test_admins_cannot_lock_themselves_or_everyone_out(secured):
    admin = admin_session()
    me = admin.get("/api/auth/status").json()["user"]["id"]
    assert admin.patch(f"/api/users/{me}", json={"role": "user"}).status_code == 409
    assert admin.delete(f"/api/users/{me}").status_code == 409
    assert admin.patch("/api/users/999", json={"role": "user"}).status_code == 404
    assert admin.delete("/api/users/999").status_code == 404
    # The "keep one admin" rule also holds below the API.
    conn = sqlite3.connect(secured / "horus.db")
    conn.row_factory = sqlite3.Row
    with pytest.raises(auth.UserError, match="at least one admin"):
        auth.set_role(conn, me, "user")
    with pytest.raises(auth.UserError, match="at least one admin"):
        auth.delete_user(conn, me)
    second = add_user(admin, "second", "second password", "admin")
    auth.set_role(conn, me, "user")  # fine now: another admin exists
    with pytest.raises(auth.UserError):
        auth.delete_user(conn, second["id"])  # ...but not the last one
    conn.close()


def test_deleting_a_user_signs_them_out_and_frees_the_name(secured):
    admin = admin_session()
    user = add_user(admin)
    viewer = local()
    sign_in(viewer, "viewer", "viewer password")
    assert viewer.get("/api/activity").status_code == 200
    assert admin.delete(f"/api/users/{user['id']}").status_code == 204
    assert viewer.get("/api/activity").status_code == 401
    assert sign_in(local(), "viewer", "viewer password").status_code == 401
    add_user(admin)  # the name can be used again


def test_admin_can_reset_someones_password(secured):
    admin = admin_session()
    user = add_user(admin)
    viewer = local()
    sign_in(viewer, "viewer", "viewer password")
    assert admin.patch(f"/api/users/{user['id']}", json={"password": "short"}).status_code == 422
    assert admin.patch(f"/api/users/{user['id']}", json={"password": "a fresh password"}
                       ).status_code == 200
    assert viewer.get("/api/activity").status_code == 401  # signed out everywhere
    assert sign_in(local(), "viewer", "viewer password").status_code == 401
    assert sign_in(local(), "viewer", "a fresh password").status_code == 200


def test_user_changes_are_logged_without_passwords(secured, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="horus_media")
    admin = admin_session()
    user = add_user(admin, password="super secret pw 1")
    admin.patch(f"/api/users/{user['id']}", json={"role": "admin", "password": "super secret pw 2"})
    admin.delete(f"/api/users/{user['id']}")
    text = "\n".join(r.getMessage() for r in caplog.records)
    for expected in ("user created: viewer (user) by owner", "role changed: viewer is now admin",
                     "password reset: viewer (by owner)", "user deleted: viewer (by owner)"):
        assert expected in text
    assert "super secret" not in text


# --- upgrading from the single-admin version ------------------------------------------------

def test_existing_admin_becomes_the_first_user(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for version, sql in enumerate(MIGRATIONS[:4], start=1):  # the schema before accounts existed
        conn.executescript(sql)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    stored = auth.hash_password("old admin password")
    conn.execute("INSERT INTO admin (id, password_hash, created_at) VALUES (1, ?, '2026-01-01')",
                 (stored,))
    conn.commit()
    migrate(conn)
    users = auth.list_users(conn)
    assert [(u["username"], u["role"]) for u in users] == [("admin", "admin")]
    assert auth.authenticate(conn, "admin", "old admin password")["role"] == "admin"
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'admin'").fetchone() is None
    assert "user_id" in [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]


# --- API keys -------------------------------------------------------------------------------

def make_key(client, name="Grafana"):
    r = client.post("/api/keys", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


def test_api_key_lifecycle(secured):
    admin = admin_session()
    created = make_key(admin)
    key = created["key"]
    assert key.startswith("hk_") and len(key) > 30

    listing = admin.get("/api/keys")
    assert key not in listing.text and "key_hash" not in listing.text  # shown once, never again
    row = listing.json()[0]
    assert row["name"] == "Grafana" and row["prefix"] == key[:7] and row["last_used_at"] is None
    assert sqlite3.connect(secured / "horus.db").execute(
        "SELECT key_hash FROM api_keys").fetchone()[0] != key

    robot = local()  # no login cookie: only the key
    assert robot.get("/api/v1/servers").status_code == 401
    assert robot.get("/api/v1/servers", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert robot.get("/api/v1/servers", headers={"X-API-Key": key}).status_code == 200
    assert robot.get("/api/v1/servers", headers={"Authorization": "Bearer hk_wrong"}).status_code == 401
    assert admin.get("/api/keys").json()[0]["last_used_at"] is not None

    # A key opens the read-only data API and nothing else.
    for path in ("/api/servers", "/api/settings", "/api/keys", "/api/backups", "/api/logs",
                 "/api/users"):
        assert robot.get(path, headers={"X-API-Key": key}).status_code == 401, path
    assert robot.post("/api/backups", headers={"X-API-Key": key}).status_code == 401

    # A normal user's session can't read the data API, but a key still can.
    add_user(admin)
    viewer = local()
    sign_in(viewer, "viewer", "viewer password")
    assert viewer.get("/api/v1/servers").status_code == 403
    assert viewer.get("/api/v1/servers", headers={"X-API-Key": key}).status_code == 200

    assert admin.delete(f"/api/keys/{created['id']}").status_code == 204
    assert robot.get("/api/v1/servers", headers={"X-API-Key": key}).status_code == 401
    assert admin.delete(f"/api/keys/{created['id']}").status_code == 404


def test_keys_need_an_admin_and_a_sensible_name(secured):
    admin = admin_session()
    assert local().post("/api/keys", json={"name": "x"}).status_code == 401
    assert admin.post("/api/keys", json={"name": "  "}).status_code == 422
    assert admin.post("/api/keys", json={"name": "x" * 61}).status_code == 422


def test_keys_are_scrubbed_from_logs():
    assert "hk_" in scrub("key hk_abcdefghijklmnop123 used")
    assert "abcdefghijklmnop123" not in scrub("key hk_abcdefghijklmnop123 used")
