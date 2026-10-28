"""Users, browser sessions and API keys.

Accounts have a username and a password and one of two roles: `admin` (everything) or `user`
(view Activity and Graphs, and change their own password). Passwords are hashed with scrypt.
Session tokens and API keys are random secrets that are shown once and only their SHA-256 is
stored, so a copy of the database does not hand out working logins.
"""

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

log = logging.getLogger(__name__)

SESSION_COOKIE = "horus_session"
SESSION_DAYS = 30
MIN_PASSWORD = 8
KEY_PREFIX = "hk_"
ROLES = ("admin", "user")
# Letters, numbers and . _ - @ + so an email address works as a username.
USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{2,63}$")
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
_KEY_TOUCH_EVERY = timedelta(minutes=1)


class UserError(ValueError):
    """A rejected user change. The message is safe to show."""


def auth_disabled() -> bool:
    """For setups that put their own login in front of Horus (e.g. a reverse proxy)."""
    return os.environ.get("HORUS_AUTH_DISABLED", "").lower() in ("1", "true", "yes")


def _now() -> datetime:
    return datetime.now(UTC)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# --- passwords ------------------------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY: str | None = None


def _dummy_hash() -> str:
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = hash_password(secrets.token_hex(8))
    return _DUMMY


def check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise UserError(f"Use at least {MIN_PASSWORD} characters")


# --- users ----------------------------------------------------------------------------------

def _public(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "username": row["username"], "role": row["role"],
            "created_at": row["created_at"], "last_login_at": row["last_login_at"],
            "media_username": row["media_username"]}


def admin_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE role = 'admin'").fetchone() is not None


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT id, username, role, created_at, last_login_at, media_username FROM users "
        "ORDER BY username"
    ).fetchall()
    return [_public(r) for r in rows]


def get_user(conn: sqlite3.Connection, user_id: int) -> dict | None:
    row = conn.execute(
        "SELECT id, username, role, created_at, last_login_at, media_username FROM users "
        "WHERE id = ?", (user_id,)
    ).fetchone()
    return _public(row) if row else None


def create_user(conn: sqlite3.Connection, username: str, password: str, role: str) -> dict:
    username = username.strip()
    if not USERNAME_RE.fullmatch(username):
        raise UserError("Usernames are 3 to 64 letters, numbers, dots, dashes, underscores, @ or +. "
                        "An email address works")
    if role not in ROLES:
        raise UserError("Role must be admin or user")
    check_new_password(password)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, hash_password(password), role, _now().isoformat()),
            )
    except sqlite3.IntegrityError:
        raise UserError("That username is already taken") from None
    return get_user(conn, cur.lastrowid)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> dict | None:
    """The user if the credentials are right. Takes the same time whether or not the username
    exists, so the response does not reveal which usernames are real."""
    row = conn.execute(
        "SELECT id, username, role, created_at, last_login_at, media_username, password_hash "
        "FROM users "
        "WHERE username = ?", (username.strip(),)
    ).fetchone()
    ok = verify_password(password, row["password_hash"] if row else _dummy_hash())
    if not (row and ok):
        return None
    touch_login(conn, row["id"])
    return get_user(conn, row["id"])


def touch_login(conn: sqlite3.Connection, user_id: int) -> None:
    with conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?",
                     (_now().isoformat(), user_id))


def check_password(conn: sqlite3.Connection, user_id: int, password: str) -> bool:
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return bool(row) and verify_password(password, row[0])


def set_user_password(conn: sqlite3.Connection, user_id: int, password: str) -> None:
    check_new_password(password)
    with conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (hash_password(password), user_id))


def set_media_username(conn: sqlite3.Connection, user_id: int, name: str) -> None:
    """Which Plex/Emby name this person is, so "hide other people's activity" knows what to show."""
    name = name.strip()
    if len(name) > 100:
        raise UserError("That name is too long")
    with conn:
        conn.execute("UPDATE users SET media_username = ? WHERE id = ?", (name, user_id))


