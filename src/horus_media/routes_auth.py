"""Login, first-run setup, user accounts and API-key management."""

import logging
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import app_settings, auth
from .config import db_path
from .db import connect, migrate
from .restore_upload import restore_upload
from .store import ServerStore

log = logging.getLogger(__name__)

Role = Literal["admin", "user"]


class Login(BaseModel):
    username: str
    password: str


class Setup(BaseModel):
    username: str
    password: str


class ChangePassword(BaseModel):
    current: str
    new: str


class NewUser(BaseModel):
    username: str
    password: str
    role: Role = "user"
    media_username: str = ""


class UserChange(BaseModel):
    role: Role | None = None
    password: str | None = None
    media_username: str | None = None
    username: str | None = None
    display_name: str | None = None


class MyDetails(BaseModel):
    username: str | None = None
    display_name: str | None = None
    media_username: str | None = None  # admins only: it decides what a limited person may see


class KeyName(BaseModel):
    name: str
    scope: Literal["read", "control"] = "read"
    expires_in_days: int | None = None  # required for a control key


def make_router(get_store: Callable) -> APIRouter:
    router = APIRouter(prefix="/api")
    Store = Annotated[ServerStore, Depends(get_store)]
    limiter = auth.LoginLimiter()

    def client(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def start_session(response: Response, request: Request, store: ServerStore, user_id: int) -> None:
        token = auth.create_session(store.conn, user_id)
        response.set_cookie(
            auth.SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True,
            samesite="lax", secure=request.url.scheme == "https",
        )

    def me(request: Request) -> dict:
        """The signed-in user (set by the login gate). None of the account routes make sense
        when login is turned off."""
        user = getattr(request.state, "user", None)
        if auth.auth_disabled():
            raise HTTPException(409, "Login is turned off (HORUS_AUTH_DISABLED)")
        if not user:
            raise HTTPException(401, "Login required")
        return user

    def admin(request: Request) -> dict:
        user = me(request)
        if user["role"] != "admin":
            raise HTTPException(403, "Admin only")
        return user

    def refuse(error: auth.UserError, code: int = 422) -> HTTPException:
        return HTTPException(code, str(error))

    # --- status, first-run setup, login ---

    @router.get("/auth/status")
    def status(request: Request, store: Store):
        exists = auth.admin_exists(store.conn)
        user = auth.session_user(store.conn, request.cookies.get(auth.SESSION_COOKIE))
        return {
            "auth_disabled": auth.auth_disabled(),
            "setup_required": not exists and not auth.auth_disabled(),
            "authenticated": auth.auth_disabled() or user is not None,
            "user": user,
        }

    @router.post("/auth/setup")
    def setup(body: Setup, request: Request, response: Response, store: Store):
        if auth.admin_exists(store.conn):
            raise HTTPException(409, "Horus is already set up")
        try:
            user = auth.create_user(store.conn, body.username, body.password, "admin")
        except auth.UserError as e:
            raise refuse(e) from e
        auth.touch_login(store.conn, user["id"])
        start_session(response, request, store, user["id"])
        log.info("first admin account created: %s", user["username"])
        return {"ok": True, "user": auth.get_user(store.conn, user["id"])}

    @router.post("/auth/restore")
    async def restore_at_setup(request: Request):
        """First run only: start from a backup instead of creating an account. The body is the
        backup file. It stops working as soon as an admin exists."""
        conn = connect(db_path())
        try:
            migrate(conn)
            taken = auth.admin_exists(conn)
        finally:
            conn.close()
        if taken or auth.auth_disabled():
            raise HTTPException(409, "Horus is already set up")
        return await restore_upload(request)

    @router.post("/auth/login")
    def login(body: Login, request: Request, response: Response, store: Store):
        who = client(request)
        if limiter.blocked(who):
            raise HTTPException(429, "Too many attempts, wait a minute")
        user = auth.authenticate(store.conn, body.username, body.password)
        if not user:
            limiter.fail(who)
            # The username typed is deliberately not logged: people mistype passwords into it.
            log.warning("failed login from %s", who)
            raise HTTPException(401, "Wrong username or password")
        limiter.reset(who)
        start_session(response, request, store, user["id"])
        log.info("login: %s from %s", user["username"], who)
        return {"ok": True, "user": user}

    @router.post("/auth/logout")
    def logout(request: Request, response: Response, store: Store):
        auth.end_session(store.conn, request.cookies.get(auth.SESSION_COOKIE))
        response.delete_cookie(auth.SESSION_COOKIE)
        return {"ok": True}

    @router.post("/auth/password")
    def change_password(body: ChangePassword, request: Request, store: Store):
        user = me(request)
        if not auth.check_password(store.conn, user["id"], body.current):
            raise HTTPException(403, "Current password is wrong")
        try:
            auth.set_user_password(store.conn, user["id"], body.new)
        except auth.UserError as e:
            raise refuse(e) from e
        auth.end_user_sessions(store.conn, user["id"], request.cookies.get(auth.SESSION_COOKIE))
        log.info("password changed: %s; their other sessions were signed out", user["username"])
        return {"ok": True}

    @router.patch("/auth/me")
    def change_my_details(body: MyDetails, request: Request, store: Store):
        """Your own username and display name. A normal user may not set their Plex/Emby name:
        with "see only their own activity" on, it decides whose plays they can see."""
        user = me(request)
        if body.media_username is not None and user["role"] != "admin":
            raise HTTPException(403, "Only an admin can set the Plex/Emby name")
        try:
            if body.username is not None and body.username.strip() != user["username"]:
                auth.set_username(store.conn, user["id"], body.username)
                log.info("username changed: %s is now %s", user["username"], body.username.strip())
            if body.display_name is not None:
                auth.set_display_name(store.conn, user["id"], body.display_name)
            if body.media_username is not None:
                auth.set_media_username(store.conn, user["id"], body.media_username)
        except auth.UserError as e:
            raise refuse(e, 409 if "taken" in str(e) else 422) from e
        return auth.get_user(store.conn, user["id"])

    # --- user accounts (admins only) ---

    @router.get("/users")
    def list_users(request: Request, store: Store):
        admin(request)
        return auth.list_users(store.conn)

    @router.post("/users", status_code=201)
    def create_user(body: NewUser, request: Request, store: Store):
        actor = admin(request)
        try:
            user = auth.create_user(store.conn, body.username, body.password, body.role)
        except auth.UserError as e:
            raise refuse(e, 409 if "taken" in str(e) else 422) from e
        if body.media_username.strip():
            auth.set_media_username(store.conn, user["id"], body.media_username)
            user = auth.get_user(store.conn, user["id"])
        log.info("user created: %s (%s) by %s", user["username"], user["role"], actor["username"])
        return user

    @router.patch("/users/{user_id}")
    def change_user(user_id: int, body: UserChange, request: Request, store: Store):
        actor = admin(request)
        target = auth.get_user(store.conn, user_id)
        if not target:
            raise HTTPException(404, "User not found")
        try:
            if body.role is not None and body.role != target["role"]:
                if user_id == actor["id"]:
                    raise HTTPException(409, "You can't change your own role; ask another admin")
                auth.set_role(store.conn, user_id, body.role)
                log.info("role changed: %s is now %s (by %s)", target["username"], body.role,
                         actor["username"])
            if body.username is not None and body.username.strip() != target["username"]:
                auth.set_username(store.conn, user_id, body.username)
                log.info("username changed: %s is now %s (by %s)", target["username"],
                         body.username.strip(), actor["username"])
            if body.display_name is not None:
                auth.set_display_name(store.conn, user_id, body.display_name)
            if body.media_username is not None:
                auth.set_media_username(store.conn, user_id, body.media_username)
                log.info("media name linked: %s (by %s)", target["username"], actor["username"])
            if body.password is not None:
                auth.set_user_password(store.conn, user_id, body.password)
                # A reset signs that person out everywhere (including you, if it is your own).
                auth.end_user_sessions(
                    store.conn, user_id,
                    request.cookies.get(auth.SESSION_COOKIE) if user_id == actor["id"] else None)
                log.info("password reset: %s (by %s)", target["username"], actor["username"])
        except auth.UserError as e:
            raise refuse(e, 409 if ("admin" in str(e) or "taken" in str(e)) else 422) from e
        return auth.get_user(store.conn, user_id)

    @router.delete("/users/{user_id}", status_code=204)
    def delete_user(user_id: int, request: Request, store: Store):
        actor = admin(request)
        if user_id == actor["id"]:
            raise HTTPException(409, "You can't delete your own account")
        target = auth.get_user(store.conn, user_id)
        try:
            if not auth.delete_user(store.conn, user_id):
                raise HTTPException(404, "User not found")
        except auth.UserError as e:
            raise refuse(e, 409) from e
        log.info("user deleted: %s (by %s)", target["username"], actor["username"])
        return Response(status_code=204)

    # --- API keys ---

    @router.get("/keys")
    def list_keys(store: Store):
        return auth.list_keys(store.conn)

    def new_key(store: ServerStore, body: KeyName, owner: dict | None) -> dict:
        """Make a key for `owner`. A control key also needs an admin owner, the Settings switch
        turned on, and an expiry."""
        name = body.name.strip()
        if not name or len(name) > 60:
            raise HTTPException(422, "Give the key a short name, such as Grafana")
        expires_at = None
        if body.scope == "control":
            if owner is None:
                raise HTTPException(409, "Control keys need a signed-in admin account")
            if owner["role"] != "admin":
                raise HTTPException(403, "Only an admin can create a control key")
            if not app_settings.load(store.conn).control_keys_enabled:
                raise HTTPException(409, "Control keys are switched off. Turn them on first")
            if body.expires_in_days not in auth.CONTROL_DAYS:
                days = ", ".join(str(d) for d in auth.CONTROL_DAYS)
                raise HTTPException(422, f"Choose how many days it lasts: {days}")
            expires_at = auth.expiry_in(body.expires_in_days)
        key_id, key = auth.create_key(store.conn, name, owner["id"] if owner else None,
                                      body.scope, expires_at)
        log.info("API key created: %s (%s) for %s (%s...)%s", name, body.scope,
                 owner["username"] if owner else "nobody in particular", key[:7],
                 f", expires {expires_at[:10]}" if expires_at else "")
        # The only time the full key is ever shown.
        return {"id": key_id, "name": name, "key": key, "scope": body.scope, "expires_at": expires_at}

    @router.post("/keys", status_code=201)
    def create_key(body: KeyName, request: Request, store: Store):
        return new_key(store, body, getattr(request.state, "user", None))

    @router.delete("/keys/{key_id}", status_code=204)
    def revoke_key(key_id: int, store: Store):
        if not auth.revoke_key(store.conn, key_id):
            raise HTTPException(404, "Key not found")
        log.info("API key revoked: id %d", key_id)
        return Response(status_code=204)

    # --- your own keys (any signed-in person; they see only what the account may see) ---

    @router.get("/my-keys")
    def my_keys(request: Request, store: Store):
        return auth.list_keys(store.conn, me(request)["id"])

    @router.post("/my-keys", status_code=201)
    def create_my_key(body: KeyName, request: Request, store: Store):
        return new_key(store, body, me(request))

    @router.delete("/my-keys/{key_id}", status_code=204)
    def revoke_my_key(key_id: int, request: Request, store: Store):
        if not auth.revoke_key(store.conn, key_id, me(request)["id"]):
            raise HTTPException(404, "Key not found")
        return Response(status_code=204)

    return router
