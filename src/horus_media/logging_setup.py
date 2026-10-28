"""Application logging: a rotating file next to the database, the console, and a scrubber.

Everything that reaches a log (file or console) passes through `scrub`, so tokens, API keys,
passwords and credentials embedded in URLs never get written, even inside tracebacks. Code
should still avoid logging secrets in the first place; this is the safety net.
"""

import logging
import os
import re
from collections import deque
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_NAME = "horus.log"
MAX_BYTES = 2_000_000
BACKUPS = 4
LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR"]

_SECRET_WORDS = r"x-plex-token|x-emby-token|authtoken|access[_-]?token|api[_-]?key|token|password|secret"
_PATTERNS = [
    # token=abc, "token": "abc", X-Plex-Token: abc
    (re.compile(rf"(?i)((?:{_SECRET_WORDS})[\"']?\s*[=:]\s*[\"']?)([^\s&\"',;)}}\]]+)"), r"\1***"),
    # https://user:pass@host
    (re.compile(r"(?i)(://)[^/\s:@]+:[^/\s@]+@"), r"\1***@"),
    # Horus API keys (hk_...)
    (re.compile(r"hk_[A-Za-z0-9_-]{10,}"), "hk_***"),
    # Authorization: Bearer abc
    (re.compile(r"(?i)(authorization[\"']?\s*[=:]\s*[\"']?(?:bearer|basic)\s+)[^\s\"']+"), r"\1***"),
]


def scrub(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class ScrubbingFormatter(logging.Formatter):
    """One line per event: `2026-10-27T18:07:18.460Z INFO  horus_media.poller: message`.
    Tracebacks follow on indented lines, so a line starting with a date is always a new event."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        stamp = datetime.fromtimestamp(record.created, UTC)
        return stamp.strftime("%Y-%m-%dT%H:%M:%S.") + f"{stamp.microsecond // 1000:03d}Z"

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        head, *rest = scrub(text).split("\n")
        return "\n".join([head, *("    " + line for line in rest)])


_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_OURS = "_horus_handler"
_LOGGERS = ("horus_media", "uvicorn.error")


def log_path(db_file: Path) -> Path:
    return db_file.parent / "logs" / LOG_NAME


def configure(db_file: Path, level: str = "INFO") -> Path:
    """Attach the file and console handlers. Safe to call more than once."""
    path = log_path(db_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    formatter = ScrubbingFormatter(_FORMAT)
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        for handler in [h for h in logger.handlers if getattr(h, _OURS, False)]:
            logger.removeHandler(handler)
            handler.close()
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        file_handler = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUPS,
                                           encoding="utf-8")
        handlers: list[logging.Handler] = [file_handler]
        if name == "horus_media":  # uvicorn already prints its own messages to the console
            handlers.append(logging.StreamHandler())
        for handler in handlers:
            handler.setFormatter(formatter)
            setattr(handler, _OURS, True)
            logger.addHandler(handler)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # not supported on every filesystem
    # Libraries log request URLs at INFO; our own events are what matter here.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    set_level(level)
    return path


def set_level(level: str) -> None:
    logging.getLogger("horus_media").setLevel(level if level in LEVELS else "INFO")


def clear(db_file: Path) -> None:
    """Empty the log: the current file is truncated in place (Horus keeps writing to it) and the
    older rotated files are deleted."""
    path = log_path(db_file)
    if path.is_file():
        with path.open("r+b") as f:
            f.truncate(0)
    for old in path.parent.glob(f"{path.name}.*"):
        try:
            old.unlink()
        except OSError:
            pass  # in use or already gone; the next rotation overwrites it anyway


def shutdown() -> None:
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        for handler in [h for h in logger.handlers if getattr(h, _OURS, False)]:
            logger.removeHandler(handler)
            handler.close()


_LINE = re.compile(r"^(\d{4}-\d\d-\d\dT[\d:.]+Z) (\w+)\s+([\w.]+): (.*)$")
_RANK = {name: i for i, name in enumerate(LEVELS)}


def read_entries(db_file: Path, limit: int = 500, min_level: str = "DEBUG",
                 search: str = "") -> list[dict]:
    """The newest `limit` events (oldest first), read from the current log file."""
    path = log_path(db_file)
    if not path.is_file():
        return []
    with path.open("rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - 1_500_000))
        raw = f.read().decode("utf-8", errors="replace").replace("\r\n", "\n")
    entries: list[dict] = []
    for line in raw.split("\n"):
        m = _LINE.match(line)
        if m:
            entries.append({"ts": m[1], "level": m[2], "logger": m[3], "message": m[4]})
        elif entries and line.startswith("    "):
            entries[-1]["message"] += "\n" + line[4:]
    floor = _RANK.get(min_level, 0)
    needle = search.lower()
    kept = deque(maxlen=limit)
    for e in entries:
        if _RANK.get(e["level"], 0) < floor:
            continue
        if needle and needle not in (e["message"] + " " + e["logger"]).lower():
            continue
        kept.append(e)
    return list(kept)
