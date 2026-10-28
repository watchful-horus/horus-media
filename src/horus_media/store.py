from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from .crypto import TokenCipher
from .models import ServerType

PUBLIC_COLUMNS = "id, type, name, url, created_at"


class ServerStore:
    def __init__(self, conn: sqlite3.Connection, cipher: TokenCipher):
        self._conn = conn
        self._cipher = cipher

    @property
    def cipher(self) -> TokenCipher:
        return self._cipher

    @property
    def conn(self) -> sqlite3.Connection:
        return self._conn

    def _token(self, row: sqlite3.Row) -> str:
        """Blank after restoring a download without tokens: the server then answers 401 and
        shows as "Authentication failed" until the token is entered again."""
        return self._cipher.decrypt(row["token"]) if row["token"] else ""

    def add(self, type_: ServerType, name: str, url: str, token: str) -> sqlite3.Row:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO servers (type, name, url, token, created_at) VALUES (?, ?, ?, ?, ?)",
                (type_, name, url.rstrip("/"), self._cipher.encrypt(token),
                 datetime.now(UTC).isoformat()),
            )
        return self._conn.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM servers WHERE id = ?", (cur.lastrowid,)
        ).fetchone()

    def list(self) -> list[sqlite3.Row]:
        return self._conn.execute(f"SELECT {PUBLIC_COLUMNS} FROM servers ORDER BY id").fetchall()

    def get_with_token(self, id_: int) -> tuple[sqlite3.Row, str] | None:
        row = self._conn.execute("SELECT * FROM servers WHERE id = ?", (id_,)).fetchone()
        if row is None:
            return None
        return row, self._token(row)

    def rename(self, id_: int, name: str) -> sqlite3.Row | None:
        """History, graphs and metrics join on the id, so the new name shows up everywhere."""
        with self._conn:
            changed = self._conn.execute(
                "UPDATE servers SET name = ? WHERE id = ?", (name, id_)).rowcount
        if not changed:
            return None
        return self._conn.execute(
            f"SELECT {PUBLIC_COLUMNS} FROM servers WHERE id = ?", (id_,)).fetchone()

    def delete(self, id_: int) -> bool:
        with self._conn:
            return self._conn.execute("DELETE FROM servers WHERE id = ?", (id_,)).rowcount > 0

    def list_with_tokens(self) -> list[tuple[sqlite3.Row, str]]:
        rows = self._conn.execute("SELECT * FROM servers ORDER BY id").fetchall()
        return [(r, self._token(r)) for r in rows]
