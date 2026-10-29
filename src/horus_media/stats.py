"""Aggregates play history into the datasets behind the graphs.

Days, hours and weekdays use the time zone from Settings (default: the host's own, so set TZ
in Docker); timestamps are stored in UTC.
"""

import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta, tzinfo

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
UNKNOWN = "Unknown"
METHODS = ["direct_play", "direct_stream", "transcode"]
HOURLY_MAX_DAYS = 90
HOUR = timedelta(hours=1)


def _local(ts: str, tz: tzinfo | None) -> datetime:
    return datetime.fromisoformat(ts).astimezone(tz)


def _bounds(start: date, end: date, tz: tzinfo | None) -> tuple[str, str]:
    """Inclusive local dates -> UTC ISO strings [lo, hi)."""
    lo = datetime.combine(start, time.min, tzinfo=tz).astimezone(UTC)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=tz).astimezone(UTC)
    return lo.isoformat(), hi.isoformat()


def _ranked(counter: Counter) -> list[dict]:
    return [{"name": n, "count": c} for n, c in counter.most_common()]


def _bucket(extent: timedelta) -> timedelta:
    """Bandwidth bucket size from how much recorded data there is, so a few minutes of data
    still draws a line instead of collapsing into one hourly point."""
    if extent <= timedelta(hours=3):
        return timedelta(minutes=1)
    if extent <= timedelta(hours=24):
        return timedelta(minutes=5)
    if extent <= timedelta(days=3):
        return timedelta(minutes=15)
    if extent <= timedelta(days=14):
        return timedelta(hours=1)
    if extent <= timedelta(days=62):
        return timedelta(hours=3)
    return timedelta(days=1)


def _floor(dt: datetime, size: timedelta) -> datetime:
    if size >= timedelta(days=1):
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if size >= timedelta(hours=1):
        return dt.replace(minute=0, second=0, microsecond=0)
    minutes = size.seconds // 60
    return dt.replace(minute=dt.minute - dt.minute % minutes, second=0, microsecond=0)


