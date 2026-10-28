"""Housekeeping the background thread does between polls: pruning old history, scheduled
backups and the optional daily update check. Each step decides for itself whether it is due."""

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
UPDATE_EVERY = timedelta(days=1)


def _due(conn: sqlite3.Connection, key: str, every: timedelta, now: datetime) -> bool:
    last = app_settings.get_state(conn, key)
    if not last:
        return True
    try:
        return now - datetime.fromisoformat(last) >= every
    except ValueError:
        return True


def prune_history(conn: sqlite3.Connection, days: int, now: datetime) -> None:
    cutoff = (now - timedelta(days=days)).isoformat()
    with conn:
        plays = conn.execute(
            "DELETE FROM history WHERE ended_at IS NOT NULL AND started_at < ?", (cutoff,)
        ).rowcount
        samples = conn.execute("DELETE FROM bandwidth_samples WHERE ts < ?", (cutoff,)).rowcount
    if plays or samples:
        log.info("retention: removed %d play(s) and %d bandwidth sample(s) older than %d days",
                 plays, samples, days)


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

    if cfg.backup_enabled and _due(
        conn, "last_backup", timedelta(hours=cfg.backup_interval_hours), now
    ):
        made = backup.write_full_backup(db_file, now)
        backup.prune(db_file, cfg.backup_keep)
        log.info("scheduled backup written: %s (keeping %d)", made.name, cfg.backup_keep)
        app_settings.set_state(conn, "last_backup", now.isoformat())

    if cfg.report_schedule != "off":
        send_scheduled_report(conn, db_file, cfg, now, transport)

    url = updates.effective_url(cfg.update_url)
    if cfg.update_check and url and _due(conn, "last_update_check", UPDATE_EVERY, now):
        app_settings.set_state(conn, "last_update_check", now.isoformat())  # even on failure
        try:
            result = updates.check(url, transport)
            app_settings.set_state(conn, "update_latest", result["latest"])
            log.info("update check: latest is %s, newer=%s", result["latest"], result["newer"])
        except updates.UpdateCheckError as e:
            log.warning("update check failed: %s", e)
