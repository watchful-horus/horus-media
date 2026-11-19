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
from cryptography.fernet import InvalidToken

from .crypto import TokenCipher

log = logging.getLogger(__name__)

KINDS = ("webhook", "discord", "ntfy")
EVENTS = {
    "play_started": "Someone starts playing",
    "play_ended": "A play ends",
    "server_down": "A server becomes unreachable",
    "server_up": "A server comes back",
    "transcodes_high": "Too many transcodes at once (set the limit under Alerts and reports)",
    "bandwidth_high": "A server's bandwidth is over the limit (set it under Alerts and reports)",
    "playback_problem": "A stream has a playback problem (buffering, stalled, slow transcode)",
    "new_user": "Someone new plays something (turn on under Alerts and reports)",
    "report": "The weekly or monthly report (turn on under Alerts and reports)",
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


UNREADABLE = ("This channel's address can no longer be read. Enter it again with Edit, or remove "
              "the channel and add it again.")
_warned: set[int] = set()  # unreadable channels already mentioned in the log


def address(row: sqlite3.Row, cipher: TokenCipher) -> str | None:
    """The channel's address, or None when it cannot be decrypted: the encryption key was lost
    or replaced since the channel was added (for example by restoring a database without its
    key). One such channel must never stop the others, or the poll that wanted to notify."""
    try:
        return cipher.decrypt(row["url"])
    except InvalidToken:
        if row["id"] not in _warned:
            _warned.add(row["id"])
            log.warning("notification channel %s: its address cannot be read with the current "
                        "encryption key; enter the address again (Settings > Notifications, "
                        "Edit)", row["name"])
        return None


def _public(row: sqlite3.Row, cipher: TokenCipher) -> dict:
    url = address(row, cipher)
    return {"id": row["id"], "name": row["name"], "kind": row["kind"],
            "host": (urlsplit(url).hostname or "") if url else "", "unreadable": url is None,
            "events": json.loads(row["events"]), "enabled": bool(row["enabled"])}


def list_channels(conn: sqlite3.Connection, cipher: TokenCipher) -> list[dict]:
    return [_public(r, cipher) for r in conn.execute(
        "SELECT * FROM notification_channels ORDER BY id")]


def _name(name: str) -> str:
    name = name.strip()
    if not name or len(name) > 60:
        raise ChannelError("Give the channel a short name")
    return name


def add_channel(conn: sqlite3.Connection, cipher: TokenCipher, name: str, kind: str, url: str,
                events: list[str]) -> dict:
    name = _name(name)
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
                   enabled: bool | None = None, events: list[str] | None = None,
                   name: str | None = None, kind: str | None = None,
                   url: str | None = None) -> dict | None:
    """Change what was given and keep the rest. An empty address means "keep the current one":
    the stored address is never sent back to the page, so it cannot be re-submitted. The type
    only changes together with a new address, because the old one would not fit it."""
    row = conn.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,)).fetchone()
    if row is None:
        return None
    url = (url or "").strip() or None
    changes: dict[str, object] = {}
    if name is not None:
        changes["name"] = _name(name)
    if kind is not None and kind != row["kind"]:
        if kind not in KINDS:
            raise ChannelError("Unknown channel type")
        if url is None:
            raise ChannelError("Enter the new address too when you change the type")
        changes["kind"] = kind
    if url is not None or events is not None or "kind" in changes:
        # Without a new address only the events are being checked; an unreadable stored address
        # must not block pausing or changing them.
        checked = validate(kind or row["kind"],
                           url or address(row, cipher) or "https://unreadable.invalid",
                           json.loads(row["events"]) if events is None else events)
        if events is not None:
            changes["events"] = json.dumps(checked)
    if url is not None:
        changes["url"] = cipher.encrypt(url)
    if enabled is not None:
        changes["enabled"] = int(enabled)
    if changes:
        with conn:
            conn.execute(
                f"UPDATE notification_channels SET {', '.join(f'{c} = ?' for c in changes)} "
                "WHERE id = ?", (*changes.values(), channel_id))
    if url is not None:
        _warned.discard(channel_id)  # readable again; warn afresh if the key is lost once more
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
        url = address(row, cipher)
        if url is None:
            continue

        def job(row=row, url=url) -> None:
            problem = send(row["kind"], url, event, title, message, transport)
            if problem:
                log.warning("notification to %s failed: %s", row["name"], problem)

        if wait:
            job()
        else:
            _pool.submit(job)
