"""Sign in with Plex: the plex.tv PIN flow, so users never have to dig out a token.

The account token stays on the server. The browser only ever sees a PIN id, the sign-in
URL, and the list of servers (names and addresses).
"""

import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

PLEX_TV = "https://plex.tv"
AUTH_APP = "https://app.plex.tv/auth"
PIN_TTL_SECONDS = 600
PRODUCT = "Horus"


class PlexLinkError(Exception):
    """plex.tv could not be reached or answered unexpectedly. Message is safe to show."""


def client_identifier(conn: sqlite3.Connection) -> str:
    """A random id for this Horus install, as plex.tv expects; created on first use."""
    row = conn.execute("SELECT value FROM settings WHERE key = 'plex_client_id'").fetchone()
    if row:
        return row[0]
    value = secrets.token_hex(16)
    with conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('plex_client_id', ?)", (value,))
    return value


@dataclass
class _Pending:
    code: str
    created: float
    token: str | None = None
    resources: list[dict] = field(default_factory=list)


class PlexLinker:
    def __init__(self, transport: httpx.BaseTransport | None = None, clock=time.monotonic):
        self._transport = transport
        self._clock = clock
        self._pending: dict[int, _Pending] = {}

    def _http(self, client_id: str, token: str | None = None) -> httpx.Client:
        headers = {
            "Accept": "application/json",
            "X-Plex-Product": PRODUCT,
            "X-Plex-Client-Identifier": client_id,
            "X-Plex-Device-Name": PRODUCT,
        }
        if token:
            headers["X-Plex-Token"] = token
        return httpx.Client(headers=headers, timeout=10.0, transport=self._transport)

    def _expire(self) -> None:
        now = self._clock()
        for pin_id in [i for i, p in self._pending.items() if now - p.created > PIN_TTL_SECONDS]:
            del self._pending[pin_id]

    def start(self, client_id: str) -> tuple[int, str]:
        self._expire()
        try:
            with self._http(client_id) as http:
                resp = http.post(f"{PLEX_TV}/api/v2/pins", params={"strong": "true"})
                resp.raise_for_status()
                body = resp.json()
            pin_id, code = int(body["id"]), str(body["code"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise PlexLinkError("Could not reach plex.tv") from e
        self._pending[pin_id] = _Pending(code=code, created=self._clock())
        url = (
            f"{AUTH_APP}#?clientID={quote(client_id)}&code={quote(code)}"
            f"&context%5Bdevice%5D%5Bproduct%5D={PRODUCT}"
        )
        return pin_id, url

    def poll(self, client_id: str, pin_id: int) -> tuple[str, list[dict]]:
        """Returns ("pending" | "expired" | "done", servers). Servers carry no token."""
        self._expire()
        pending = self._pending.get(pin_id)
        if pending is None:
            return "expired", []
        try:
            if pending.token is None:
                with self._http(client_id) as http:
                    resp = http.get(
                        f"{PLEX_TV}/api/v2/pins/{pin_id}", params={"code": pending.code}
                    )
                    resp.raise_for_status()
                    token = resp.json().get("authToken")
                if not token:
                    return "pending", []
                with self._http(client_id, token) as http:
                    resp = http.get(
                        f"{PLEX_TV}/api/v2/resources",
                        params={"includeHttps": 1, "includeRelay": 0},
                    )
                    resp.raise_for_status()
                    resources = resp.json()
                pending.token = token
                pending.resources = [r for r in resources if "server" in (r.get("provides") or "")]
        except (httpx.HTTPError, ValueError, TypeError) as e:
            raise PlexLinkError("Could not reach plex.tv") from e
        return "done", [_public(r) for r in pending.resources]

    def take(self, pin_id: int, resource_id: str, url: str) -> tuple[str, str]:
        """Claim a server from a finished sign-in. Returns (name, token); url must be one of
        that server's advertised addresses, so the token can only go where Plex says it lives."""
        pending = self._pending.get(pin_id)
        if pending is None or pending.token is None:
            raise KeyError("sign-in expired")
        for r in pending.resources:
            if r.get("clientIdentifier") == resource_id:
                if url not in {c["url"] for c in _public(r)["connections"]}:
                    raise ValueError("address does not belong to that server")
                pending.resources.remove(r)  # each server is claimed once
                if not pending.resources:
                    del self._pending[pin_id]
                return r.get("name") or "Plex", r.get("accessToken") or pending.token
        raise KeyError("server not found")


def _public(resource: dict) -> dict:
    conns = [
        {"url": c["uri"], "local": bool(c.get("local"))}
        for c in resource.get("connections") or []
        if c.get("uri") and not c.get("relay")
    ]
    conns.sort(key=lambda c: (not c["local"], c["url"].startswith("https")))
    return {
        "id": resource.get("clientIdentifier", ""),
        "name": resource.get("name") or "Plex",
        "owned": bool(resource.get("owned")),
        "connections": conns,
    }
