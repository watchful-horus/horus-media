"""The change-password form counts wrong attempts, and a read key may be given an end date."""
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from horus_media import auth
from horus_media.main import create_app

OWNER, PASSWORD = "owner", "correct horse battery"
CHANGE = "/api/auth/password"


@pytest.fixture
def secured(tmp_path, monkeypatch):
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    return tmp_path


def browser(app):
    return TestClient(app, client=("127.0.0.1", 50000))


def admin_session(app):
    client = browser(app)
    assert client.post("/api/auth/setup", json={"username": OWNER, "password": PASSWORD}).status_code == 200
    return client


def test_the_change_password_form_stops_after_five_wrong_tries(secured):
    app = create_app()
    admin = admin_session(app)
    for _ in range(5):
        assert admin.post(CHANGE, json={"current": "a wrong guess", "new": "a new password!"}).status_code == 403
    # Now even the right current password has to wait, so the guessing gains nothing.
    blocked = admin.post(CHANGE, json={"current": PASSWORD, "new": "a new password!"})
    assert blocked.status_code == 429 and "wait" in blocked.json()["detail"]
    # The password did not change and the session still works.
    assert admin.get("/api/servers").status_code == 200
    assert browser(app).post("/api/auth/login",
                             json={"username": OWNER, "password": PASSWORD}).status_code == 200


def test_wrong_tries_are_counted_per_account_not_per_address(secured):
    app = create_app()
    admin = admin_session(app)
    assert admin.post("/api/users", json={"username": "viewer", "password": "viewer password",
                                          "role": "user"}).status_code == 201
    viewer = browser(app)  # the same address as the admin
    assert viewer.post("/api/auth/login",
                       json={"username": "viewer", "password": "viewer password"}).status_code == 200
    for _ in range(5):
        assert viewer.post(CHANGE, json={"current": "nope", "new": "viewer new password"}).status_code == 403
    assert viewer.post(CHANGE, json={"current": "viewer password",
                                     "new": "viewer new password"}).status_code == 429
    assert admin.post(CHANGE, json={"current": PASSWORD, "new": "a new password!"}).status_code == 200


def test_a_right_password_clears_the_count(secured):
    app = create_app()
    admin = admin_session(app)
    for _ in range(4):
        assert admin.post(CHANGE, json={"current": "wrong", "new": "a new password!"}).status_code == 403
    assert admin.post(CHANGE, json={"current": PASSWORD, "new": "a new password!"}).status_code == 200
    for _ in range(4):
        assert admin.post(CHANGE, json={"current": "wrong", "new": "another password"}).status_code == 403
    assert admin.post(CHANGE, json={"current": "a new password!",
                                    "new": "another password"}).status_code == 200


def test_the_wrong_password_is_not_written_to_the_log(secured, caplog):
    admin = admin_session(create_app())
    with caplog.at_level("WARNING"):
        admin.post(CHANGE, json={"current": "my-mistyped-secret", "new": "a new password!"})
    assert "change-password form" in caplog.text and "my-mistyped-secret" not in caplog.text


def test_a_read_key_has_no_end_date_unless_one_is_chosen(secured):
    admin = admin_session(create_app())
    plain = admin.post("/api/keys", json={"name": "Grafana"}).json()
    assert plain["expires_at"] is None
    dated = admin.post("/api/keys", json={"name": "Trial", "expires_in_days": 30}).json()
    ends = datetime.fromisoformat(dated["expires_at"])
    assert timedelta(days=29, hours=23) < ends - datetime.now(UTC) <= timedelta(days=30)
    listed = {k["name"]: k["expires_at"] for k in admin.get("/api/keys").json()}
    assert listed == {"Grafana": None, "Trial": dated["expires_at"]}


def test_a_read_key_takes_only_the_offered_lengths(secured):
    admin = admin_session(create_app())
    for days in auth.READ_DAYS:
        assert admin.post("/api/keys", json={"name": f"k{days}", "expires_in_days": days}).status_code == 201
    for days in (0, 1, 7, 31, 10000, -5):
        refused = admin.post("/api/keys", json={"name": "bad", "expires_in_days": days})
        assert refused.status_code == 422, days
    assert len(admin.get("/api/keys").json()) == len(auth.READ_DAYS)


def test_a_read_key_stops_working_at_its_end_date(secured, monkeypatch):
    app = create_app()
    admin = admin_session(app)
    key = admin.post("/api/keys", json={"name": "Trial", "expires_in_days": 30}).json()["key"]
    robot = browser(app)
    assert robot.get("/api/v1/servers", headers={"X-API-Key": key}).status_code == 200
    real_now = auth._now
    monkeypatch.setattr(auth, "_now", lambda: real_now() + timedelta(days=29, hours=23))
    assert robot.get("/api/v1/servers", headers={"X-API-Key": key}).status_code == 200
    monkeypatch.setattr(auth, "_now", lambda: real_now() + timedelta(days=30, minutes=1))
    assert robot.get("/api/v1/servers", headers={"X-API-Key": key}).status_code == 401


def test_a_normal_user_can_give_their_own_key_an_end_date(secured):
    app = create_app()
    admin = admin_session(app)
    assert admin.post("/api/users", json={"username": "viewer", "password": "viewer password",
                                          "role": "user"}).status_code == 201
    viewer = browser(app)
    viewer.post("/api/auth/login", json={"username": "viewer", "password": "viewer password"})
    made = viewer.post("/api/my-keys", json={"name": "Mine", "expires_in_days": 90})
    assert made.status_code == 201 and made.json()["expires_at"]
    assert viewer.get("/api/my-keys").json()[0]["expires_at"] == made.json()["expires_at"]
    assert viewer.post("/api/my-keys", json={"name": "Odd", "expires_in_days": 5}).status_code == 422
