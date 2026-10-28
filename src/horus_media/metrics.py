"""Prometheus metrics at /metrics (text exposition format).

A scrape asks each server for its live sessions (read-only), so the numbers are current.
Access needs an admin session or an API key (`Authorization: Bearer hk_...` works with
Prometheus's `authorization` setting). Labels carry server names only: no users or titles.
"""

from collections import Counter
from collections.abc import Callable
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from . import __version__
from .activity import fetch_all
from .store import ServerStore

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
METHODS = ("direct_play", "direct_stream", "transcode")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _line(name: str, labels: dict[str, str], value: float) -> str:
    if not labels:
        return f"{name} {value}"
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items())
    return f"{name}{{{inner}}} {value}"


def render(store: ServerStore, transport: httpx.BaseTransport | None) -> str:
    results = fetch_all(store.list_with_tokens(), transport)
    recorded = {
        r["server_id"]: r["n"]
        for r in store.conn.execute(
            "SELECT server_id, COUNT(*) AS n FROM history GROUP BY server_id")
    }
    out: list[str] = []

    def family(name: str, kind: str, text: str) -> None:
        out.append(f"# HELP {name} {text}")
        out.append(f"# TYPE {name} {kind}")

    family("horus_info", "gauge", "Horus version.")
    out.append(_line("horus_info", {"version": __version__}, 1))

    family("horus_server_up", "gauge", "1 if the media server answered, 0 if not.")
    for r in results:
        out.append(_line("horus_server_up", {"server": r.name, "type": r.type}, int(r.ok)))

    family("horus_streams", "gauge", "Streams playing right now, by play method.")
    for r in results:
        if not r.ok:
            continue
        counts = Counter(s.play_method for s in r.sessions)
        for method in METHODS:
            out.append(_line("horus_streams", {"server": r.name, "type": r.type,
                                               "play_method": method}, counts[method]))

    family("horus_stream_bandwidth_kbps", "gauge", "Combined bandwidth of the streams, in kbit/s.")
    for r in results:
        if r.ok:
            out.append(_line("horus_stream_bandwidth_kbps", {"server": r.name, "type": r.type},
                             sum(s.bandwidth_kbps or 0 for s in r.sessions)))

    family("horus_plays_recorded_total", "counter", "Plays Horus has in its history.")
    for r in results:
        out.append(_line("horus_plays_recorded_total", {"server": r.name, "type": r.type},
                         recorded.get(r.id, 0)))
    return "\n".join(out) + "\n"


def make_router(get_store: Callable, transport: httpx.BaseTransport | None) -> APIRouter:
    router = APIRouter()
    Store = Annotated[ServerStore, Depends(get_store)]

    @router.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
    def metrics(store: Store):
        return PlainTextResponse(render(store, transport), media_type=CONTENT_TYPE)

    return router
