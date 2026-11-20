"""Telegram, Gotify and Pushover channels: each takes two values instead of one address."""

import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import notifications
from horus_media.db import connect
from horus_media.main import create_app

BOT = "123456789:AAExampleExampleExampleExampleExamp"
APP = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5"  # 30 characters
USER = "u1v2w3x4y5z6a7b8c9d0e1f2g3h4i5"
GOTIFY = "AbCdEf.123456-xyz"
FIELDS = {
    "telegram": {"token": BOT, "chat": "-1009876543210"},
    "gotify": {"server": "https://push.example.com/gotify/", "token": GOTIFY},
    "pushover": {"token": APP, "user": USER},
}
SENT: list[httpx.Request] = []


def handler(request: httpx.Request) -> httpx.Response:
    SENT.append(request)
    return httpx.Response(401 if b"refuse" in request.content else 200)


@pytest.fixture
def api(tmp_path, monkeypatch):
    SENT.clear()
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    return TestClient(create_app(transport=httpx.MockTransport(handler)))


def request_for(kind, title="Horus test", message="Hello"):
    return notifications._request(kind, notifications.compose(kind, FIELDS[kind]), "test",
                                  title, message)


def test_telegram_posts_plain_text_to_the_bot():
    r = request_for("telegram", title="alice started playing", message="A *Film* on Plex")
    assert str(r.url) == f"https://api.telegram.org/bot{BOT}/sendMessage"
    body = json.loads(r.content)
    assert body == {"chat_id": "-1009876543210", "text": "alice started playing\nA *Film* on Plex"}
    assert "parse_mode" not in body  # so nothing in a title is read as markup


def test_telegram_takes_a_channel_name_too():
    url = notifications.compose("telegram", {"token": BOT, "chat": "@my_channel"})
    r = notifications._request("telegram", url, "test", "t", "m")
    assert json.loads(r.content)["chat_id"] == "@my_channel"


def test_gotify_sends_the_token_in_a_header_not_in_the_address():
    r = request_for("gotify")
    assert str(r.url) == "https://push.example.com/gotify/message"
    assert r.headers["X-Gotify-Key"] == GOTIFY
    assert json.loads(r.content) == {"title": "Horus test", "message": "Hello"}


def test_pushover_sends_both_keys_in_the_form_not_in_the_address():
    r = request_for("pushover")
    assert str(r.url) == "https://api.pushover.net/1/messages.json"
    form = {k: v[0] for k, v in parse_qs(r.content.decode()).items()}
    assert form == {"token": APP, "user": USER, "title": "Horus test", "message": "Hello"}


@pytest.mark.parametrize("kind, fields", [
    ("telegram", {"token": "not-a-token", "chat": "987654"}),
    ("telegram", {"token": BOT, "chat": "my chat"}),
    ("telegram", {"token": BOT}),
    ("gotify", {"server": "push.example.com", "token": GOTIFY}),
    ("gotify", {"server": "https://push.example.com", "token": ""}),
    ("pushover", {"token": APP[:-1], "user": USER}),
    ("pushover", {"token": APP, "user": USER + "x"}),
    ("discord", {"token": "x"}),
])
def test_values_that_do_not_look_right_are_refused_without_repeating_them(kind, fields):
    with pytest.raises(notifications.ChannelError) as e:
        notifications.compose(kind, fields)
    for value in fields.values():
        assert not value or value not in str(e.value)


@pytest.mark.parametrize("kind, host", [("telegram", "api.telegram.org"),
                                        ("gotify", "push.example.com"),
                                        ("pushover", "api.pushover.net")])
def test_add_through_the_api_stores_nothing_in_clear_and_shows_only_the_host(
        api, tmp_path, kind, host):
    r = api.post("/api/notifications", json={
        "name": "Phone", "kind": kind, "fields": FIELDS[kind], "events": ["server_down"]})
    assert r.status_code == 201, r.text
    assert r.json()["host"] == host and r.json()["kind"] == kind
    listing = api.get("/api/notifications").text
    raw = connect(tmp_path / "horus.db").execute("SELECT url FROM notification_channels").fetchone()
    for secret in (BOT, APP, USER, GOTIFY, "-1009876543210"):
        assert secret not in r.text and secret not in listing and secret not in raw[0]

    cid = r.json()["id"]
    assert api.post(f"/api/notifications/{cid}/test").json() == {"ok": True, "error": None}
    assert SENT[-1].url.host == host


