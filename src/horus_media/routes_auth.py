"""Login, first-run setup, user accounts and API-key management."""

import logging
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from . import auth
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


class KeyName(BaseModel):
    name: str


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
            raise refuse(e, 409 if "admin" in str(e) else 422) from e
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

    @router.post("/keys", status_code=201)
    def create_key(body: KeyName, request: Request, store: Store):
        name = body.name.strip()
        if not name or len(name) > 60:
            raise HTTPException(422, "Give the key a short name, such as Grafana")
        owner = getattr(request.state, "user", None)
        key_id, key = auth.create_key(store.conn, name, owner["id"] if owner else None)
        log.info("API key created: %s (%s...)", name, key[:7])
        # The only time the full key is ever shown.
        return {"id": key_id, "name": name, "key": key}

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
        user = me(request)
        name = body.name.strip()
        if not name or len(name) > 60:
            raise HTTPException(422, "Give the key a short name, such as Grafana")
        key_id, key = auth.create_key(store.conn, name, user["id"])
        log.info("API key created: %s for %s (%s...)", name, user["username"], key[:7])
        return {"id": key_id, "name": name, "key": key}

    @router.delete("/my-keys/{key_id}", status_code=204)
    def revoke_my_key(key_id: int, request: Request, store: Store):
        if not auth.revoke_key(store.conn, key_id, me(request)["id"]):
            raise HTTPException(404, "Key not found")
        return Response(status_code=204)

    return router
