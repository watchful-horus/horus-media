"""Playback problems: buffering, stalled playback, a conversion that cannot keep up.

Horus only reads what Plex and Emby say about a playing stream, once per poll, so this is a
best guess from the server's side of things:

  buffering          the player itself says it is buffering (Plex only; Emby has no such state)
  stalled            not paused, yet the position has not moved for a while (both servers)
  slow_transcode     the server converts slower than playback and is not just pausing because
                     it is far enough ahead. Plex reports the speed. Emby does not, so for Emby
                     it is worked out here: how far the conversion moved between two polls,
                     against the time that passed (see `_measure`)
  direct_play_error  Emby fell back to converting because direct play failed

A problem must be seen on two polls in a row before it counts, so a seek or the short buffer
at the start of a play is not recorded. Something shorter than the poll interval is missed.
The poller's thread records; the Activity page only reads what is open right now.
"""

import logging
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, tzinfo

from .models import ServerActivity, Session
from .stats import UNKNOWN, _bounds, _local, _ranked

log = logging.getLogger(__name__)

KINDS = {
    "buffering": "Buffering",
    "stalled": "Stalled",
    "slow_transcode": "Slow transcode",
    "direct_play_error": "Direct play failed",
}
INTERRUPTIONS = ("buffering", "stalled")  # the kinds a viewer notices as a pause
CONFIRM_POLLS = 2
# Players report their position every 10 to 20 seconds, so a position that is the same on two
# quick polls means nothing. It has to stay put this long.
STALL_SECONDS = 40
SLOW_BELOW = 0.95  # of playback speed
REPEATED = 3  # this many interruptions in one play is "keeps buffering"
RECENT = 200  # rows in the list on the Graphs page


@dataclass
class _Track:
    """What the poller remembers about one playing stream between polls."""
    position: int | None
    moved_at: datetime
    still_since: str | None = None  # first poll that saw the position unchanged
    seen: Counter = field(default_factory=Counter)  # polls in a row, per kind
    first: dict[str, str] = field(default_factory=dict)  # when each run of polls began
    open: dict[str, int] = field(default_factory=dict)  # kind -> row id of the open problem
    interruptions: int = 0
    notified: set[str] = field(default_factory=set)
    # For a server that reports how far it has converted but no speed (Emby): the last reading
    # that can be measured from, and the speed worked out at the latest poll.
    converted: int | None = None
    converted_at: datetime | None = None
    converted_from: int | None = None  # the player's position at that reading
    speed: float | None = None


_lock = threading.Lock()
_tracks: dict[tuple[int, str, str], _Track] = {}


def forget() -> None:
    """Drop everything remembered (tests, and a restore that swapped the database)."""
    with _lock:
        _tracks.clear()