def compute_stats(
    conn: sqlite3.Connection, start: date, end: date, tz: tzinfo | None = None,
    hourly: bool = False, only_user: str | None = None,
) -> dict:
    """`only_user` limits everything to one person's plays (and hides bandwidth, which is
    server-wide); used when normal users may only see their own activity."""
    lo, hi = _bounds(start, end, tz)
    servers = [dict(r) for r in conn.execute("SELECT id, name FROM servers ORDER BY id")]
    names = {s["id"]: s["name"] for s in servers}
    plays = conn.execute(
        "SELECT server_id, user, platform, play_method, media_type, started_at FROM history "
        "WHERE started_at >= ? AND started_at < ? "
        + ("AND user = ? COLLATE NOCASE " if only_user is not None else "")
        + "ORDER BY started_at",
        (lo, hi) if only_user is None else (lo, hi, only_user),
    ).fetchall()

    days = [(start + timedelta(days=i)) for i in range((end - start).days + 1)]
    per_day = {sid: Counter() for sid in names}
    by_user, by_platform, methods, types = Counter(), Counter(), Counter(), Counter()
    by_hour, by_weekday = [0] * 24, [0] * 7
    weekday_types: dict[str, list[int]] = {}
    per_day_method = {m: Counter() for m in METHODS}
    per_hour: dict[int, Counter] = {sid: Counter() for sid in names}
    per_hour_method = {m: Counter() for m in METHODS}
    for p in plays:
        local = _local(p["started_at"], tz)
        per_day[p["server_id"]][local.date()] += 1
        by_user[p["user"] or UNKNOWN] += 1
        by_platform[p["platform"] or UNKNOWN] += 1
        methods[p["play_method"]] += 1
        if p["play_method"] in per_day_method:
            per_day_method[p["play_method"]][local.date()] += 1
        types[p["media_type"]] += 1
        by_hour[local.hour] += 1
        if hourly:
            key = _floor(local, HOUR).isoformat()
            per_hour[p["server_id"]][key] += 1
            if p["play_method"] in per_hour_method:
                per_hour_method[p["play_method"]][key] += 1
        by_weekday[local.weekday()] += 1
        weekday_types.setdefault(p["media_type"] or UNKNOWN, [0] * 7)[local.weekday()] += 1

    samples = conn.execute(
        "SELECT server_id, ts, kbps FROM bandwidth_samples WHERE ts >= ? AND ts < ? ORDER BY ts",
        (lo, hi),
    ).fetchall() if only_user is None else []
    extent = (
        _local(samples[-1]["ts"], tz) - _local(samples[0]["ts"], tz) if samples else timedelta(0)
    )
    size = _bucket(extent)
    buckets: dict[tuple[int, datetime], list[int]] = {}
    for r in samples:
        buckets.setdefault((r["server_id"], _floor(_local(r["ts"], tz), size)), []).append(r["kbps"])
    # Combined bandwidth: every server's sample from one poll shares a timestamp, so adding
    # them gives the true total at that moment (and a true peak, unlike summing peaks).
    per_poll: dict[str, int] = {}
    for r in samples:
        per_poll[r["ts"]] = per_poll.get(r["ts"], 0) + r["kbps"]
    total_buckets: dict[datetime, list[int]] = {}
    for ts, kbps in per_poll.items():
        total_buckets.setdefault(_floor(_local(ts, tz), size), []).append(kbps)
    bandwidth_total = [
        [b.isoformat(), round(sum(v) / len(v)), max(v)] for b, v in sorted(total_buckets.items())
    ]
    bandwidth = []
    for sid, name in names.items():
        points = [
            [b.isoformat(), round(sum(v) / len(v)), max(v)]
            for (s, b), v in sorted(buckets.items(), key=lambda kv: kv[0][1])
            if s == sid
        ]
        bandwidth.append({"server": name, "points": points})

    first = conn.execute("SELECT MIN(started_at) FROM history").fetchone()[0]
    first_sample = conn.execute("SELECT MIN(ts) FROM bandwidth_samples").fetchone()[0]
    bandwidth_since = _local(first_sample, tz).isoformat() if first_sample else None
    hourly_data: dict = {}
    if hourly:
        hours: list[str] = []
        seen: set[str] = set()
        t, stop = datetime.fromisoformat(lo), datetime.fromisoformat(hi)
        while t < stop:  # every local hour in the range (DST-safe: keys carry their offset)
            key = _floor(t.astimezone(tz), HOUR).isoformat()
            if key not in seen:
                seen.add(key)
                hours.append(key)
            t += HOUR
        hourly_data = {
            "plays_per_hour": {
                "hours": hours,
                "series": [{"server": names[sid], "data": [per_hour[sid][h] for h in hours]}
                           for sid in names],
            },
            "methods_per_hour": [{"name": m, "data": [per_hour_method[m][h] for h in hours]}
                                 for m in METHODS],
        }
    return {
        **hourly_data,
        "earliest": _local(first, tz).date().isoformat() if first else None,
        "methods_per_day": [
            {"name": m, "data": [per_day_method[m][d] for d in days]} for m in METHODS
        ],
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "total_plays": len(plays),
        "servers": servers,
        "plays_per_day": {
            "days": [d.isoformat() for d in days],
            "series": [
                {"server": names[sid], "data": [per_day[sid][d] for d in days]} for sid in names
            ],
        },
        "by_user": _ranked(by_user),
        "by_platform": _ranked(by_platform),
        "by_hour": by_hour,
        "by_weekday": {
            "labels": WEEKDAYS,
            "data": by_weekday,
            # The same plays split by media type, busiest type first, so the chart can stack them.
            "by_type": [
                {"name": name, "data": counts}
                for name, counts in sorted(weekday_types.items(), key=lambda kv: -sum(kv[1]))
            ],
        },
        "play_methods": _ranked(methods),
        "media_types": _ranked(types),
        "bandwidth": bandwidth,
        "bandwidth_total": bandwidth_total,
        # When the first bandwidth sample was ever recorded (imported history has none).
        "bandwidth_since": bandwidth_since,
    }


def compute_monthly(
    conn: sqlite3.Connection, tz: tzinfo | None = None, only_user: str | None = None,
    today: date | None = None, months: int = 12,
) -> dict:
    """Plays per calendar month for the last `months` months, this month included, per server.
    Independent of the date range chosen on the Graphs page."""
    today = today or datetime.now(tz).date()
    year, month = today.year, today.month - (months - 1)
    while month < 1:
        year, month = year - 1, month + 12
    first = date(year, month, 1)
    keys = []
    for i in range(months):
        m = month - 1 + i
        keys.append(date(year + m // 12, m % 12 + 1, 1).isoformat())
    lo, hi = _bounds(first, today, tz)
    servers = [dict(r) for r in conn.execute("SELECT id, name FROM servers ORDER BY id")]
    counts = {s["id"]: Counter() for s in servers}
    rows = conn.execute(
        "SELECT server_id, started_at FROM history WHERE started_at >= ? AND started_at < ? "
        + ("AND user = ? COLLATE NOCASE" if only_user is not None else ""),
        (lo, hi) if only_user is None else (lo, hi, only_user),
    ).fetchall()
    for r in rows:
        if r["server_id"] in counts:
            counts[r["server_id"]][_local(r["started_at"], tz).date().replace(day=1).isoformat()] += 1
    return {
        "months": keys,
        "series": [{"server": s["name"], "data": [counts[s["id"]][k] for k in keys]}
                   for s in servers],
        "total": len(rows),
    }
