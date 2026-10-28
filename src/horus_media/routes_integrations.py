"""Notification channels and the Seerr connection (admin only: neither is on the user allow-list)."""

import logging
from collections.abc import Callable
from datetime import date
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from . import app_settings, notifications, reports, seerr
from .store import ServerStore

log = logging.getLogger(__name__)


class ChannelIn(BaseModel):
    name: str
    kind: str
    url: str
    events: list[str]


class ChannelChange(BaseModel):
    enabled: bool | None = None
    events: list[str] | None = None


class SeerrIn(BaseModel):
    url: str
    api_key: str | None = None  # left out when only the address changes


def make_router(get_store: Callable, transport: httpx.BaseTransport | None) -> APIRouter:
    router = APIRouter(prefix="/api")
    Store = Annotated[ServerStore, Depends(get_store)]

    # --- notifications ---

    # --- reports ---

    def report_range(start: date | None, end: date | None) -> tuple[date, date]:
        if start is None or end is None:
            start, end = reports.last_days(7)
        if start > end or (end - start).days > 365:
            raise HTTPException(422, "Invalid date range")
        return start, end

    @router.get("/report")
    def report(store: Store, start: date | None = None, end: date | None = None,
               format: Literal["json", "csv"] = "json"):
        start, end = report_range(start, end)
        built = reports.build(store.conn, start, end, app_settings.load(store.conn).zone())
        if format == "csv":
            return Response(reports.to_csv(built), media_type="text/csv", headers={
                "Content-Disposition": f'attachment; filename="horus-{start}-to-{end}.csv"'})
        return {**built, "text": reports.to_text(built)}

    @router.post("/report/send", status_code=204)
    def send_report(store: Store, start: date | None = None, end: date | None = None):
        """Send the report now to every channel that has the report event ticked."""
        start, end = report_range(start, end)
        built = reports.build(store.conn, start, end, app_settings.load(store.conn).zone())
        notifications.notify(store.conn, store.cipher, "report", "Horus report",
                             reports.to_text(built), transport, wait=True)
        log.info("report sent on request: %s to %s", start, end)
        return Response(status_code=204)

    @router.get("/notifications")
    def channels(store: Store):
        return {"events": notifications.EVENTS, "kinds": notifications.KINDS,
                "channels": notifications.list_channels(store.conn, store.cipher)}

    @router.post("/notifications", status_code=201)
    def add_channel(body: ChannelIn, store: Store):
        try:
            channel = notifications.add_channel(store.conn, store.cipher, body.name, body.kind,
                                                body.url, body.events)
        except notifications.ChannelError as e:
            raise HTTPException(422, str(e)) from None
        log.info("notification channel added: %s (%s)", channel["name"], channel["kind"])
        return channel

    @router.patch("/notifications/{channel_id}")
    def change_channel(channel_id: int, body: ChannelChange, store: Store):
        try:
            channel = notifications.update_channel(store.conn, store.cipher, channel_id,
                                                   body.enabled, body.events)
        except notifications.ChannelError as e:
            raise HTTPException(422, str(e)) from None
        if channel is None:
            raise HTTPException(404, "Channel not found")
        return channel

    @router.delete("/notifications/{channel_id}", status_code=204)
    def delete_channel(channel_id: int, store: Store):
        if not notifications.delete_channel(store.conn, channel_id):
            raise HTTPException(404, "Channel not found")
        log.info("notification channel removed: id %d", channel_id)
        return Response(status_code=204)

    @router.post("/notifications/{channel_id}/test")
    def test_channel(channel_id: int, store: Store):
        row = store.conn.execute("SELECT kind, url FROM notification_channels WHERE id = ?",
                                 (channel_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Channel not found")
        problem = notifications.send(
            row["kind"], store.cipher.decrypt(row["url"]), "test", "Horus test",
            "This is a test message from Horus.", transport)
        return {"ok": problem is None, "error": problem}

    # --- Seerr ---

    def client(store: ServerStore) -> seerr.SeerrClient:
        cfg = seerr.get_config(store.conn, store.cipher)
        if cfg is None:
            raise HTTPException(404, "Seerr is not set up")
        return seerr.SeerrClient(*cfg, transport=transport)

    @router.get("/seerr")
    def seerr_status(store: Store):
        return seerr.public(store.conn)

    @router.put("/seerr")
    def seerr_save(body: SeerrIn, store: Store):
        try:
            seerr.save_config(store.conn, store.cipher, body.url, body.api_key)
        except seerr.SeerrError as e:
            raise HTTPException(422, str(e)) from None
        log.info("Seerr connection saved")
        return seerr.public(store.conn)

    @router.delete("/seerr", status_code=204)
    def seerr_remove(store: Store):
        seerr.clear_config(store.conn)
        return Response(status_code=204)

    @router.post("/seerr/test")
    def seerr_test(store: Store):
        http = client(store)
        try:
            return {"ok": True, "version": http.status()}
        except httpx.HTTPStatusError as e:
            bad = e.response.status_code in (401, 403)
            return {"ok": False, "error": "API key not accepted" if bad
                    else f"HTTP {e.response.status_code}"}
        except (httpx.HTTPError, ValueError):
            return {"ok": False, "error": "Could not connect or unexpected response"}
        finally:
            http.close()

    @router.get("/seerr/requests")
    def seerr_requests(store: Store):
        http = client(store)
        try:
            return http.requests()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            log.warning("Seerr requests failed: %s", type(e).__name__)
            raise HTTPException(502, "Could not read requests from Seerr") from None
        finally:
            http.close()

    return router