def _clock(ms: int) -> str:
    total = max(0, ms // 1000)
    hours, minutes, seconds = total // 3600, total % 3600 // 60, total % 60
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


MEASURE_MIN_SECONDS = 5  # two readings closer together than this say too little
SEEK_SLACK_MS = 30_000  # the player's position moved this much more or less than the clock


def _measure(s: Session, track: _Track, now: datetime) -> None:
    """Work out a conversion speed for a stream whose server gives none: media time converted
    since the last poll, divided by the time that passed. A healthy server converts in bursts
    and waits in between, so over a whole poll it still averages at least 1; one that cannot
    keep up never gets to wait and stays below 1.

    Only two readings in a row taken while the server was working (not waiting, not paused, no
    seek in between) are compared; anything else starts again from this reading."""
    usable = (s.play_method == "transcode" and s.transcoded_ms is not None
              and s.transcode_speed is None and not s.transcode_throttled
              and s.state == "playing")
    track.speed = None
    if usable and track.converted is not None and track.converted_at is not None:
        elapsed_ms = (now - track.converted_at).total_seconds() * 1000
        moved = s.transcoded_ms - track.converted
        played = (s.position_ms - track.converted_from
                  if s.position_ms is not None and track.converted_from is not None else None)
        seeked = played is not None and abs(played - elapsed_ms) > SEEK_SLACK_MS
        if elapsed_ms >= MEASURE_MIN_SECONDS * 1000 and moved >= 0 and not seeked:
            track.speed = round(moved / elapsed_ms, 2)
            s.transcode_speed = track.speed
    if usable:
        track.converted, track.converted_at = s.transcoded_ms, now
        track.converted_from = s.position_ms
    else:
        track.converted = track.converted_at = track.converted_from = None


def _signals(s: Session, track: _Track, now: datetime, ts: str) -> dict[str, str | None]:
    """The problems this poll shows for one stream, with a short detail for each."""
    found: dict[str, str | None] = {}
    if s.direct_play_failed:
        found["direct_play_error"] = s.transcode_reason
    moved = s.position_ms != track.position
    if moved or s.state != "playing" or s.position_ms is None:
        # Paused, buffering, just moved, or no position at all (live TV): start counting afresh.
        track.position, track.moved_at, track.still_since = s.position_ms, now, None
    if s.state == "paused":
        return found
    if s.state == "buffering":
        found["buffering"] = None
    elif not moved and s.position_ms is not None:
        track.still_since = track.still_since or ts
        if (now - track.moved_at).total_seconds() >= STALL_SECONDS:
            found["stalled"] = f"Position stayed at {_clock(s.position_ms)}"
    if (s.play_method == "transcode" and s.transcode_speed is not None
            and s.transcode_speed < SLOW_BELOW and not s.transcode_throttled):
        found["slow_transcode"] = f"Converting at {s.transcode_speed:g}x playback speed"
    return found


def _needed(kind: str) -> int:
    # A stall has already waited STALL_SECONDS over at least two polls; Emby's direct play
    # error does not come and go.
    return 1 if kind in ("stalled", "direct_play_error") else CONFIRM_POLLS


def observe(conn: sqlite3.Connection, result: ServerActivity, ts: str, emit=None) -> None:
    """Look at one server's sessions from one poll: open, extend and close problems."""
    emit = emit or (lambda *args: None)
    now = datetime.fromisoformat(ts)
    with _lock:
        seen_keys = set()
        for s in result.sessions:
            key = (result.id, s.session_key, s.title)
            seen_keys.add(key)
            track = _tracks.get(key)
            if track is None:
                track = _tracks[key] = _Track(position=s.position_ms, moved_at=now)
            _measure(s, track, now)
            found = _signals(s, track, now, ts)
            for kind in KINDS:
                if kind not in found:
                    _close(conn, track, kind)
                    continue
                track.seen[kind] += 1
                started = track.first.setdefault(
                    kind, (track.still_since or ts) if kind == "stalled" else ts)
                if kind in track.open:
                    conn.execute("UPDATE playback_problems SET last_seen_at = ?, detail = ? "
                                 "WHERE id = ?", (ts, found[kind], track.open[kind]))
                elif track.seen[kind] >= _needed(kind):
                    _open(conn, result, s, track, kind, found[kind], started, ts, emit)
        for key in [k for k in _tracks if k[0] == result.id and k not in seen_keys]:
            track = _tracks.pop(key)
            for kind in list(track.open):
                _close(conn, track, kind)


def _open(conn, result: ServerActivity, s: Session, track: _Track, kind: str,
          detail: str | None, started: str, ts: str, emit) -> None:
    where = (s.location or "").split(":")[0] or None  # LAN or WAN, never the address
    track.open[kind] = conn.execute(
        "INSERT INTO playback_problems (server_id, session_key, kind, user, title, device, "
        "platform, play_method, location, detail, started_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (result.id, s.session_key, kind, s.user, s.title, s.device, s.platform, s.play_method,
         where, detail, started, ts),
    ).lastrowid
    label = KINDS[kind]
    log.info("playback problem: %s for %s - %s on %s (%s)", label.lower(), s.user, s.title,
             result.name, s.device)
    about = f"{s.title} on {result.name} ({s.device}, {s.play_method.replace('_', ' ')})"
    if detail:
        about += f". {detail}."
    if kind in INTERRUPTIONS:
        track.interruptions += 1
        if track.interruptions == REPEATED:
            emit("playback_problem", f"{s.user}: keeps buffering",
                 f"Interrupted {REPEATED} times in this play. {about}")
            return
    if kind not in track.notified:  # once per kind and play, not for every hiccup
        track.notified.add(kind)
        emit("playback_problem", f"{s.user}: {label.lower()}", about)