def _other_admins(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND id != ?",
                        (user_id,)).fetchone()[0]


def set_role(conn: sqlite3.Connection, user_id: int, role: str) -> None:
    if role not in ROLES:
        raise UserError("Role must be admin or user")
    user = get_user(conn, user_id)
    if user and user["role"] == "admin" and role != "admin" and not _other_admins(conn, user_id):
        raise UserError("Keep at least one admin")
    with conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


def delete_user(conn: sqlite3.Connection, user_id: int) -> bool:
    user = get_user(conn, user_id)
    if not user:
        return False
    if user["role"] == "admin" and not _other_admins(conn, user_id):
        raise UserError("Keep at least one admin")
    with conn:  # sessions go with the user (foreign key cascade)
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return True


# --- sessions -------------------------------------------------------------------------------

def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    with conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_sha(token), user_id, now.isoformat(), (now + timedelta(days=SESSION_DAYS)).isoformat()),
        )
    return token


def session_user(conn: sqlite3.Connection, token: str | None) -> dict | None:
    """The signed-in user for a session cookie, or None."""
    if not token:
        return None
    row = conn.execute(
        "SELECT u.id, u.username, u.role, u.created_at, u.last_login_at, u.media_username, "
        "s.expires_at "
        "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?", (_sha(token),)
    ).fetchone()
    if not row or row["expires_at"] <= _now().isoformat():
        return None
    return _public(row)


def end_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        with conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(token),))


def end_user_sessions(conn: sqlite3.Connection, user_id: int, keep: str | None = None) -> None:
    """Sign a user out everywhere, optionally keeping the session making the request."""
    with conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                     (user_id, _sha(keep or "")))


# --- API keys -------------------------------------------------------------------------------

CONTROL_DAYS = (1, 7, 30, 90)  # how long a control key may last, in days
# A control key acts as its admin owner on everything except these addresses, so a leaked one
# can't create people, mint more keys, change passwords or lock the admin out.
_CONTROL_BLOCKED = re.compile(r"^/api/(users|keys|my-keys|auth)(/|$)")


def expiry_in(days: int) -> str:
    return (_now() + timedelta(days=days)).isoformat()


def create_key(conn: sqlite3.Connection, name: str, user_id: int | None = None,
               scope: str = "read", expires_at: str | None = None) -> tuple[int, str]:
    """A new API key. With an owner it can see only what that person may see; without one
    (login turned off, or a key from before accounts had keys) it sees everything. A "control"
    key can also act on Horus as its (admin) owner, and always has an expiry."""
    key = KEY_PREFIX + secrets.token_urlsafe(30)
    with conn:
        cur = conn.execute(
            "INSERT INTO api_keys (name, prefix, key_hash, created_at, user_id, scope, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, key[:7], _sha(key), _now().isoformat(), user_id, scope, expires_at),
        )
    return cur.lastrowid, key


def list_keys(conn: sqlite3.Connection, user_id: int | None = None) -> list[dict]:
    """Every key (with its owner), or only one person's."""
    rows = conn.execute(
        "SELECT k.id, k.name, k.prefix, k.created_at, k.last_used_at, k.scope, k.expires_at, "
        "u.username AS owner "
        "FROM api_keys k LEFT JOIN users u ON u.id = k.user_id "
        + ("WHERE k.user_id = ? " if user_id is not None else "") + "ORDER BY k.id",
        (user_id,) if user_id is not None else (),
    ).fetchall()
    return [dict(r) for r in rows]


def revoke_key(conn: sqlite3.Connection, key_id: int, user_id: int | None = None) -> bool:
    with conn:
        if user_id is None:
            return conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,)).rowcount > 0
        return conn.execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?",
                            (key_id, user_id)).rowcount > 0


