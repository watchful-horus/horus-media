"""Notification channels: Horus sends a message when something happens on your servers.

Three kinds of channel, all plain HTTPS posts to an address you provide:
  webhook  JSON {event, title, message, time} to any URL
  discord  a Discord channel webhook
  ntfy     a topic on ntfy.sh (or your own ntfy server): the full topic URL

A channel's address is a secret (anyone holding it can post to your channel), so it is encrypted
at rest, never logged and never returned by the API; only its host is shown.
"""

import json
import logging
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from .crypto import TokenCipher

log = logging.getLogger(__name__)

KINDS = ("webhook", "discord", "ntfy")
EVENTS = {
    "play_started": "Someone starts playing",
    "play_ended": "A play ends",
    "server_down": "A server becomes unreachable",
    "server_up": "A server comes back",
}
TIMEOUT = 5.0
_URL = re.compile(r"https?://[^\s]+")
# Sending happens off the poller's thread so a slow channel never delays polling.
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notify")


class ChannelError(ValueError):
    """A rejected channel. The message is safe to show."""


def validate(kind: str, url: str, events: list[str]) -> list[str]:
    if kind not in KINDS:
        raise ChannelError("Unknown channel type")
    if not _URL.fullmatch(url.strip()):
        raise ChannelError("The address must start with http:// or https://")
    unknown = [e for e in events if e not in EVENTS]
    if unknown or not events:
        raise ChannelError("Pick at least one event to send")
    return sorted(set(events), key=list(EVENTS).index)


def _public(row: sqlite3.Row, cipher: TokenCipher) -> dict:
    host = urlsplit(cipher.decrypt(row["url"])).hostname or ""
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "host": host,
            "events": json.loads(row["events"]), "enabled": bool(row["enabled"])}


def list_channels(conn: sqlite3.Connection, cipher: TokenCipher) -> list[dict]:
    return [_public(r, cipher) for r in conn.execute(
        "SELECT * FROM notification_channels ORDER BY id")]


def add_channel(conn: sqlite3.Connection, cipher: TokenCipher, name: str, kind: str, url: str,
                events: list[str]) -> dict:
    name = name.strip()
    if not name or len(name) > 60:
        raise ChannelError("Give the channel a short name")
    events = validate(kind, url, events)
    with conn:
        cur = conn.execute(
            "INSERT INTO notification_channels (name, kind, url, events, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, kind, cipher.encrypt(url.strip()), json.dumps(events),
             datetime.now(UTC).isoformat()))
    return _public(conn.execute("SELECT * FROM notification_channels WHERE id = ?",
                                (cur.lastrowid,)).fetchone(), cipher)


def update_channel(conn: sqlite3.Connection, cipher: TokenCipher, channel_id: int,
                   enabled: bool | None, events: list[str] | None) -> dict | None:
    row = conn.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,)).fetchone()
    if row is None:
        return None
    if events is not None:
        events = validate(row["kind"], cipher.decrypt(row["url"]), events)
    with conn:
        if enabled is not None:
            conn.execute("UPDATE notification_channels SET enabled = ? WHERE id = ?",
                         (int(enabled), channel_id))
        if events is not None:
            conn.execute("UPDATE notification_channels SET events = ? WHERE id = ?",
                         (json.dumps(events), channel_id))
    return _public(conn.execute("SELECT * FROM notification_channels WHERE id = ?",
                                (channel_id,)).fetchone(), cipher)


def delete_channel(conn: sqlite3.Connection, channel_id: int) -> bool:
    with conn:
        return conn.execute("DELETE FROM notification_channels WHERE id = ?",
                            (channel_id,)).rowcount > 0


def _request(kind: str, url: str, event: str, title: str, message: str) -> httpx.Request:
    if kind == "discord":
        return httpx.Request("POST", url, json={"content": f"**{title}**\n{message}"})
    if kind == "ntfy":
        return httpx.Request("POST", url, content=message.encode(),
                             headers={"Title": title.encode("ascii", "replace").decode(),
                                      "Tags": event})
    return httpx.Request("POST", url, json={
        "event": event, "title": title, "message": message,
        "time": datetime.now(UTC).isoformat()})


def send(kind: str, url: str, event: str, title: str, message: str,
         transport: httpx.BaseTransport | None = None) -> str | None:
    """Post one message. Returns None on success or a short reason (never containing the address)."""
    try:
        with httpx.Client(timeout=TIMEOUT, transport=transport) as http:
            resp = http.send(_request(kind, url, event, title, message))
        if resp.status_code >= 400:
            return f"The channel answered HTTP {resp.status_code}"
        return None
    except httpx.HTTPError as e:
        return f"Could not reach the channel ({type(e).__name__})"


def notify(conn: sqlite3.Connection, cipher: TokenCipher, event: str, title: str, message: str,
           transport: httpx.BaseTransport | None = None, wait: bool = False) -> None:
    """Send to every enabled channel that wants this event. Failures are logged, never raised."""
    rows = conn.execute("SELECT * FROM notification_channels WHERE enabled = 1").fetchall()
    for row in rows:
        if event not in json.loads(row["events"]):
            continue
        url = cipher.decrypt(row["url"])

        def job(row=row, url=url) -> None:
            problem = send(row["kind"], url, event, title, message, transport)
            if problem:
                log.warning("notification to %s failed: %s", row["name"], problem)

        if wait:
            job()
        else:
            _pool.submit(job)
