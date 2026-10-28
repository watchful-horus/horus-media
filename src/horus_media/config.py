import os
from pathlib import Path


def db_path() -> Path:
    return Path(os.environ.get("HORUS_DB_PATH", "./data/horus.db"))


def poll_seconds() -> float:
    """Seconds between polls; 0 disables the background poller."""
    try:
        return max(0.0, float(os.environ.get("HORUS_POLL_SECONDS", "15")))
    except ValueError:
        return 15.0