def key_info(conn: sqlite3.Connection, key: str | None) -> dict | None:
    """The key's record (id, name, scope, owner) when the key exists and may be used right now:
    it has not expired and, for a control key, control keys are switched on. Marks it as used."""
    if not key or not key.startswith(KEY_PREFIX):
        return None
    row = conn.execute(
        "SELECT id, name, last_used_at, user_id, scope, expires_at FROM api_keys WHERE key_hash = ?",
        (_sha(key),),
    ).fetchone()
    if not row:
        return None
    now = _now()
    if row[5] and now >= datetime.fromisoformat(row[5]):
        return None  # expired
    if row[4] == "control":
        from . import app_settings  # imported here: app_settings does not need auth otherwise

        if not app_settings.load(conn).control_keys_enabled:
            return None  # an admin switched control keys off
    if not row[2] or now - datetime.fromisoformat(row[2]) > _KEY_TOUCH_EVERY:
        with conn:  # "last used", at most once a minute so a busy dashboard does not write nonstop
            conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?",
                         (now.isoformat(), row[0]))
    owner = get_user(conn, row[3]) if row[3] is not None else None
    return {"id": row[0], "name": row[1], "scope": row[4], "owner": owner}


def control_refusal(info: dict, path: str) -> str | None:
    """Why this key may not call this address as a control key, or None when it may."""
    if info["scope"] != "control":
        return "This key can only read the data endpoints (/api/v1)"
    owner = info["owner"]
    if owner is None or owner["role"] != "admin":
        return "A control key needs an admin account as its owner"
    if _CONTROL_BLOCKED.match(path):
        return "A control key can't manage users or keys, or sign in"
    return None


def check_key(conn: sqlite3.Connection, key: str | None) -> tuple[bool, dict | None]:
    """(valid, owner). The owner is None for a key that belongs to nobody in particular."""
    info = key_info(conn, key)
    return (False, None) if info is None else (True, info["owner"])


def key_valid(conn: sqlite3.Connection, key: str | None) -> bool:
    return check_key(conn, key)[0]


def only_media_user(conn: sqlite3.Connection, viewer: dict | None) -> str | None:
    """None when this viewer may see everyone's plays. Otherwise the Plex/Emby name they may
    see ("" when no name is linked to their account, which means they see nothing)."""
    from . import app_settings  # imported here: app_settings does not need auth otherwise

    if not viewer or viewer["role"] == "admin" or not app_settings.load(conn).hide_others_activity:
        return None
    return (viewer.get("media_username") or "").strip()


def credential_from_headers(headers) -> str | None:
    value = headers.get("authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return headers.get("x-api-key") or None


# --- what each role may do ------------------------------------------------------------------

_USER_PATHS = {"/api/activity", "/api/stats"}
_POSTER = re.compile(r"/api/servers/\d+/poster")
_MY_KEY = re.compile(r"/api/my-keys/\d+")


def role_allows(role: str, method: str, path: str) -> bool:
    """Admins may do anything. A normal user is limited to a short allow-list (everything else,
    including any endpoint added later, needs an admin)."""
    if role == "admin":
        return True
    if method == "GET" and (path in _USER_PATHS or _POSTER.fullmatch(path)):
        return True
    if path == "/api/my-keys" and method in ("GET", "POST"):
        return True
    if method == "DELETE" and _MY_KEY.fullmatch(path):
        return True
    return method == "POST" and path == "/api/auth/password"


# --- brute-force protection ------------------------------------------------------------------

class LoginLimiter:
    """At most `limit` failed attempts per client in `window` seconds."""

    def __init__(self, limit: int = 5, window: float = 60.0, clock=time.monotonic):
        self._limit, self._window, self._clock = limit, window, clock
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _trim(self, who: str) -> deque[float]:
        q = self._failures[who]
        while q and self._clock() - q[0] > self._window:
            q.popleft()
        return q

    def blocked(self, who: str) -> bool:
        return len(self._trim(who)) >= self._limit

    def fail(self, who: str) -> None:
        self._trim(who).append(self._clock())

    def reset(self, who: str) -> None:
        self._failures.pop(who, None)
