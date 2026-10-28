"""Start Horus when you sign in to Windows (and stop doing so again).

Adds or removes one value under the current user's Run key, so no administrator rights are
needed and nothing outside your own account changes:

    python -m horus_media startup install
    python -m horus_media startup remove
    python -m horus_media startup status

The entry pins the data folder to where it is when you install, so a login start uses the same
database as a start from a terminal, whatever the "current folder" is at login.
"""

import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE = "Horus"


class StartupError(Exception):
    """Safe to show to the user."""


def _registry():
    try:
        import winreg
    except ImportError:
        raise StartupError("Start at login is a Windows feature. On Linux use a systemd "
                           "service or your container's restart policy.") from None
    return winreg


def command(db_file: Path, python: str | None = None) -> str:
    """The command Windows runs at sign-in: pythonw (no console window) with a fixed data path."""
    exe = Path(python or sys.executable)
    windowless = exe.with_name("pythonw.exe")
    if windowless.exists():
        exe = windowless
    return f'"{exe}" -m horus_media --db "{db_file.resolve()}"'


def install(db_file: Path) -> str:
    reg = _registry()
    cmd = command(db_file)
    with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
        reg.SetValueEx(key, VALUE, 0, reg.REG_SZ, cmd)
    return cmd


def remove() -> bool:
    """True if there was an entry to remove."""
    reg = _registry()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_SET_VALUE) as key:
            reg.DeleteValue(key, VALUE)
        return True
    except FileNotFoundError:
        return False


def status() -> str | None:
    """The command that runs at sign-in, or None when Horus is not set to start."""
    reg = _registry()
    try:
        with reg.OpenKey(reg.HKEY_CURRENT_USER, RUN_KEY, 0, reg.KEY_READ) as key:
            return reg.QueryValueEx(key, VALUE)[0]
    except FileNotFoundError:
        return None
