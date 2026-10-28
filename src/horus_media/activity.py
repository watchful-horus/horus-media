import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx

from .clients import make_client
from .models import ServerActivity

log = logging.getLogger(__name__)


def fetch_server_activity(
    row: sqlite3.Row, token: str, transport: httpx.BaseTransport | None = None
) -> ServerActivity:
    base = {"id": row["id"], "name": row["name"], "type": row["type"]}
    client = make_client(row["type"], row["name"], row["url"], token, transport=transport)
    try:
        return ServerActivity(**base, ok=True, sessions=client.get_sessions())
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        log.debug("%s: HTTP %s from %s", row["name"], code, e.request.url.path)
        msg = "Authentication failed (check token)" if code in (401, 403) else f"HTTP {code}"
        return ServerActivity(**base, ok=False, error=msg)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        log.debug("%s: %s: %s", row["name"], type(e).__name__, e)
        # Deliberately generic: exception text can contain the request URL.
        return ServerActivity(**base, ok=False, error="Could not connect or unexpected response")
    finally:
        client.close()


def fetch_all(
    entries: list[tuple[sqlite3.Row, str]], transport: httpx.BaseTransport | None = None
) -> list[ServerActivity]:
    if not entries:
        return []
    with ThreadPoolExecutor(max_workers=len(entries)) as pool:
        return list(pool.map(lambda e: fetch_server_activity(*e, transport=transport), entries))
