"""Optional import of past plays from a server's own history into Horus's history."""

import logging
import sqlite3
from datetime import timedelta

import httpx

from .clients import make_client
from .models import HistoryRecord
from .store import ServerStore

log = logging.getLogger(__name__)


class HistoryImportError(Exception):
    """The import could not run. The message is safe to show."""


def import_history(store: ServerStore, server_id: int,
                   transport: httpx.BaseTransport | None = None) -> dict:
    """Read the server's past plays and add the ones Horus does not have yet.

    Safe to repeat: each play has a stable key, and a play Horus already recorded live (same
    user and title, started within two minutes) is skipped rather than counted twice.
    """
    found = store.get_with_token(server_id)
    if found is None:
        raise KeyError(server_id)
    row, token = found
    client = make_client(row["type"], row["name"], row["url"], token, transport=transport)
    try:
        records = client.get_history()
    except NotImplementedError:
        raise HistoryImportError("This server type can't export its history") from None
    except LookupError as e:
        cause = e.__cause__
        status = getattr(getattr(cause, "response", None), "status_code", "?")
        log.warning("history import for %s: Playback Reporting not available (HTTP %s)",
                    row["name"], status)
        raise HistoryImportError(
            "Emby has no Playback Reporting plugin to read history from. In Emby open "
            "Dashboard > Plugins > Catalogue, install Playback Reporting, restart Emby and try "
            "again. Horus must also use an administrator's API key. The plugin only has plays "
            "from after it was installed.") from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.warning("history import failed for %s: %s", row["name"], type(e).__name__)
        raise HistoryImportError("Could not read the history from that server") from None
    finally:
        client.close()
    return store_records(store.conn, server_id, records)


def store_records(conn: sqlite3.Connection, server_id: int, records: list[HistoryRecord]) -> dict:
    added = skipped = 0
    with conn:
        for r in records:
            lo = (r.started_at - timedelta(minutes=2)).isoformat()
            hi = (r.started_at + timedelta(minutes=2)).isoformat()
            live = conn.execute(
                "SELECT 1 FROM history WHERE server_id = ? AND user = ? AND title = ? "
                "AND started_at BETWEEN ? AND ? AND imported = 0",
                (server_id, r.user, r.title, lo, hi),
            ).fetchone()
            if live:
                skipped += 1
                continue
            end = (r.started_at + timedelta(seconds=r.duration_seconds)).isoformat()
            cur = conn.execute(
                "INSERT OR IGNORE INTO history (server_id, session_key, user, title, media_type, "
                "device, platform, play_method, started_at, last_seen_at, ended_at, imported) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (server_id, f"import:{r.key}", r.user, r.title, r.media_type, r.device,
                 r.platform, r.play_method, r.started_at.isoformat(), end, end),
            )
            if cur.rowcount:
                added += 1
            else:
                skipped += 1
    log.info("history import for server %d: %d added, %d already there", server_id, added, skipped)
    return {"found": len(records), "added": added, "skipped": skipped}
