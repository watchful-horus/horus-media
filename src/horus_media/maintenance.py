"""Housekeeping the background thread does between polls: pruning old history, thinning old
bandwidth samples, scheduled backups and the optional daily update check. Each step decides for itself whether it is due."""

import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from . import app_settings, backup, notifications, reports, updates
from .app_settings import Settings
from .crypto import TokenCipher

log = logging.getLogger(__name__)

PRUNE_EVERY = timedelta(hours=1)


def _due(conn: sqlite3.Connection, key: str, every: timedelta, now: datetime) -> bool:
    last = app_settings.get_state(conn, key)
    if not last:
        return True
    try:
        return now - datetime.fromisoformat(last) >= every
    except ValueError:
        return True


def due_daily(conn: sqlite3.Connection, key: str, hhmm: str, tz, now: datetime, days: int = 1) -> bool:
    """True when a run at HH:MM (in `tz`, or this machine's zone) is due, every `days` day(s).
    A first run (nothing recorded yet) is due at once."""
    last = app_settings.get_state(conn, key)
    if not last:
        return True
    hour, minute = (int(n) for n in hhmm.split(":"))

    def slot_at_or_before(moment: datetime) -> datetime:
        local = moment.astimezone(tz)
        slot = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return slot - timedelta(days=1) if slot > local else slot

    try:
        previous = datetime.fromisoformat(last)
    except ValueError:
        return True
    return slot_at_or_before(now) - slot_at_or_before(previous) >= timedelta(days=days)


def prune_history(conn: sqlite3.Connection, days: int, now: datetime) -> None:
    cutoff = (now - timedelta(days=days)).isoformat()
    with conn:
        plays = conn.execute(
            "DELETE FROM history WHERE ended_at IS NOT NULL AND started_at < ?", (cutoff,)
        ).rowcount
        samples = conn.execute("DELETE FROM bandwidth_samples WHERE ts < ?", (cutoff,)).rowcount
        conn.execute("DELETE FROM playback_problems WHERE ended_at IS NOT NULL AND started_at < ?",
                     (cutoff,))
    if plays or samples:
        log.info("retention: removed %d play(s) and %d bandwidth sample(s) older than %d days",
                 plays, samples, days)


THIN_AFTER = timedelta(days=30)
THIN_EVERY = timedelta(days=1)


def _five_minutes(moment: datetime) -> datetime:
    moment = moment.astimezone(UTC)
    return moment.replace(minute=moment.minute - moment.minute % 5, second=0, microsecond=0)


def thin_samples(conn: sqlite3.Connection, now: datetime) -> tuple[int, int]:
    """Replace bandwidth samples older than a month by one per server and five minutes, holding
    the average, the peak and the most streams seen. Returns (samples before, samples after)
    for the days it changed. Work is done a day at a time, each day in one transaction, and
    where it got to is remembered, so a run that is cut short just continues next time. The
    edge sits on a five-minute boundary, so no five minutes are thinned in two halves."""
    cutoff = _five_minutes(now - THIN_AFTER)
    done_until = app_settings.get_state(conn, "samples_thinned_until")
    first = conn.execute("SELECT MIN(ts) FROM bandwidth_samples").fetchone()[0]
    before = after = 0
    if first:
        start = max(datetime.fromisoformat(t) for t in (done_until, first) if t)
        day = start.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        while day < cutoff:
            lo, hi = day.isoformat(), min(day + timedelta(days=1), cutoff).isoformat()
            rows = conn.execute(
                "SELECT server_id, ts, kbps, COALESCE(peak_kbps, kbps) AS peak, sessions "
                "FROM bandwidth_samples WHERE ts >= ? AND ts < ?", (lo, hi)).fetchall()
            groups: dict[tuple[int, datetime], list[sqlite3.Row]] = {}
            for r in rows:
                slot = _five_minutes(datetime.fromisoformat(r["ts"]))
                groups.setdefault((r["server_id"], slot), []).append(r)
            if len(groups) < len(rows):  # otherwise this day is already one per five minutes
                with conn:
                    conn.execute("DELETE FROM bandwidth_samples WHERE ts >= ? AND ts < ?", (lo, hi))
                    conn.executemany(
                        "INSERT INTO bandwidth_samples (server_id, ts, kbps, peak_kbps, sessions) "
                        "VALUES (?, ?, ?, ?, ?)",
                        [(sid, slot.isoformat(), round(sum(r["kbps"] for r in g) / len(g)),
                          max(r["peak"] for r in g), max(r["sessions"] for r in g))
                         for (sid, slot), g in sorted(groups.items(), key=lambda kv: kv[0][1])])
                before += len(rows)
                after += len(groups)
            day += timedelta(days=1)
    app_settings.set_state(conn, "samples_thinned_until", cutoff.isoformat())
    return before, after


