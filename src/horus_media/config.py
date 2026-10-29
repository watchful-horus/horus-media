import os
from pathlib import Path

# Where "check for updates" looks unless the person changes it in Settings (a plain read of the
# latest release; nothing is sent about the user or their servers).
DEFAULT_UPDATE_URL = "https://api.github.com/repos/watchful-horus/horus-media/releases/latest"


def db_path() -> Path:
    return Path(os.environ.get("HORUS_DB_PATH", "./data/horus.db"))


def poll_seconds() -> float:
    """Seconds between polls; 0 disables the background poller."""
    try:
        return max(0.0, float(os.environ.get("HORUS_POLL_SECONDS", "15")))
    except ValueError:
        return 15.0
