"""Read-only data API for dashboards such as Grafana (/api/v1).

Every endpoint returns a plain JSON array (or CSV with ?format=csv), so it works directly with
Grafana's Infinity or JSON API data sources. Times are ISO 8601. `from` / `to` accept ISO dates
or date-times, or epoch milliseconds (what Grafana's ${__from} / ${__to} variables produce).
Nothing here writes to Horus or to your Plex/Emby servers, and no tokens are ever included.
"""

import csv
import io
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from . import app_settings, auth
from .activity import fetch_all
from .stats import _bucket, _floor, _local
from .store import ServerStore

MAX_SPAN = timedelta(days=3650)
Format = Literal["json", "csv"]
INTERVALS = {"1m": timedelta(minutes=1), "5m": timedelta(minutes=5), "15m": timedelta(minutes=15),
             "1h": timedelta(hours=1), "1d": timedelta(days=1)}


def parse_time(value: str | None, tz: tzinfo | None, default: datetime) -> datetime:
    """ISO date/date-time or epoch seconds/milliseconds -> an aware UTC datetime."""
    if value is None or not value.strip():
        return default
    text = value.strip()
    try:
        if text.isdigit():
            n = int(text)
            return datetime.fromtimestamp(n / 1000 if n > 10**11 else n, UTC)
        parsed = datetime.fromisoformat(text)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(422, f"Cannot read the time '{value}'") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz) if tz else parsed.astimezone()
    return parsed.astimezone(UTC)


def window(frm: str | None, to: str | None, tz: tzinfo | None) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    end = parse_time(to, tz, now)
    start = parse_time(frm, tz, end - timedelta(days=7))
    if start >= end:
        raise HTTPException(422, "'from' must be earlier than 'to'")
    if end - start > MAX_SPAN:
        raise HTTPException(422, "That range is longer than ten years")
    return start, end


def respond(rows: list[dict], fmt: Format, columns: list[str]):
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return Response(buf.getvalue(), media_type="text/csv")
    return rows


def _floor_bucket(dt: datetime, interval: str) -> datetime:
    if interval == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if interval == "day":
        return day
    if interval == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)  # month