def thin_old_samples(conn: sqlite3.Connection, db_file: Path, now: datetime) -> None:
    """The daily run of `thin_samples`. The very first time there is something to thin, a full
    backup is written first, because the fine-grained samples cannot be brought back."""
    if not app_settings.get_state(conn, "samples_thinned_until"):
        old = conn.execute("SELECT COUNT(*) FROM bandwidth_samples WHERE ts < ?",
                           (_five_minutes(now - THIN_AFTER).isoformat(),)).fetchone()[0]
        if old:
            made = backup.write_full_backup(db_file, now)
            log.info("backup written before old bandwidth samples are thinned for the first "
                     "time: %s (%d samples older than 30 days)", made.name, old)
    before, after = thin_samples(conn, now)
    if before:
        log.info("bandwidth samples older than 30 days thinned to five-minute averages: "
                 "%d became %d", before, after)


REPORT_DAYS = {"weekly": 7, "monthly": 30}


def send_scheduled_report(conn: sqlite3.Connection, db_file: Path, cfg: Settings, now: datetime,
                          transport: httpx.BaseTransport | None = None) -> None:
    days = REPORT_DAYS[cfg.report_schedule]
    if not app_settings.get_state(conn, "last_report"):
        # Just switched on: the first report goes out one period from now, not at once.
        app_settings.set_state(conn, "last_report", now.isoformat())
        return
    if not _due(conn, "last_report", timedelta(days=days), now):
        return
    app_settings.set_state(conn, "last_report", now.isoformat())  # even if sending fails
    start, end = reports.last_days(days, now.astimezone(cfg.zone()).date())
    text = reports.to_text(reports.build(conn, start, end, cfg.zone()))
    notifications.notify(conn, TokenCipher.for_db(db_file), "report",
                         f"Horus {cfg.report_schedule} report", text, transport, wait=True)
    log.info("%s report sent", cfg.report_schedule)


def run(
    conn: sqlite3.Connection,
    db_file: Path,
    cfg: Settings,
    now: datetime | None = None,
    transport: httpx.BaseTransport | None = None,
) -> None:
    now = now or datetime.now(UTC)

    if cfg.retention_days and _due(conn, "last_prune", PRUNE_EVERY, now):
        prune_history(conn, cfg.retention_days, now)
        app_settings.set_state(conn, "last_prune", now.isoformat())

    if _due(conn, "last_thin", THIN_EVERY, now):
        thin_old_samples(conn, db_file, now)
        app_settings.set_state(conn, "last_thin", now.isoformat())

    # Whole days (24, 48, ...) run at the chosen time of day; any other number of hours is a plain interval.
    on_the_clock = cfg.backup_interval_hours % 24 == 0
    if cfg.backup_enabled and (
        due_daily(conn, "last_backup", cfg.backup_time, cfg.zone(), now, cfg.backup_interval_hours // 24)
        if on_the_clock
        else _due(conn, "last_backup", timedelta(hours=cfg.backup_interval_hours), now)
    ):
        made = backup.write_full_backup(db_file, now)
        backup.prune(db_file, cfg.backup_keep)
        log.info("scheduled backup written: %s (keeping %d)", made.name, cfg.backup_keep)
        app_settings.set_state(conn, "last_backup", now.isoformat())

    if cfg.report_schedule != "off":
        send_scheduled_report(conn, db_file, cfg, now, transport)

    url = updates.effective_url(cfg.update_url)
    if cfg.update_check and url and due_daily(
        conn, "last_update_check", cfg.update_check_time, cfg.zone(), now
    ):
        app_settings.set_state(conn, "last_update_check", now.isoformat())  # even on failure
        try:
            result = updates.check(url, transport)
            app_settings.set_state(conn, "update_latest", result["latest"])
            log.info("update check: latest is %s, newer=%s", result["latest"], result["newer"])
        except updates.UpdateCheckError as e:
            log.warning("update check failed: %s", e)
