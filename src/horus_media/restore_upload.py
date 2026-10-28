"""Shared by Settings > Backups and the first-run screen: restore from an uploaded file."""

import logging

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from . import backup
from .config import db_path
from .poller import DB_WORK

log = logging.getLogger(__name__)


def _restore(path) -> dict:
    if not DB_WORK.acquire(timeout=60):  # pauses the background poller while files are swapped
        raise HTTPException(409, "Horus is busy, try again in a moment")
    try:
        return backup.restore_file(db_path(), path, "uploaded file")
    except backup.RestoreError as e:
        log.warning("restore of an uploaded file refused: %s", e)
        raise HTTPException(422, str(e)) from e
    finally:
        DB_WORK.release()


async def restore_upload(request: Request) -> dict:
    """The request body is the backup file itself (not a form), which needs no extra packages."""
    try:
        path = await backup.save_upload(request.stream(), db_path())
    except backup.RestoreError as e:
        raise HTTPException(413, str(e)) from e
    try:
        return await run_in_threadpool(_restore, path)
    finally:
        path.unlink(missing_ok=True)
