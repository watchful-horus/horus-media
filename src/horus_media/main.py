import logging
import re
import threading
import time
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, app_settings, auth, backup, listen, logging_setup, problems
from . import activity as live
from .activity import fetch_all
from .clients import make_client
from .config import db_path
from .crypto import TokenCipher
from .db import connect, migrate, owner_only
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
    StopIn,
    TestResult,
)
from .plex_auth import PlexLinker, PlexLinkError, client_identifier
from .poller import run_forever
from .routes_auth import make_router as make_auth_router
from .routes_integrations import make_router as make_integrations_router
from .routes_settings import make_router
from .routes_v1 import make_router as make_v1_router
from .stats import HOURLY_MAX_DAYS, compute_monthly, compute_stats
from .store import ServerStore

STATIC_DIR = Path(__file__).parent / "static"
# Reachable without logging in: what the login screen itself needs.
PUBLIC_API = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup",
              "/api/auth/logout", "/api/auth/restore"}

# Requests that change something. A browser sends these with the session cookie attached, so
# they are only accepted from Horus's own pages (see `same_origin_only`).
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# These two take a backup file as the body; every other body is JSON.
UPLOAD_PATHS = {"/api/backups/upload", "/api/auth/restore"}
_HOST = re.compile(r"^(\[[0-9A-Fa-f:.]+\]|[A-Za-z0-9.-]+)$")

log = logging.getLogger(__name__)


def from_another_origin(headers, scheme: str = "http") -> bool:
    """True when a browser says this request was sent by a page that is not Horus itself.

    `Sec-Fetch-Site` is the browser's own verdict and is not affected by a reverse proxy, but
    browsers send it only over https or to localhost. Otherwise the `Origin` header (sent with
    every request that changes something) must name the scheme, host and port the request was
    sent to. `scheme` is how the request reached Horus; a proxy's `X-Forwarded-Proto` is
    believed for this comparison only, which is safe because a page on another site cannot
    add that header without the browser asking Horus first.
    A request with neither header does not come from a web page (curl, scripts, API keys)."""
    site = headers.get("sec-fetch-site")
    if site:
        return site not in ("same-origin", "none")
    origin = headers.get("origin")
    if origin is None:
        return False
    sent_from = urlsplit(origin)
    scheme = headers.get("x-forwarded-proto", scheme).split(",")[0].strip().lower()
    return (sent_from.scheme.lower() != scheme
            or sent_from.netloc.lower() != headers.get("host", "").lower())


