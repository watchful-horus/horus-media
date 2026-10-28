import logging
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, app_settings, auth, listen, logging_setup
from .activity import fetch_all
from .clients import make_client
from .config import db_path
from .crypto import TokenCipher
from .db import connect, migrate
from .history_import import HistoryImportError, import_history
from .metrics import make_router as make_metrics_router
from .models import (
    Activity,
    PlexLinkAdd,
    PlexLinkStart,
    PlexLinkStatus,
    ServerIn,
    ServerOut,
    ServerRenameIn,
    ServerTestIn,
    ServerType,
    TestResult,
)
from .plex_auth import PlexLinker, PlexLinkError, client_identifier
from .poller import run_forever
from .routes_auth import make_router as make_auth_router
from .routes_integrations import make_router as make_integrations_router
from .routes_settings import make_router
from .routes_v1 import make_router as make_v1_router
from .stats import HOURLY_MAX_DAYS, compute_stats
from .store import ServerStore

STATIC_DIR = Path(__file__).parent / "static"
# Reachable without logging in: what the login screen itself needs.
PUBLIC_API = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup",
              "/api/auth/logout", "/api/auth/restore"}

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging_setup.configure(db_path())
    log.info("Horus %s starting (data folder: %s)", __version__, db_path().resolve().parent)
    conn = connect(db_path())
    try:
        migrate(conn)
        first_run = not auth.admin_exists(conn)
        listen.remember_startup(app_settings.load(conn))
    finally:
        conn.close()
    if auth.auth_disabled():
        log.warning("Login is DISABLED (HORUS_AUTH_DISABLED): anyone who can reach Horus can use it")
    elif first_run:
        log.warning("First-run setup: no account exists yet. Open Horus and create the admin account now: "
                    "until you do, anyone who can reach this page could create it")
    stop = threading.Event()
    thread = threading.Thread(target=run_forever, args=(stop,), daemon=True, name="poller")
    thread.start()
    yield
    log.info("Horus stopping")
    stop.set()
    thread.join(timeout=5)
    logging_setup.shutdown()