def make_router(get_store: Callable, transport: httpx.BaseTransport | None) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    Store = Annotated[ServerStore, Depends(get_store)]

    def limit_to(request: Request, store: ServerStore) -> str | None:
        """The one Plex/Emby name this caller may see, or None for everyone."""
        return auth.only_media_user(store.conn, getattr(request.state, "user", None))

    @router.get("/servers")
    def servers(store: Store, format: Format = "json"):
        """The servers Horus watches (names and types only)."""
        rows = [{"id": r["id"], "name": r["name"], "type": r["type"]} for r in store.list()]
        return respond(rows, format, ["id", "name", "type"])

    @router.get("/activity")
    def activity(request: Request, store: Store, format: Format = "json"):
        """What is playing right now, one row per stream. Empty when nothing is playing."""
        only = limit_to(request, store)
        rows = [s.model_dump(exclude={"poster", "session_key"})
                for result in fetch_all(store.list_with_tokens(), transport)
                for s in result.sessions
                if only is None or s.user.casefold() == only.casefold()]
        columns = ["server", "user", "title", "media_type", "device", "platform", "play_method",
                   "state", "bandwidth_kbps", "progress_percent", "position_ms", "duration_ms",
                   "product", "quality", "container", "video", "audio", "subtitle", "location"]
        return respond(rows, format, columns)

    @router.get("/history")
    def history(
        request: Request,
        store: Store,
        frm: Annotated[str | None, Query(alias="from")] = None,
        to: str | None = None,
        server: str | None = None,
        user: str | None = None,
        limit: Annotated[int, Query(ge=1, le=10000)] = 1000,
        offset: Annotated[int, Query(ge=0)] = 0,
        format: Format = "json",
    ):
        """Individual plays, newest first."""
        tz = app_settings.load(store.conn).zone()
        start, end = window(frm, to, tz)
        where, args = ["h.started_at >= ?", "h.started_at < ?"], [start.isoformat(), end.isoformat()]
        only = limit_to(request, store)
        if only is not None:
            user = only  # they can't ask for anyone else's plays
        for column, value in (("s.name", server), ("h.user", user)):
            if value or (column == "h.user" and only is not None):
                where.append(f"{column} = ? COLLATE NOCASE")
                args.append(value)
        found = store.conn.execute(
            "SELECT h.id, s.name AS server, h.user, h.title, h.media_type, h.device, h.platform, "
            "h.play_method, h.bandwidth_kbps, h.started_at, h.last_seen_at, h.ended_at "
            "FROM history h JOIN servers s ON s.id = h.server_id "
            f"WHERE {' AND '.join(where)} ORDER BY h.started_at DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        rows = [{
            "id": r["id"], "server": r["server"], "user": r["user"], "title": r["title"],
            "media_type": r["media_type"], "device": r["device"], "platform": r["platform"],
            "play_method": r["play_method"], "peak_bandwidth_kbps": r["bandwidth_kbps"],
            "started_at": r["started_at"], "ended_at": r["ended_at"],
            "duration_seconds": int((datetime.fromisoformat(r["last_seen_at"])
                                     - datetime.fromisoformat(r["started_at"])).total_seconds()),
        } for r in found]
        return respond(rows, format, ["id", "server", "user", "title", "media_type", "device",
                                      "platform", "play_method", "peak_bandwidth_kbps",
                                      "started_at", "ended_at", "duration_seconds"])

    @router.get("/plays")
    def plays(
        request: Request,
        store: Store,
        frm: Annotated[str | None, Query(alias="from")] = None,
        to: str | None = None,
        interval: Literal["hour", "day", "week", "month"] = "day",
        by: Literal["none", "server", "user", "platform", "play_method", "media_type"] = "none",
        format: Format = "json",
    ):
        """How many plays started in each hour/day/week/month, optionally split by a field."""
        tz = app_settings.load(store.conn).zone()
        start, end = window(frm, to, tz)
        column = {"server": "s.name", "user": "h.user", "platform": "h.platform",
                  "play_method": "h.play_method", "media_type": "h.media_type"}.get(by, "''")
        only = limit_to(request, store)
        found = store.conn.execute(
            f"SELECT h.started_at AS ts, {column} AS grp FROM history h "
            "JOIN servers s ON s.id = h.server_id WHERE h.started_at >= ? AND h.started_at < ?"
            + (" AND h.user = ? COLLATE NOCASE" if only is not None else ""),
            (start.isoformat(), end.isoformat(), *([only] if only is not None else [])),
        ).fetchall()
        counts: Counter = Counter()
        for r in found:
            bucket = _floor_bucket(_local(r["ts"], tz), interval)
            counts[(bucket, r["grp"] or "Unknown")] += 1
        rows = []
        for (bucket, group), n in sorted(counts.items()):
            row = {"time": bucket.isoformat()}
            if by != "none":
                row[by] = group
            row["plays"] = n
            rows.append(row)
        return respond(rows, format, ["time", *([by] if by != "none" else []), "plays"])

    @router.get("/bandwidth")
    def bandwidth(
        request: Request,
        store: Store,
        frm: Annotated[str | None, Query(alias="from")] = None,
        to: str | None = None,
        interval: Literal["auto", "1m", "5m", "15m", "1h", "1d"] = "auto",
        format: Format = "json",
    ):
        """Average and peak bandwidth per server, plus a combined "Total" row, per time bucket."""
        tz = app_settings.load(store.conn).zone()
        start, end = window(frm, to, tz)
        if limit_to(request, store) is not None:  # bandwidth is server-wide, so it would show
            return respond([], format, ["time", "server", "avg_kbps", "peak_kbps"])  # others' use
        samples = store.conn.execute(
            "SELECT s.name AS server, b.ts, b.kbps FROM bandwidth_samples b "
            "JOIN servers s ON s.id = b.server_id WHERE b.ts >= ? AND b.ts < ? ORDER BY b.ts",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        if interval == "auto":
            extent = (_local(samples[-1]["ts"], tz) - _local(samples[0]["ts"], tz)
                      if samples else timedelta(0))
            size = _bucket(extent)
        else:
            size = INTERVALS[interval]
        per_server: dict[tuple[str, datetime], list[int]] = {}
        per_poll: dict[str, int] = {}
        for r in samples:
            per_server.setdefault((r["server"], _floor(_local(r["ts"], tz), size)), []).append(
                r["kbps"])
            per_poll[r["ts"]] = per_poll.get(r["ts"], 0) + r["kbps"]  # same poll shares a timestamp
        names = {r["server"] for r in samples}
        if len(names) > 1:
            for ts, kbps in per_poll.items():
                per_server.setdefault(("Total", _floor(_local(ts, tz), size)), []).append(kbps)
        rows = [
            {"time": bucket.isoformat(), "server": name, "avg_kbps": round(sum(v) / len(v)),
             "peak_kbps": max(v)}
            for (name, bucket), v in sorted(per_server.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        ]
        return respond(rows, format, ["time", "server", "avg_kbps", "peak_kbps"])

    return router
