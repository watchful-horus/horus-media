"""Read-only view of Seerr (Overseerr / Jellyseerr) media requests.

Only GET requests are issued. The Seerr API key is encrypted at rest and never returned.
"""

import logging
import sqlite3

import httpx

from .crypto import TokenCipher

log = logging.getLogger(__name__)
TIMEOUT = 10.0
MAX_REQUESTS = 30

# Seerr's own numbering: request status, and media availability.
_REQUEST = {1: "Pending", 2: "Approved", 3: "Declined", 4: "Failed", 5: "Completed"}
_MEDIA = {4: "Partly available", 5: "Available"}


class SeerrError(Exception):
    """Safe to show."""


def get_config(conn: sqlite3.Connection, cipher: TokenCipher) -> tuple[str, str] | None:
    row = conn.execute("SELECT url, api_key FROM seerr WHERE id = 1").fetchone()
    return (row["url"], cipher.decrypt(row["api_key"])) if row else None


def public(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT url FROM seerr WHERE id = 1").fetchone()
    return {"configured": row is not None, "url": row["url"] if row else ""}


def save_config(conn: sqlite3.Connection, cipher: TokenCipher, url: str, api_key: str | None) -> None:
    url = url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise SeerrError("The address must start with http:// or https://")
    existing = get_config(conn, cipher)
    key = (api_key or "").strip() or (existing[1] if existing else "")
    if not key:
        raise SeerrError("Enter the Seerr API key")
    with conn:
        conn.execute(
            "INSERT INTO seerr (id, url, api_key) VALUES (1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET url = excluded.url, api_key = excluded.api_key",
            (url, cipher.encrypt(key)))


def clear_config(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM seerr")


class SeerrClient:
    def __init__(self, url: str, api_key: str, transport: httpx.BaseTransport | None = None):
        self._http = httpx.Client(base_url=url.rstrip("/"), headers={"X-Api-Key": api_key},
                                  timeout=TIMEOUT, transport=transport)
        self._titles: dict[tuple[str, int], str] = {}

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, params: dict | None = None):
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    def status(self) -> str | None:
        return self._get("/api/v1/status").get("version")

    def _title(self, kind: str, tmdb_id: int) -> str:
        key = (kind, tmdb_id)
        if key not in self._titles:
            try:
                data = self._get(f"/api/v1/{'tv' if kind == 'tv' else 'movie'}/{tmdb_id}")
                self._titles[key] = data.get("title") or data.get("name") or f"TMDB {tmdb_id}"
            except httpx.HTTPError:
                self._titles[key] = f"TMDB {tmdb_id}"
        return self._titles[key]

    def requests(self, take: int = 20) -> list[dict]:
        data = self._get("/api/v1/request", {"take": min(take, MAX_REQUESTS), "skip": 0,
                                             "sort": "added"})
        rows = []
        for r in data.get("results", []):
            media = r.get("media") or {}
            kind = r.get("type") or media.get("mediaType") or "movie"
            who = r.get("requestedBy") or {}
            state = _MEDIA.get(media.get("status")) or _REQUEST.get(r.get("status"), "Unknown")
            rows.append({
                "id": r.get("id"),
                "title": self._title(kind, media["tmdbId"]) if media.get("tmdbId") else "Unknown",
                "type": kind,
                "status": state,
                "requested_by": (who.get("displayName") or who.get("username")
                                 or who.get("plexUsername") or who.get("jellyfinUsername")
                                 or "Unknown"),
                "requested_at": r.get("createdAt"),
            })
        return rows