def content_security_policy(headers) -> str:
    """Scripts, styles, images and requests only from Horus itself; no framing by anyone.

    Inline styles stay allowed because the charts position their tooltips with them. After a
    restart on a new port the page looks for Horus on the same host at that port, so requests
    to this host on any port are allowed too."""
    host = headers.get("host", "")
    if not host.endswith("]"):  # drop the port, but not the end of a bare IPv6 address
        host = host.rsplit(":", 1)[0]
    # The Host header is whatever the client sent, so it goes in only if it is a plain name.
    same_host = f" http://{host}:* https://{host}:*" if _HOST.fullmatch(host) else ""
    return ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self' data:; "
            f"connect-src 'self'{same_host}; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging_setup.configure(db_path())
    log.info("Horus %s starting (data folder: %s)", __version__, db_path().resolve().parent)
    backup.finish_restore(db_path())
    conn = connect(db_path())
    try:
        migrate(conn)
        owner_only(db_path())
        auth.admin_from_environment(conn)
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
    # No generated API pages: they would be open without a login and load scripts from a CDN.
    # The API is described in docs/api.md.
    app = FastAPI(title="Horus", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

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

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        """Changes are accepted only from Horus's own pages, and only as JSON (or a backup file
        on the two upload addresses). Without this, a page on another site, or another web app
        on the same host, could use the cookie of an admin who has Horus open, or reach a copy
        that has its login turned off. It applies with the login off as well."""
        path = request.url.path
        if request.method in UNSAFE_METHODS and path.startswith("/api/"):
            if from_another_origin(request.headers, request.url.scheme):
                log.warning("refused %s %s: sent by a page on another site (%s)", request.method,
                            path, request.headers.get("origin") or "origin not given")
                return JSONResponse({"detail": "This request did not come from Horus itself"},
                                    status_code=403)
            has_body = (request.headers.get("content-length", "0") != "0"
                        or "transfer-encoding" in request.headers)
            kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
            if has_body and path not in UPLOAD_PATHS and kind != "application/json":
                return JSONResponse({"detail": "Send the request body as application/json"},
                                    status_code=415)
        return await call_next(request)

    def answers_without_login() -> bool:
        """True while anyone who reaches Horus can use it: login off, or no admin yet."""
        if auth.auth_disabled():
            return True
        conn = connect(db_path())
        try:
            migrate(conn)
            return not auth.admin_exists(conn)
        finally:
            conn.close()

    @app.middleware("http")
    async def known_host_only(request: Request, call_next):
        """While Horus answers without a login, it answers only to an IP address, localhost or
        a name the owner listed (see `listen.known_host`). With a login nothing is checked, and
        the database is only looked at for a name that is not already known."""
        header = request.headers.get("host", "")
        if not listen.known_host(header) and answers_without_login():
            name = listen.host_name(header)
            shown = name if _HOST.fullmatch(name) else "that name"
            log.warning("refused %s %s: opened as %s, which is not an IP address or a name in "
                        "HORUS_ALLOWED_HOSTS", request.method, request.url.path, shown)
            return Response(
                f"Horus does not know the name {shown}.\n\n"
                "While Horus has no login (no account yet, or HORUS_AUTH_DISABLED), it only "
                "answers to its IP address, to localhost, or to a name listed in "
                "HORUS_ALLOWED_HOSTS.\n"
                f"Open Horus by its IP address, or start it with HORUS_ALLOWED_HOSTS={shown}\n",
                status_code=403, media_type="text/plain; charset=utf-8")
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """On every response, including refusals. A route may set a stricter policy of its own
        (the poster proxy does)."""
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy",
                                    content_security_policy(request.headers))
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
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
        servers = fetch_all(store.list_with_tokens(), transport, live.LIVE_MAX_AGE)
        viewer = getattr(request.state, "user", None)
        only = auth.only_media_user(store.conn, viewer)
        problems.annotate(servers)
        if only is not None:  # "hide other people's activity" is on for this person
            for s in servers:
                s.sessions = [x for x in s.sessions if x.user.casefold() == only.casefold()]
        if not auth.sees_addresses(viewer):
            for s in servers:
                for x in s.sessions:
                    x.location = auth.without_address(x.location)
        return Activity(servers=servers, sessions=[x for s in servers for x in s.sessions])

    @app.post("/api/activity/stop", status_code=204)
    def stop_stream(body: StopIn, request: Request, store: Store):
        """End one playing stream. Admins only, from a signed-in session: an API key, even a
        control key, can never do this. The stream is looked up again on the server, so only a
        stream that is playing right now can be stopped."""
        if getattr(request.state, "via_key", None):
            raise HTTPException(403, "Stopping a stream needs a signed-in admin, not an API key")
        found = store.get_with_token(body.server_id)
        if found is None:
            raise HTTPException(404, "Server not found")
        row, token = found
        client = make_client(row["type"], row["name"], row["url"], token, transport=transport)
        who = request.state.user["username"] if getattr(request.state, "user", None) else "someone"
        try:
            target = next((s for s in client.get_sessions() if s.session_key == body.session_key), None)
            if target is None or not target.native_id:
                raise HTTPException(404, "That stream has already ended")
            client.stop_session(target.native_id, body.reason)
            live.forget_live()  # so the page shows it gone at once
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            log.warning("stopping a stream on %s was refused (HTTP %d)", row["name"], code)
            if code in (401, 403):
                raise HTTPException(502, f"{row['name']} refused to stop the stream. Plex needs Plex Pass "
                                         "and an owner token for this; check the token's permissions.") from e
            raise HTTPException(502, f"{row['name']} could not stop the stream (HTTP {code})") from e
        except (httpx.HTTPError, ValueError) as e:
            log.warning("could not stop a stream on %s: %s", row["name"], type(e).__name__)
            raise HTTPException(502, f"Could not reach {row['name']}") from e
        finally:
            client.close()
        log.info("stream stopped by %s: %s's %s on %s%s", who, target.user, target.title, row["name"],
                 " (message sent)" if body.reason else "")

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
        # The bytes come from the media server, so the browser may show them as an image and
        # nothing more: no scripts, no requests, even when the address is opened on its own.
        return Response(content, media_type=content_type, headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        })

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

    @app.get("/api/problems")
    def playback_problems(request: Request, store: Store, start: date | None = None,
                          end: date | None = None):
        """Playback problems recorded in a date range: counts and the latest ones."""
        end = end or date.today()
        start = start or end - timedelta(days=29)
        if start > end or (end - start).days > 730:
            raise HTTPException(422, "Invalid date range")
        only = auth.only_media_user(store.conn, getattr(request.state, "user", None))
        return problems.compute(store.conn, start, end, app_settings.load(store.conn).zone(),
                                only_user=only)

    @app.get("/api/stats/monthly")
    def stats_monthly(request: Request, store: Store):
        only = auth.only_media_user(store.conn, getattr(request.state, "user", None))
        return compute_monthly(store.conn, app_settings.load(store.conn).zone(), only_user=only)

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
