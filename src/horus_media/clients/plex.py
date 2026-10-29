import logging
import re
from datetime import UTC, datetime

import httpx

from ..models import HistoryRecord, PlayMethod, Session
from .base import ConnectionResult, MediaServer
from .display import channels, rate, resolution, with_label, words

log = logging.getLogger(__name__)
POSTER_PATH = re.compile(r"^/library/metadata/\d+/(thumb|art)(/\d+)?$")

HISTORY_PAGE = 200
DECISIONS = {"transcode": "Transcode", "copy": "Direct Stream", "burn": "Burn"}


def _first(streams: list[dict], stream_type: int, selected_only: bool = False) -> dict | None:
    candidates = [s for s in streams if s.get("streamType") == stream_type]
    chosen = [s for s in candidates if s.get("selected")]
    if chosen:
        return chosen[0]
    return None if selected_only else (candidates[0] if candidates else None)


def _language(stream: dict) -> str:
    return stream.get("language") or "Unknown"


class PlexClient(MediaServer):
    def _headers(self, token: str) -> dict[str, str]:
        return {"X-Plex-Token": token, "Accept": "application/json"}

    def test_connection(self) -> ConnectionResult:
        mc = self._get("/identity")["MediaContainer"]
        return ConnectionResult(self.name, mc.get("version"))

    def get_sessions(self) -> list[Session]:
        mc = self._get("/status/sessions")["MediaContainer"]
        return [self._normalise(item) for item in mc.get("Metadata", [])]

    def stop_session(self, native_id: str, reason: str = "") -> None:
        """Plex ends a stream with a GET; it needs Plex Pass on the server and an owner token."""
        params = {"sessionId": native_id}
        if reason:
            params["reason"] = reason
        resp = self._http.get("/status/sessions/terminate", params=params)
        log.debug("%s GET /status/sessions/terminate -> %d", self.name, resp.status_code)
        resp.raise_for_status()

    def get_history(self) -> list[HistoryRecord]:
        """Every play in Plex's own history, newest first. Read-only."""
        accounts = {str(a.get("id")): a.get("name") for a in self._container("/accounts", "Account")}
        devices = {str(d.get("id")): d for d in self._container("/devices", "Device")}
        records: list[HistoryRecord] = []
        start = 0
        while True:
            mc = self._get("/status/sessions/history/all", {
                "sort": "viewedAt:desc", "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": HISTORY_PAGE,
            })["MediaContainer"]
            items = mc.get("Metadata", [])
            for item in items:
                if not item.get("viewedAt"):
                    continue
                device = devices.get(str(item.get("deviceID")), {})
                title = item.get("title", "")
                if item.get("type") == "episode" and item.get("grandparentTitle"):
                    title = f"{item['grandparentTitle']} - {title}"
                records.append(HistoryRecord(
                    key=f"{item.get('historyKey') or item.get('ratingKey')}:{item['viewedAt']}",
                    user=accounts.get(str(item.get("accountID"))) or "Unknown",
                    title=title,
                    media_type=item.get("type", "unknown"),
                    device=device.get("name") or "Unknown",
                    platform=device.get("platform") or "Unknown",
                    started_at=datetime.fromtimestamp(item["viewedAt"], UTC),
                ))
            start += len(items)
            if len(items) < HISTORY_PAGE or start >= mc.get("totalSize", start):
                return records

    def _container(self, path: str, key: str) -> list[dict]:
        try:
            return self._get(path)["MediaContainer"].get(key, [])
        except httpx.HTTPError:  # names are nice to have; without them plays show as Unknown
            return []

    def get_image(self, path: str) -> tuple[bytes, str]:
        if not POSTER_PATH.fullmatch(path):
            raise ValueError("unsupported image path")
        # Let Plex resize it, so a card does not download a full-size poster.
        return self._get_image(
            "/photo/:/transcode",
            {"width": 300, "height": 450, "minSize": 1, "url": path},
        )

    def _normalise(self, item: dict) -> Session:
        player = item.get("Player", {})
        transcode = item.get("TranscodeSession")
        method: PlayMethod = "direct_play"
        if transcode:
            decisions = (transcode.get("videoDecision"), transcode.get("audioDecision"))
            method = "transcode" if "transcode" in decisions else "direct_stream"
        duration, offset = item.get("duration"), item.get("viewOffset")
        progress = round(offset / duration * 100, 1) if duration and offset is not None else None
        title = item.get("title", "")
        if item.get("type") == "episode" and item.get("grandparentTitle"):
            title = f"{item['grandparentTitle']} - {title}"
        details = self._details(item, player, transcode or {})
        return Session(
            session_key=str(item.get("sessionKey") or item.get("Session", {}).get("id") or ""),
            native_id=str(item.get("Session", {}).get("id") or ""),
            server=self.name,
            user=item.get("User", {}).get("title", "Unknown"),
            title=title,
            media_type=item.get("type", "unknown"),
            device=player.get("title") or player.get("device") or "Unknown",
            platform=player.get("platform") or player.get("product") or "Unknown",
            play_method=method,
            bandwidth_kbps=item.get("Session", {}).get("bandwidth"),
            progress_percent=progress,
            state="paused" if player.get("state") == "paused" else "playing",
            poster=item.get("grandparentThumb" if item.get("type") == "episode" else "thumb")
            or item.get("thumb"),
            position_ms=offset,
            duration_ms=duration,
            **details,
        )

    @staticmethod
    def _details(item: dict, player: dict, ts: dict) -> dict:
        media = (item.get("Media") or [{}])[0]
        streams = ((media.get("Part") or [{}])[0]).get("Stream") or []

        def label(decision: str | None) -> str:
            return DECISIONS.get(decision or "", "Direct Play") if ts else "Direct Play"

        video_label, audio_label = label(ts.get("videoDecision")), label(ts.get("audioDecision"))
        source_video = words((media.get("videoCodec") or "").upper(),
                             resolution(media.get("videoResolution")))
        video = source_video
        if video_label == "Transcode" and ts.get("videoCodec"):
            video = f"{source_video} → " + words(
                ts["videoCodec"].upper(), resolution(ts.get("height")))

        audio_stream = _first(streams, 2)
        audio = ""
        if audio_stream:
            audio = f"{_language(audio_stream)} - " + words(
                (audio_stream.get("codec") or "").upper(),
                channels(audio_stream.get("channels")))
        sub_stream = _first(streams, 3, selected_only=True)
        subtitle = None
        if sub_stream:
            subtitle = with_label(
                label(ts.get("subtitleDecision")),
                f"{_language(sub_stream)} - {(sub_stream.get('codec') or '').upper()}".rstrip(" -"),
            )

        both_copy = ts.get("videoDecision") == "copy" and ts.get("audioDecision") == "copy"
        container_label = (
            "Direct Play" if not ts else "Direct Stream" if both_copy else "Transcode"
        )
        container_text = (media.get("container") or "").upper()
        if ts.get("container") and ts["container"].upper() != container_text:
            container_text = f"{container_text} → {ts['container'].upper()}"

        original = rate(media.get("bitrate"))
        if video_label == "Transcode":
            quality = "Converted"
        else:
            quality = f"Original ({original})" if original else "Original"

        address = player.get("address") or player.get("remotePublicAddress")
        where = "LAN" if (item.get("Session", {}).get("location") == "lan" or
                          player.get("local")) else "WAN"
        return {
            "product": player.get("product"),
            "quality": quality,
            "container": with_label(container_label, container_text),
            "video": with_label(video_label, video),
            "audio": with_label(audio_label, audio),
            "subtitle": subtitle,
            "location": f"{where}: {address}" if address else where,
        }