def create_app(transport: httpx.BaseTransport | None = None) -> FastAPI:
    """`transport` lets tests inject fake Plex/Emby responses."""
    app = FastAPI(title="Horus", version=__version__, lifespan=lifespan)

    def get_store() -> Iterator[ServerStore]:
        path = db_path()
        conn = connect(path)
        try:
            migrate(conn)
            yield ServerStore(conn, TokenCipher.for_db(path))
        finally:
            conn.close()

    def run_test(type_: ServerType, name: str, url: str, token: str) -> TestResult:
        client = make_client(type_, name, url, token, transport=transport)
        try:
            result = client.test_connection()
            log.info("connection test OK: %s %s (v%s)", type_, name, result.version)
            return TestResult(ok=True, server_name=result.server_name, version=result.version)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            msg = "Authentication failed (check token)" if code in (401, 403) else f"HTTP {code}"
            log.warning("connection test failed: %s %s: %s", type_, name, msg)
            return TestResult(ok=False, error=msg)
        except (httpx.HTTPError, ValueError, KeyError) as e:
            log.warning("connection test failed: %s %s: %s: %s", type_, name, type(e).__name__, e)
            # The message shown to the user stays generic.
            return TestResult(ok=False, error="Could not connect or unexpected response")
        finally:
            client.close()

    linker = PlexLinker(transport=transport)

    Store = Annotated[ServerStore, Depends(get_store)]

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        gated = path.startswith("/api/") or path == "/metrics"
        if auth.auth_disabled() or not gated or path in PUBLIC_API:
            return await call_next(request)
        conn = connect(db_path())
        via_key = refusal = None
        try:
            migrate(conn)
            user = auth.session_user(conn, request.cookies.get(auth.SESSION_COOKIE))
            if path == "/metrics":  # for Prometheus: an admin, or a key that isn't a limited person's
                allowed = bool(user) and user["role"] == "admin"
            else:
                allowed = bool(user) and auth.role_allows(user["role"], request.method, path)
            if not allowed and (path.startswith("/api/v1/") or path == "/metrics"):
                valid, owner = auth.check_key(conn, auth.credential_from_headers(request.headers))
                allowed = valid and (path != "/metrics" or owner is None or owner["role"] == "admin")
                if allowed and owner:
                    user = owner  # a person's key acts as that person
            elif not allowed and not user and path.startswith("/api/"):
                # A control key (switched on in Settings, with an admin owner) acts as that admin,
                # except on users, keys and sign-in.
                info = auth.key_info(conn, auth.credential_from_headers(request.headers))
                if info and info["scope"] == "control":  # a plain read key just gets "Login required"
                    refusal = auth.control_refusal(info, path)
                    if refusal:
                        log.warning("API key %s refused on %s %s: %s", info["name"], request.method,
                                    path, refusal)
                    else:
                        user, allowed, via_key = info["owner"], True, info["name"]
        finally:
            conn.close()
        if refusal:
            return JSONResponse({"detail": refusal}, status_code=403)
        if not allowed:
            if user:  # signed in, but this needs an admin
                return JSONResponse({"detail": "Admin only"}, status_code=403)
            return JSONResponse({"detail": "Login required"}, status_code=401)
        request.state.user = user
        if via_key:
            request.state.via_key = via_key
            response = await call_next(request)
            log.info("control key %s (%s): %s %s -> %d", via_key, user["username"], request.method,
                     path, response.status_code)
            return response
        return await call_next(request)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """At DEBUG level only: one line per API call (method, path, status, time). No query
        string, so no tokens or search text. The log viewer's own polling is left out."""
        path = request.url.path
        if not log.isEnabledFor(logging.DEBUG) or not path.startswith("/api/") \
                or path == "/api/logs":
            return await call_next(request)
        started = time.monotonic()
        response = await call_next(request)
        log.debug("request: %s %s -> %d (%d ms)", request.method, path, response.status_code,
                  (time.monotonic() - started) * 1000)
        return response

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/api/servers", response_model=ServerOut, status_code=201)
    def add_server(body: ServerIn, store: Store):
        added = dict(store.add(body.type, body.name, body.url, body.token))
        log.info("server added: %s %s (%s)", body.type, body.name, added["url"])
        return added

    @app.get("/api/servers", response_model=list[ServerOut])
    def list_servers(store: Store):
        return [dict(r) for r in store.list()]

    @app.patch("/api/servers/{server_id}", response_model=ServerOut)
    def rename_server(server_id: int, body: ServerRenameIn, store: Store):
        renamed = store.rename(server_id, body.name)
        if renamed is None:
            raise HTTPException(404, "Server not found")
        log.info("server renamed: id %d is now %s", server_id, body.name)
        return dict(renamed)

    @app.delete("/api/servers/{server_id}", status_code=204)
    def delete_server(server_id: int, store: Store):
        if not store.delete(server_id):
            raise HTTPException(404, "Server not found")
        log.info("server removed: id %d", server_id)
        return Response(status_code=204)

    # Registered before /{server_id}/test so "test" is never parsed as an id.
    @app.post("/api/servers/test", response_model=TestResult)
    def test_unsaved(body: ServerTestIn):
        return run_test(body.type, body.url, body.url, body.token)

    @app.post("/api/servers/{server_id}/test", response_model=TestResult)
    def test_saved(server_id: int, store: Store):
        found = store.get_with_token(server_id)
        if found is None:
            raise HTTPException(404, "Server not found")
        row, token = found
        return run_test(row["type"], row["name"], row["url"], token)

    @app.post("/api/servers/{server_id}/import-history")
    def import_server_history(server_id: int, store: Store):
        """Bring this server's own past plays into Horus (safe to repeat)."""
        try:
            result = import_history(store, server_id, transport)
        except KeyError:
            raise HTTPException(404, "Server not found") from None
        except HistoryImportError as e:
            raise HTTPException(422, str(e)) from None
        return result

    @app.get("/api/activity", response_model=Activity)
    def activity(request: Request, store: Store):
        servers = fetch_all(store.list_with_tokens(), transport)
        only = auth.only_media_user(store.conn, getattr(request.state, "user", None))
        if only is not None:  # "hide other people's activity" is on for this person
            for s in servers:
                s.sessions = [x for x in s.sessions if x.user.casefold() == only.casefold()]
        return Activity(servers=servers, sessions=[x for s in servers for x in s.sessions])

    @app.post("/api/plex/link", response_model=PlexLinkStart)
    def plex_link_start(store: Store):
        try:
            pin_id, url = linker.start(client_identifier(store.conn))
        except PlexLinkError as e:
            log.warning("Plex sign-in could not start: %s", e)
            raise HTTPException(502, str(e)) from e
        log.info("Plex sign-in started")
        return PlexLinkStart(pin_id=pin_id, auth_url=url)

    @app.get("/api/plex/link/{pin_id}", response_model=PlexLinkStatus)
    def plex_link_status(pin_id: int, store: Store):
        try:
            status, servers = linker.poll(client_identifier(store.conn), pin_id)
        except PlexLinkError as e:
            raise HTTPException(502, str(e)) from e
        return PlexLinkStatus(status=status, servers=servers)

    @app.post("/api/plex/link/{pin_id}/add", response_model=ServerOut, status_code=201)
    def plex_link_add(pin_id: int, body: PlexLinkAdd, store: Store):
        try:
            name, token = linker.take(pin_id, body.resource_id, body.url)
        except KeyError as e:
            raise HTTPException(404, "Sign-in expired or server not found") from e
        except ValueError as e:
            raise HTTPException(422, "Address does not belong to that server") from e
        added = dict(store.add("plex", name, body.url, token))
        log.info("server added via Plex sign-in: %s (%s)", name, added["url"])
        return added

    @app.get("/api/servers/{server_id}/poster")
    def poster(server_id: int, path: str, store: Store):
        """Poster for a playing item. Proxied so the server token never reaches the browser."""
        found = store.get_with_token(server_id)
        if found is None:
            raise HTTPException(404, "Server not found")
        row, token = found
        client = make_client(row["type"], row["name"], row["url"], token, transport=transport)
        try:
            content, content_type = client.get_image(path)
        except (ValueError, httpx.HTTPError) as e:
            log.debug("poster not available for %s: %s", row["name"], type(e).__name__)
            raise HTTPException(404, "Poster not available") from e
        finally:
            client.close()
        return Response(content, media_type=content_type,
                        headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/stats")
    def stats(request: Request, store: Store, start: date | None = None,
              end: date | None = None, hourly: bool = False):
        end = end or date.today()
        start = start or end - timedelta(days=29)
        if start > end or (end - start).days > 730:
            raise HTTPException(422, "Invalid date range")
        if hourly and (end - start).days + 1 > HOURLY_MAX_DAYS:
            raise HTTPException(422, f"Hour view needs a range of {HOURLY_MAX_DAYS} days or less")
        only = auth.only_media_user(store.conn, getattr(request.state, "user", None))
        return compute_stats(store.conn, start, end, app_settings.load(store.conn).zone(), hourly,
                             only_user=only)

    app.include_router(make_router(get_store, transport))
    app.include_router(make_auth_router(get_store))
    app.include_router(make_v1_router(get_store, transport))
    app.include_router(make_metrics_router(get_store, transport))
    app.include_router(make_integrations_router(get_store, transport))

    # Mounted last so it never shadows /api routes. Absent until the web UI is built.
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    return app


app = create_app()
