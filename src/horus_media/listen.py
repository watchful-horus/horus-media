"""Where Horus listens: the HTTP port and whether other computers may connect.

Precedence for each: environment variable (HORUS_PORT / HORUS_HOST), else the saved setting.
Changes need a restart. `python -m horus_media` resolves them at start and records what it
used in HORUS_RUNNING_* so the Settings page can show it; when Horus is started some other way
(for example `uvicorn ... --port 9000`) the command line decides and the page says so.
"""

import os
import socket
import sqlite3
import time
from dataclasses import dataclass

from . import app_settings, auth

LOCALHOST = "127.0.0.1"
ALL_INTERFACES = "0.0.0.0"


# What the saved port / network settings were when this process started. If they differ from
# what is saved now, a restart is needed. This works however Horus was started (python -m
# horus_media or uvicorn), because a restart always goes through `python -m horus_media`.
_STARTED_WITH: dict | None = None


def remember_startup(cfg) -> None:
    global _STARTED_WITH
    _STARTED_WITH = {"http_port": cfg.http_port, "listen_all": cfg.listen_all}


def restart_needed(cfg) -> bool:
    if _STARTED_WITH is None:
        remember_startup(cfg)
    return _STARTED_WITH != {"http_port": cfg.http_port, "listen_all": cfg.listen_all}


def in_container() -> bool:
    """Inside Docker/Kubernetes, where the container (not Horus) owns start, stop and ports."""
    return (os.path.exists("/.dockerenv") or bool(os.environ.get("KUBERNETES_SERVICE_HOST"))
            or bool(os.environ.get("HORUS_CONTAINER")))


@dataclass
class Listen:
    host: str
    port: int
    host_source: str  # "environment" | "setting"
    port_source: str


def resolve(conn: sqlite3.Connection) -> Listen:
    cfg = app_settings.load(conn)
    host_env, port_env = os.environ.get("HORUS_HOST"), os.environ.get("HORUS_PORT")
    host = host_env or (ALL_INTERFACES if cfg.listen_all else LOCALHOST)
    if (not host_env and host == ALL_INTERFACES and not auth.admin_exists(conn)
            and os.environ.get("HORUS_OPEN_FIRST_RUN") != "1"):
        # Never open to the network before there is a login, unless the installer said that the
        # first account is created in the browser (HORUS_OPEN_FIRST_RUN=1, set by the Proxmox
        # installers). That choice only matters until the first admin exists.
        host = LOCALHOST
    return Listen(
        host=host,
        port=int(port_env) if port_env else cfg.http_port,
        host_source="environment" if host_env else "setting",
        port_source="environment" if port_env else "setting",
    )


def record_running(listen: Listen) -> None:
    os.environ["HORUS_RUNNING_HOST"] = listen.host
    os.environ["HORUS_RUNNING_PORT"] = str(listen.port)
    os.environ["HORUS_HOST_SOURCE"] = listen.host_source
    os.environ["HORUS_PORT_SOURCE"] = listen.port_source


def running() -> dict:
    """What this process was started with, or None values when not started via -m horus_media."""
    port = os.environ.get("HORUS_RUNNING_PORT")
    return {
        "host": os.environ.get("HORUS_RUNNING_HOST"),
        "port": int(port) if port else None,
        "host_source": os.environ.get("HORUS_HOST_SOURCE", "command"),
        "port_source": os.environ.get("HORUS_PORT_SOURCE", "command"),
        "can_restart": port is not None,
    }


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if os.name != "nt":
            # The server binds this way too. Without it, the closed connections of the process
            # that just stopped (TIME_WAIT, about a minute) make a free port look taken.
            # Not on Windows, where this option would let a port in use pass.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def wait_until_free(host: str, port: int, timeout: float = 20.0) -> bool:
    """After a restart the old process may still be letting go of the port."""
    deadline = time.monotonic() + timeout
    while not port_is_free(host, port):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
    return True
