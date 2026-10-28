"""Background poller: turns live sessions into play history and bandwidth samples."""

import logging
import sqlite3
import threading
import time
from datetime import UTC, datetime

import httpx

from . import app_settings, logging_setup, maintenance, notifications
from .activity import fetch_all
from .config import db_path
from .crypto import TokenCipher
from .db import connect, migrate
from .models import ServerActivity
from .store import ServerStore

log = logging.getLogger(__name__)

# Last known reachability per server id, so only changes are logged (not every poll).
_server_up: dict[int, bool] = {}


# Which threshold alerts are currently firing, per (server id, alert), so each one is sent once
# when the limit is crossed and again only after it has dropped back below.
_alert_active: dict[tuple[int, str], bool] = {}


def _edge(sid: int, alert: str, over: bool) -> bool:
    """True only on the poll where `over` turns from False to True."""
    was = _alert_active.get((sid, alert), False)
    _alert_active[(sid, alert)] = over
    return over and not was


def _check_new_users(conn: sqlite3.Connection, result: ServerActivity, cfg, emit) -> None:
    if not cfg.alert_new_user:
        return
    for user in {s.user for s in result.sessions if s.user}:
        if not conn.execute("SELECT 1 FROM history WHERE user = ? LIMIT 1", (user,)).fetchone():
            emit("new_user", f"{user} is new",
                 f"Horus has not seen {user} play anything before. Now playing on {result.name}.")


def _check_limits(result: ServerActivity, cfg, emit) -> None:
    transcodes = sum(1 for s in result.sessions if s.play_method == "transcode")
    limit = cfg.alert_transcodes_over
    if _edge(result.id, "transcodes", bool(limit) and transcodes > limit):
        emit("transcodes_high", f"{result.name}: {transcodes} transcodes",
             f"More than {limit} streams are being transcoded at once.")
    mbps = sum(s.bandwidth_kbps or 0 for s in result.sessions) / 1000
    limit = cfg.alert_bandwidth_over_mbps
    if _edge(result.id, "bandwidth", bool(limit) and mbps > limit):
        emit("bandwidth_high", f"{result.name}: {mbps:.0f} Mbps",
             f"Streams from this server use more than {limit} Mbps.")


def close_stale(conn: sqlite3.Connection) -> None:
    """Close plays left open by a previous run, ending them at the last time they were seen."""
    with conn:
        conn.execute("UPDATE history SET ended_at = last_seen_at WHERE ended_at IS NULL")


def _record(conn: sqlite3.Connection, result: ServerActivity, ts: str, emit=None) -> None:
    emit = emit or (lambda *args: None)
    open_rows = conn.execute(
        "SELECT id, session_key, user, title, bandwidth_kbps FROM history "
        "WHERE server_id = ? AND ended_at IS NULL",
        (result.id,),
    ).fetchall()
    by_key = {(r["session_key"], r["title"]): r for r in open_rows}
    seen = set()
    for s in result.sessions:
        key = (s.session_key, s.title)
        seen.add(key)
        row = by_key.get(key)
        if row is None:
            log.info("play started: %s - %s on %s (%s, %s)", s.user, s.title, result.name,
                     s.device, s.play_method)
            emit("play_started", f"{s.user} started playing",
                 f"{s.title} on {result.name} ({s.device}, {s.play_method.replace('_', ' ')})")
            conn.execute(
                "INSERT INTO history (server_id, session_key, user, title, media_type, device, "
                "platform, play_method, bandwidth_kbps, progress_percent, started_at, "
                "last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result.id, s.session_key, s.user, s.title, s.media_type, s.device, s.platform,
                 s.play_method, s.bandwidth_kbps, s.progress_percent, ts, ts),
            )
        else:
            peak = max(row["bandwidth_kbps"] or 0, s.bandwidth_kbps or 0) or None
            conn.execute(
                "UPDATE history SET last_seen_at = ?, play_method = ?, bandwidth_kbps = ?, "
                "progress_percent = ? WHERE id = ?",
                (ts, s.play_method, peak, s.progress_percent, row["id"]),
            )
    for key, row in by_key.items():
        if key not in seen:
            log.info("play ended: %s - %s on %s", row["user"], row["title"], result.name)
            emit("play_ended", f"{row['user']} finished playing",
                 f"{row['title']} on {result.name}")
            conn.execute(
                "UPDATE history SET ended_at = last_seen_at WHERE id = ?", (row["id"],)
            )
    conn.execute(
        "INSERT INTO bandwidth_samples (server_id, ts, kbps, sessions) VALUES (?, ?, ?, ?)",
        (result.id, ts, sum(s.bandwidth_kbps or 0 for s in result.sessions), len(result.sessions)),
    )


def poll_once(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    transport: httpx.BaseTransport | None = None,
    now: datetime | None = None,
) -> None:
    ts = (now or datetime.now(UTC)).isoformat()
    entries = ServerStore(conn, cipher).list_with_tokens()
    cfg = app_settings.load(conn)

    def emit(event: str, title: str, message: str) -> None:
        notifications.notify(conn, cipher, event, title, message, transport)

    for result in fetch_all(entries, transport):
        was_up = _server_up.get(result.id)
        if not result.ok:
            if was_up is not False:
                log.warning("server %s is unreachable: %s", result.name, result.error)
            if was_up is True:  # only a real change, not a server that was down at startup
                emit("server_down", f"{result.name} is unreachable", result.error or "")
            _server_up[result.id] = False
            continue  # unreachable: leave open plays alone rather than guess they ended
        if was_up is False:
            log.info("server %s is reachable again", result.name)
            emit("server_up", f"{result.name} is back", "It is answering again.")
        _server_up[result.id] = True
        log.debug("polled %s: %d session(s)", result.name, len(result.sessions))
        _check_limits(result, cfg, emit)
        with conn:
            _check_new_users(conn, result, cfg, emit)  # before _record adds them to history
            _record(conn, result, ts, emit)


TICK_SECONDS = 2.0

# Held while the background thread touches the database; a restore takes it to pause the thread.
DB_WORK = threading.Lock()


def run_forever(stop: threading.Event) -> None:
    """Polls servers at the interval from Settings (re-read every tick, so changes apply at once)
    and runs housekeeping. A poll interval of 0 means history is not recorded."""
    path = db_path()
    conn = connect(path)
    try:
        migrate(conn)
        close_stale(conn)
        log.info("background poller started")
        next_poll = 0.0
        while not stop.is_set():
            try:
                with DB_WORK:
                    cfg = app_settings.load(conn)
                    logging_setup.set_level(cfg.log_level)
                    now = time.monotonic()
                    if cfg.poll_seconds > 0 and now >= next_poll:
                        next_poll = now + cfg.poll_seconds
                        # Re-read each time: a restore can swap the key under us.
                        poll_once(conn, TokenCipher.for_db(path))
                    maintenance.run(conn, path, cfg)
            except Exception as e:  # noqa: BLE001 - keep alive; log type only (text may hold URLs)
                log.warning("background task failed: %s: %s", type(e).__name__, e)
            stop.wait(TICK_SECONDS)
    finally:
        conn.close()
