"""Settings, backups, update-check and log API."""

import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any
from zoneinfo import available_timezones

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import ValidationError

from . import __version__, app_settings, auth, backup, install_info, listen, logging_setup, updates
from .config import db_path
from .poller import DB_WORK
from .restore_upload import restore_upload
from .store import ServerStore

log = logging.getLogger(__name__)
LOG_NAME = logging_setup.LOG_NAME
STARTED_AT = datetime.now(UTC)


def _restart_process() -> None:
    """Start a fresh `python -m horus_media`, which re-reads the settings, and end this one.

    On Linux and macOS exec replaces the process in place. Windows has no real exec (it leaves
    nothing running), so there a new process is started first and this one exits; the new one
    waits for the port to be released before it binds it (see __main__).
    """
    logging_setup.shutdown()
    args = [sys.executable, "-m", "horus_media"]
    if os.name == "nt":
        subprocess.Popen(args, close_fds=True)
        os._exit(0)
    os.execv(sys.executable, args)


def _shutdown_process() -> None:
    logging_setup.shutdown()
    os._exit(0)


def make_router(get_store: Callable, transport: httpx.BaseTransport | None) -> APIRouter:
    router = APIRouter(prefix="/api")
    Store = Annotated[ServerStore, Depends(get_store)]

    def listen_info(cfg, request: Request) -> dict:
        run = listen.running()
        boxed = listen.in_container()
        return {
            "port_running": run["port"] or request.url.port,
            "host_running": run["host"],
            "port_source": run["port_source"],  # setting | environment | command (uvicorn)
            "host_source": run["host_source"],
            "port_editable": run["port_source"] != "environment",
            "host_editable": run["host_source"] != "environment",
            "started_via_module": run["can_restart"],
            "in_container": boxed,
            # Containers own their start/stop; elsewhere Horus can restart or stop itself.
            "can_restart": not boxed,
            "can_shutdown": not boxed,
            "restart_needed": listen.restart_needed(cfg),
        }

    def check_listen_change(conn, changes: dict, request: Request) -> None:
        """Refuse port/network changes that cannot work, before anything is saved."""
        current, run = app_settings.load(conn), listen.running()
        if "http_port" in changes and changes["http_port"] != current.http_port:
            if run["port_source"] == "environment":
                raise HTTPException(409, "The port is set by the HORUS_PORT environment variable, "
                                         "so it can't be changed here")
            port = changes["http_port"]
            if (isinstance(port, int) and 1024 <= port <= 65535
                    and port != (run["port"] or request.url.port)
                    and not listen.port_is_free(run["host"] or listen.LOCALHOST, port)):
                raise HTTPException(422, f"http_port: port {port} is already in use")
        if "listen_all" in changes and changes["listen_all"] != current.listen_all:
            if run["host_source"] == "environment":
                raise HTTPException(409, "Network access is set by the HORUS_HOST environment "
                                         "variable, so it can't be changed here")
            if changes["listen_all"] is True and not (auth.admin_exists(conn) or auth.auth_disabled()):
                raise HTTPException(409, "Set an admin password before allowing other computers")

    def snapshot(store: ServerStore, request: Request) -> dict:
        conn = store.conn
        cfg = app_settings.load(conn)
        return {
            "settings": cfg.model_dump(),
            "info": {
                "version": __version__,
                # The port your browser used to reach Horus; it is set outside the app.
                "port": request.url.port or (443 if request.url.scheme == "https" else 80),
                "db_path": str(db_path().resolve()),
                "backup_dir": str(backup.backup_dir(db_path()).resolve()),
                "effective_timezone": cfg.timezone
                or datetime.now().astimezone().tzname()
                or "system",
                "timezones": sorted(available_timezones()),
                "update_source_configured": bool(updates.effective_url(cfg.update_url)),
                "update_url_from_env": bool(os.environ.get("HORUS_UPDATE_URL", "").strip()),
                "started_at": STARTED_AT.isoformat(),
                "data_dir": str(db_path().resolve().parent),
                "update_latest": app_settings.get_state(conn, "update_latest"),
                "update_checked_at": app_settings.get_state(conn, "last_update_check"),
                "last_backup_at": app_settings.get_state(conn, "last_backup"),
                "log_file": str(logging_setup.log_path(db_path()).resolve()),
                "listen": listen_info(cfg, request),
                "admin_exists": auth.admin_exists(conn),
                "auth_disabled": auth.auth_disabled(),
            },
        }

    @router.get("/settings")
    def get_settings(store: Store, request: Request):
        return snapshot(store, request)

    @router.put("/settings")
    def put_settings(changes: dict[str, Any], store: Store, request: Request):
        if getattr(request.state, "via_key", None) and "control_keys_enabled" in changes:
            raise HTTPException(403, "A control key can't change whether control keys are allowed")
        check_listen_change(store.conn, changes, request)
        try:
            app_settings.update(store.conn, changes)
            logging_setup.set_level(app_settings.load(store.conn).log_level)
            log.info("settings changed: %s", ", ".join(f"{k}={v}" for k, v in changes.items()))
        except KeyError as e:
            raise HTTPException(422, f"Unknown setting: {e.args[0]}") from e
        except ValidationError as e:
            first = e.errors()[0]
            field = ".".join(str(p) for p in first["loc"])
            raise HTTPException(422, f"{field}: {first['msg'].removeprefix('Value error, ')}") from e
        return snapshot(store, request)

    @router.get("/backups")
    def list_backups():
        return {"items": backup.list_backups(db_path())}

    @router.post("/backups", status_code=201)
    def create_backup(store: Store):
        path = backup.write_full_backup(db_path())
        cfg = app_settings.load(store.conn)
        backup.prune(db_path(), cfg.backup_keep)
        app_settings.set_state(store.conn, "last_backup", datetime.now(UTC).isoformat())
        log.info("backup written: %s", path.name)
        return {"name": path.name, "size": path.stat().st_size}

    @router.delete("/backups/{name}", status_code=204)
    def delete_backup(name: str):
        if not backup.delete_backup(db_path(), name):
            raise HTTPException(404, "Backup not found")
        return Response(status_code=204)

    @router.post("/backups/upload")
    async def upload_backup(request: Request):
        """Restore from a file you upload: a full backup (zip) or a download without tokens.
        The current state is backed up first, so this can be undone."""
        return await restore_upload(request)

    @router.post("/backups/{name}/restore")
    def restore_backup(name: str):
        """Replace everything with a saved backup. The current state is backed up first."""
        if not backup.NAME_RE.fullmatch(name):
            raise HTTPException(404, "Backup not found")
        if not DB_WORK.acquire(timeout=60):  # pauses the background poller while we swap files
            raise HTTPException(409, "Horus is busy, try again in a moment")
        try:
            return backup.restore(db_path(), name)
        except backup.RestoreError as e:
            log.warning("restore of %s refused: %s", name, e)
            raise HTTPException(404 if str(e) == "Backup not found" else 422, str(e)) from e
        finally:
            DB_WORK.release()

    # A full backup holds the encryption key and the server tokens, so only an admin can fetch
    # one (the login gate treats every route not on the user allow-list as admin-only).
    @router.get("/backups/{name}/download")
    def download_backup(name: str):
        path = backup.find_backup(db_path(), name)
        if path is None:
            raise HTTPException(404, "Backup not found")
        log.info("downloaded backup %s", name)
        return FileResponse(path, filename=name, media_type="application/zip")

    @router.get("/update-check")
    def update_check(store: Store):
        """Runs when you ask for it, and once a day while the daily check is on."""
        url = updates.effective_url(app_settings.load(store.conn).update_url)
        if not url:
            return {"configured": False}
        try:
            result = updates.check(url, transport)
        except updates.UpdateCheckError as e:
            log.warning("update check failed: %s", e)
            raise HTTPException(502, str(e)) from e
        log.info("update check: latest is %s, newer=%s", result["latest"], result["newer"])
        app_settings.set_state(store.conn, "update_latest", result["latest"])
        app_settings.set_state(store.conn, "last_update_check", datetime.now(UTC).isoformat())
        how = install_info.update_help(result["latest"]) if result["newer"] else None
        return {"configured": True, **result, **({"how": how} if how else {})}

    def actor(request: Request) -> str:
        user = getattr(request.state, "user", None)
        return user["username"] if user else "someone"

    @router.post("/restart")
    def restart(request: Request):
        """Restart Horus so a new port or network setting takes effect. Always starts a fresh
        `python -m horus_media`, which reads the saved settings, whatever started this process."""
        if listen.in_container():
            raise HTTPException(409, "Horus is running in a container: restart the container "
                                     "instead (and change the port mapping there)")
        log.warning("restart requested from the web UI by %s", actor(request))
        threading.Timer(1.0, _restart_process).start()  # after this response has been sent
        return {"restarting": True}

    @router.post("/shutdown")
    def shutdown(request: Request):
        """Stop Horus. It stays stopped until someone starts it again."""
        if listen.in_container():
            raise HTTPException(409, "Horus is running in a container: stop the container instead")
        log.warning("shutdown requested from the web UI by %s", actor(request))
        threading.Timer(1.0, _shutdown_process).start()
        return {"stopping": True}

    @router.get("/logs")
    def get_logs(limit: int = 500, level: str = "DEBUG", q: str = ""):
        """The newest log events (oldest first). Everything was scrubbed when it was written."""
        if level not in logging_setup.LEVELS:
            raise HTTPException(422, "Unknown level")
        entries = logging_setup.read_entries(db_path(), min(max(limit, 1), 2000), level, q.strip())
        return {"entries": entries, "level": logging.getLevelName(logging.getLogger("horus_media").level), "file": LOG_NAME}

    @router.delete("/logs", status_code=204)
    def clear_logs(request: Request):
        """Empty the log file. One line is written afterwards so it shows who did it."""
        logging_setup.clear(db_path())
        log.warning("log cleared by %s", actor(request))
        return Response(status_code=204)

    @router.get("/logs/download")
    def download_logs():
        path = logging_setup.log_path(db_path())
        if not path.is_file():
            raise HTTPException(404, "No log file yet")
        # A snapshot, not a stream: the file keeps growing while Horus runs, and a file that
        # grows after the size is announced makes the browser abort the download. The log is
        # capped at a couple of MB, so reading it whole is cheap.
        try:
            data = path.read_bytes()
        except OSError:
            raise HTTPException(404, "No log file yet") from None
        return Response(data, media_type="text/plain", headers={
            "Content-Disposition": f'attachment; filename="{LOG_NAME}"'})

    return router
