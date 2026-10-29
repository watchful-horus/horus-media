import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from ..models import HistoryRecord, Session

log = logging.getLogger(__name__)
TIMEOUT = 10.0
MAX_IMAGE_BYTES = 5_000_000


@dataclass
class ConnectionResult:
    server_name: str
    version: str | None


class MediaServer(ABC):
    """Client for a media server. It only reads, apart from stop_session, which ends one stream
    when an admin asks for it."""

    def __init__(self, name: str, url: str, token: str,
                 transport: httpx.BaseTransport | None = None):
        self.name = name
        self._http = httpx.Client(
            base_url=url.rstrip("/"), headers=self._headers(token), timeout=TIMEOUT,
            transport=transport,
        )

    @abstractmethod
    def _headers(self, token: str) -> dict[str, str]: ...

    @abstractmethod
    def test_connection(self) -> ConnectionResult: ...

    @abstractmethod
    def get_sessions(self) -> list[Session]: ...

    def stop_session(self, native_id: str, reason: str = "") -> None:
        """End one playing stream, optionally showing the viewer a message. `native_id` is the
        server's own id (Session.native_id)."""
        raise NotImplementedError

    def get_history(self) -> list[HistoryRecord]:
        """Past plays the server itself remembers. Raises NotImplementedError when it can't."""
        raise NotImplementedError

    def _get(self, path: str, params: dict | None = None) -> dict | list:
        started = time.monotonic()
        resp = self._http.get(path, params=params)
        # Path and status only: never the query string or headers, which carry the token.
        log.debug("%s GET %s -> %d (%d ms)", self.name, path, resp.status_code,
                  (time.monotonic() - started) * 1000)
        resp.raise_for_status()
        return resp.json()

    @abstractmethod
    def get_image(self, path: str) -> tuple[bytes, str]:
        """Fetch a poster for a path taken from Session.poster. Raises ValueError if the path
        is not an image path this client knows how to ask for."""

    def _get_image(self, path: str, params: dict) -> tuple[bytes, str]:
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
        if not content_type.startswith("image/") or len(resp.content) > MAX_IMAGE_BYTES:
            raise ValueError("not a usable image")
        return resp.content, content_type

    def close(self) -> None:
        self._http.close()