def test_the_page_is_told_which_boxes_each_kind_needs(api):
    data = api.get("/api/notifications").json()
    assert data["kinds"] == ["webhook", "discord", "ntfy", "telegram", "gotify", "pushover"]
    assert set(data["fields"]) == {"telegram", "gotify", "pushover"}
    assert [b["key"] for b in data["fields"]["telegram"]] == ["token", "chat"]
    for boxes in data["fields"].values():
        for box in boxes:
            assert set(box) == {"key", "label", "secret", "hint"}


def test_missing_or_wrong_boxes_are_refused(api):
    def add(**over):
        return api.post("/api/notifications", json={
            "name": "Phone", "kind": "telegram", "events": ["server_down"], **over})

    assert add().status_code == 422  # no boxes at all
    assert add(fields={"token": BOT}).status_code == 422
    assert add(url="https://example.com/anything").status_code == 422  # not a Telegram address
    bad = add(fields={"token": "wrong", "chat": "1234"})
    assert bad.status_code == 422 and "Bot token" in bad.json()["detail"]
    assert api.get("/api/notifications").json()["channels"] == []


def test_edit_keeps_the_values_unless_new_ones_are_given(api, tmp_path):
    cid = api.post("/api/notifications", json={
        "name": "Phone", "kind": "pushover", "fields": FIELDS["pushover"],
        "events": ["server_down"]}).json()["id"]

    # Name and events only: the stored keys stay.
    r = api.patch(f"/api/notifications/{cid}", json={"name": "Tablet", "events": ["server_up"]})
    assert r.status_code == 200 and r.json()["name"] == "Tablet"
    api.post(f"/api/notifications/{cid}/test")
    assert f"user={USER}" in SENT[-1].content.decode()

    # Empty boxes mean "keep".
    r = api.patch(f"/api/notifications/{cid}", json={"fields": {"token": "", "user": ""}})
    assert r.status_code == 200

    # New values for the same type.
    other = "z" * 30
    r = api.patch(f"/api/notifications/{cid}", json={"fields": {"token": APP, "user": other}})
    assert r.status_code == 200, r.text
    api.post(f"/api/notifications/{cid}/test")
    assert f"user={other}" in SENT[-1].content.decode()

    # Another type needs that type's boxes.
    assert api.patch(f"/api/notifications/{cid}", json={"kind": "telegram"}).status_code == 422
    r = api.patch(f"/api/notifications/{cid}",
                  json={"kind": "telegram", "fields": FIELDS["telegram"]})
    assert r.status_code == 200 and r.json()["host"] == "api.telegram.org"


def test_channels_from_before_the_new_kinds_survive_the_database_change(tmp_path):
    """Migration 14 rebuilds the table: what was in it, ids included, must still be there."""
    import sqlite3

    from horus_media.crypto import TokenCipher
    from horus_media.db import migrate
    from horus_media.migrations import MIGRATIONS

    path = tmp_path / "horus.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for version, sql in enumerate(MIGRATIONS[:13], start=1):
        old.executescript(sql)
        old.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
    cipher = TokenCipher(tmp_path / "horus.key")
    for cid, kind in ((3, "discord"), (7, "ntfy")):
        old.execute(
            "INSERT INTO notification_channels (id, name, kind, url, events, enabled, created_at) "
            "VALUES (?, ?, ?, ?, '[\"server_down\"]', ?, '2026-01-01T00:00:00+00:00')",
            (cid, f"Old {kind}", kind, cipher.encrypt(f"https://old.example.com/{kind}"), cid == 3))
    with pytest.raises(sqlite3.IntegrityError):  # what stopped the new kinds before
        old.execute("INSERT INTO notification_channels (name, kind, url, events, created_at) "
                    "VALUES ('x', 'telegram', 'x', '[]', 'x')")
    old.commit()
    old.close()

    conn = connect(path)
    assert migrate(conn) == len(MIGRATIONS) >= 14
    kept = notifications.list_channels(conn, cipher)
    assert [(c["id"], c["kind"], c["host"], c["enabled"]) for c in kept] == [
        (3, "discord", "old.example.com", True), (7, "ntfy", "old.example.com", False)]
    added = notifications.add_channel(conn, cipher, "New", "telegram",
                                      notifications.compose("telegram", FIELDS["telegram"]),
                                      ["server_down"])
    assert added["id"] == 8  # numbering carries on after the old ones


def test_a_refusal_is_reported_without_the_address(api):
    cid = api.post("/api/notifications", json={
        "name": "Phone", "kind": "telegram", "fields": FIELDS["telegram"],
        "events": ["server_down"]}).json()["id"]
    problem = notifications.send(
        "telegram", notifications.compose("telegram", FIELDS["telegram"]), "test", "refuse", "m",
        httpx.MockTransport(handler))
    assert problem == "The channel answered HTTP 401" and BOT not in problem
    assert cid
