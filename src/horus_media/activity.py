import hashlib
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from .clients import make_client
from .models import ServerActivity

log = logging.getLogger(__name__)


def _reason(e: Exception) -> str:
    """A short reason a server could not be reached, safe to show: never the address or a token."""
    if isinstance(e, httpx.TimeoutException):
        return "timed out"
    if isinstance(e, httpx.ConnectError):
        return "connection refused or host not reachable"
    if isinstance(e, httpx.NetworkError | httpx.RemoteProtocolError):
        return "connection dropped"
    return "unexpected response" if isinstance(e, ValueError | KeyError | TypeError) else "network error"


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
        # Only a short reason, never the exception text: that can contain the request URL.
        return ServerActivity(**base, ok=False, error=f"Could not connect: {_reason(e)}")
    finally:
        client.close()


# How old a live result may be and still be handed to a page or a scrape. Several open
# browsers, the data API and a Prometheus scrape then share one call to each media server
# instead of each making their own. The poller always asks the servers itself.
LIVE_MAX_AGE = 5.0
_live_lock = threading.Lock()
_live: dict = {}  # "key": what was asked, "at": when (monotonic seconds), "results": the answer


def forget_live() -> None:
    """Drop the remembered result, so the next caller asks the servers (after stopping a
    stream, say)."""
    with _live_lock:
        _live.clear()


def fetch_all(
    entries: list[tuple[sqlite3.Row, str]], transport: httpx.BaseTransport | None = None,
    max_age: float = 0.0,
) -> list[ServerActivity]:
    """Live sessions from every server, asked in parallel. With `max_age`, a result that is at
    most that many seconds old is reused; callers get their own copy and may change it."""
    if not entries:
        return []
    if max_age <= 0:
        return _fetch(entries, transport)
    key = (id(transport), tuple((r["id"], r["type"], r["name"], r["url"], hashlib.sha256(
        token.encode()).hexdigest()) for r, token in entries))
    with _live_lock:  # held while fetching, so callers arriving together share one fetch
        fresh = _live.get("key") == key and time.monotonic() - _live["at"] <= max_age
        if not fresh:
            _live.update(key=key, results=_fetch(entries, transport), at=time.monotonic())
        return [r.model_copy(deep=True) for r in _live["results"]]


def _fetch(
    entries: list[tuple[sqlite3.Row, str]], transport: httpx.BaseTransport | None
) -> list[ServerActivity]:
    with ThreadPoolExecutor(max_workers=len(entries)) as pool:
        return list(pool.map(lambda e: fetch_server_activity(*e, transport=transport), entries))
