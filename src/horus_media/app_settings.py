"""User-editable settings, stored in the `settings` table as JSON values.

Environment variables only provide defaults (e.g. HORUS_POLL_SECONDS); once a value is saved
from the Settings page it wins.
"""

import json
import re
import sqlite3
from typing import Literal
from zoneinfo import ZoneInfo, available_timezones

from pydantic import BaseModel, Field, ValidationError, field_validator

from .config import DEFAULT_UPDATE_URL, poll_seconds


class Settings(BaseModel):
    timezone: str = ""  # "" = the machine's own time zone
    poll_seconds: float = Field(default_factory=poll_seconds, ge=0, le=3600)  # 0 = don't record
    retention_days: int = Field(0, ge=0, le=3650)  # 0 = keep everything
    update_check: bool = True  # daily look for a newer release; it never installs anything
    backup_enabled: bool = False
    backup_interval_hours: int = Field(24, ge=1, le=720)
    backup_keep: int = Field(7, ge=1, le=100)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    http_port: int = Field(8000, ge=1024, le=65535)  # applied at the next start
    update_url: str = Field(DEFAULT_UPDATE_URL, max_length=500)  # where "check for updates" looks; empty = none
    hide_others_activity: bool = False  # normal users see only their own plays
    listen_all: bool = False  # accept connections from other computers (needs an admin login)
    control_keys_enabled: bool = False  # API keys that can control Horus (off unless an admin allows it)

    # Optional alerts, sent through the notification channels that pick them. All off by default.
    alert_transcodes_over: int = Field(0, ge=0, le=100)  # 0 = off; alert above this many transcodes
    alert_bandwidth_over_mbps: int = Field(0, ge=0, le=100000)  # 0 = off; per server
    alert_new_user: bool = False  # someone Horus has never seen before starts playing
    report_schedule: Literal["off", "weekly", "monthly"] = "off"  # summary sent to channels

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, v: str) -> str:
        if v and v not in available_timezones():
            raise ValueError("unknown time zone")
        return v

    @field_validator("update_url")
    @classmethod
    def _web_address(cls, v: str) -> str:
        v = v.strip()
        if v and not re.fullmatch(r"https?://[^\s]+", v):
            raise ValueError("must be a web address starting with http:// or https://")
        return v

    @field_validator("poll_seconds")
    @classmethod
    def _sane_poll(cls, v: float) -> float:
        if 0 < v < 5:
            raise ValueError("must be 0 (off) or at least 5 seconds")
        return v

    def zone(self) -> ZoneInfo | None:
        return ZoneInfo(self.timezone) if self.timezone else None


def _read(conn: sqlite3.Connection) -> dict:
    stored = {}
    for key, value in conn.execute("SELECT key, value FROM settings WHERE key NOT LIKE 'state.%'"):
        try:
            stored[key] = json.loads(value)
        except ValueError:
            continue
    return {k: v for k, v in stored.items() if k in Settings.model_fields}


def load(conn: sqlite3.Connection) -> Settings:
    stored = _read(conn)
    try:
        return Settings.model_validate(stored)
    except ValidationError:
        # A bad stored value should not stop the poller: fall back to defaults for the bad keys.
        good = {}
        for key, value in stored.items():
            try:
                Settings.model_validate({key: value})
                good[key] = value
            except ValidationError:
                pass
        return Settings.model_validate(good)


def update(conn: sqlite3.Connection, changes: dict) -> Settings:
    """Apply a partial update. Raises KeyError for unknown keys, ValidationError for bad values."""
    unknown = set(changes) - set(Settings.model_fields)
    if unknown:
        raise KeyError(", ".join(sorted(unknown)))
    merged = Settings.model_validate({**load(conn).model_dump(), **changes})
    with conn:
        for key in changes:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(getattr(merged, key))),
            )
    return merged


# Internal bookkeeping (last backup time, last update check...) lives beside the settings but is
# never exposed for editing.
def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (f"state.{key}",)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"state.{key}", value),
        )