def _close(conn, track: _Track, kind: str) -> None:
    track.seen.pop(kind, None)
    track.first.pop(kind, None)
    row = track.open.pop(kind, None)
    if row is not None:  # it ended some time after it was last seen
        conn.execute("UPDATE playback_problems SET ended_at = last_seen_at WHERE id = ?", (row,))


def close_stale(conn: sqlite3.Connection) -> None:
    """Close problems left open by a previous run."""
    conn.execute("UPDATE playback_problems SET ended_at = last_seen_at WHERE ended_at IS NULL")


def annotate(servers: list[ServerActivity]) -> None:
    """Fill in `problems` on live sessions from what the poller has open right now, and the
    conversion speed the poller worked out where the server itself gives none."""
    with _lock:
        for server in servers:
            for s in server.sessions:
                track = _tracks.get((server.id, s.session_key, s.title))
                if track is None:
                    continue
                if (track.speed is not None and s.transcode_speed is None
                        and not s.transcode_throttled and s.state == "playing"):
                    s.transcode_speed = track.speed
                s.problems = [k for k in KINDS if k in track.open]
                if track.interruptions >= REPEATED:
                    s.problems.append("repeated")


def _seconds(row: sqlite3.Row) -> int:
    return int((datetime.fromisoformat(row["last_seen_at"])
                - datetime.fromisoformat(row["started_at"])).total_seconds())


def rows_between(conn: sqlite3.Connection, lo: str, hi: str, only_user: str | None = None,
                 limit: int | None = None, offset: int = 0) -> list[dict]:
    """Recorded problems that started in [lo, hi), newest first."""
    found = conn.execute(
        "SELECT p.*, s.name AS server FROM playback_problems p "
        "JOIN servers s ON s.id = p.server_id WHERE p.started_at >= ? AND p.started_at < ? "
        + ("AND p.user = ? COLLATE NOCASE " if only_user is not None else "")
        + "ORDER BY p.started_at DESC, p.id DESC"
        + (" LIMIT ? OFFSET ?" if limit is not None else ""),
        [lo, hi, *([only_user] if only_user is not None else []),
         *([limit, offset] if limit is not None else [])],
    ).fetchall()
    return [{
        "id": r["id"], "server": r["server"], "kind": r["kind"], "user": r["user"],
        "title": r["title"], "device": r["device"], "platform": r["platform"],
        "play_method": r["play_method"], "location": r["location"], "detail": r["detail"],
        "started_at": r["started_at"], "ended_at": r["ended_at"],
        "duration_seconds": _seconds(r),
    } for r in found]


def compute(conn: sqlite3.Connection, start: date, end: date, tz: tzinfo | None = None,
            only_user: str | None = None) -> dict:
    """What the Graphs page shows about playback problems in a date range."""
    lo, hi = _bounds(start, end, tz)
    rows = rows_between(conn, lo, hi, only_user)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    per_day = {kind: Counter() for kind in KINDS}
    by_user, by_device, by_platform, by_kind = Counter(), Counter(), Counter(), Counter()
    for r in rows:
        if r["kind"] in per_day:
            per_day[r["kind"]][_local(r["started_at"], tz).date()] += 1
        by_user[r["user"] or UNKNOWN] += 1
        by_device[r["device"] or UNKNOWN] += 1
        by_platform[r["platform"] or UNKNOWN] += 1
        by_kind[KINDS.get(r["kind"], r["kind"])] += 1
    first = conn.execute("SELECT MIN(started_at) FROM playback_problems").fetchone()[0]
    return {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "total": len(rows),
        "since": _local(first, tz).isoformat() if first else None,
        "kinds": KINDS,
        "per_day": {
            "days": [d.isoformat() for d in days],
            "series": [{"name": KINDS[k], "data": [per_day[k][d] for d in days]} for k in KINDS],
        },
        "by_kind": _ranked(by_kind),
        "by_user": _ranked(by_user),
        "by_device": _ranked(by_device),
        "by_platform": _ranked(by_platform),
        "recent": rows[:RECENT],
    }
