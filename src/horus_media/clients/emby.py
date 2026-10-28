import ipaddress
import re
from datetime import UTC, datetime

import httpx

from ..models import HistoryRecord, PlayMethod, Session
from .base import ConnectionResult, MediaServer
from .display import channels, rate, resolution, with_label, words

POSTER_PATH = re.compile(r"^/Items/[0-9A-Za-z]+/Images/Primary$")
TICKS_PER_MS = 10_000
HISTORY_DAYS = 3650


def _poster(item: dict) -> str | None:
    """Series poster for episodes, otherwise the item's own primary image."""
    if item.get("SeriesId") and item.get("SeriesPrimaryImageTag"):
        return f"/Items/{item['SeriesId']}/Images/Primary"
    if (item.get("ImageTags") or {}).get("Primary") and item.get("Id"):
        return f"/Items/{item['Id']}/Images/Primary"
    if item.get("PrimaryImageItemId"):
        return f"/Items/{item['PrimaryImageItemId']}/Images/Primary"
    return None


def _source_bitrate(item: dict) -> int | None:
    """Best-effort bits/s for direct play/stream, where Emby reports no live rate."""
    if item.get("Bitrate"):
        return item["Bitrate"]
    for source in item.get("MediaSources") or []:
        if source.get("Bitrate"):
            return source["Bitrate"]
    total = sum(st.get("BitRate") or 0 for st in item.get("MediaStreams") or [])
    return total or None


def _location(endpoint: str | None) -> str | None:
    if not endpoint:
        return None
    host = endpoint.rsplit(":", 1)[0] if endpoint.count(":") == 1 else endpoint
    host = host.strip("[]").removeprefix("::ffff:")
    try:
        private = ipaddress.ip_address(host).is_private
    except ValueError:
        return host
    return f"{'LAN' if private else 'WAN'}: {host}"


def _row_time(row: dict) -> datetime | None:
    raw = row.get("DateCreated") or row.get("Time") or row.get("date")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _method(text: str) -> str:
    lowered = text.lower()
    if "transcode" in lowered:
        return "transcode"
    if "directstream" in lowered.replace(" ", ""):
        return "direct_stream"
    if "directplay" in lowered.replace(" ", ""):
        return "direct_play"
    return "unknown"


def _language(stream: dict) -> str:
    return stream.get("Language") or "Unknown"


class EmbyClient(MediaServer):
    def _headers(self, token: str) -> dict[str, str]:
        return {"X-Emby-Token": token, "Accept": "application/json"}

    def test_connection(self) -> ConnectionResult:
        info = self._get("/System/Info/Public")
        return ConnectionResult(info.get("ServerName") or self.name, info.get("Version"))

    def get_sessions(self) -> list[Session]:
        return [self._normalise(s) for s in self._get("/Sessions") if s.get("NowPlayingItem")]

    def get_history(self) -> list[HistoryRecord]:
        """Plays recorded by the Playback Reporting plugin. Raises LookupError if it is not
        installed (or the key is not an administrator's)."""
        try:
            users = self._get("/user_usage_stats/user_list")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (400, 401, 403, 404, 500):
                raise LookupError("Playback Reporting plugin not available") from e
            raise
        records: list[HistoryRecord] = []
        for user in users:
            rows = self._get("/user_usage_stats/UserPlaylist", {
                "user_id": user.get("id"), "aggregate_data": "false", "days": HISTORY_DAYS,
                "end_date": datetime.now(UTC).date().isoformat(),
            })
            for row in rows if isinstance(rows, list) else []:
                started = _row_time(row)
                if started is None:
                    continue
                name = row.get("ItemName") or row.get("Name") or ""
                method = str(row.get("PlaybackMethod") or row.get("PlayMethod") or "")
                records.append(HistoryRecord(
                    key=f"{row.get('rowid') or row.get('RowId') or row.get('ItemId') or name}:"
                        f"{started.isoformat()}",
                    user=row.get("user_name") or user.get("name") or "Unknown",
                    title=name,
                    media_type=str(row.get("ItemType") or row.get("Type") or "unknown").lower(),
                    device=row.get("DeviceName") or "Unknown",
                    platform=row.get("ClientName") or "Unknown",
                    play_method=_method(method),
                    started_at=started,
                    duration_seconds=int(row.get("PlayDuration") or row.get("Duration") or 0),
                ))
        return records

    def get_image(self, path: str) -> tuple[bytes, str]:
        if not POSTER_PATH.fullmatch(path):
            raise ValueError("unsupported image path")
        return self._get_image(path, {"maxHeight": 450, "quality": 85})

    def _normalise(self, s: dict) -> Session:
        item = s["NowPlayingItem"]
        play_state = s.get("PlayState", {})
        method: PlayMethod = {
            "DirectPlay": "direct_play",
            "DirectStream": "direct_stream",
            "Transcode": "transcode",
        }.get(play_state.get("PlayMethod"), "direct_play")
        runtime, position = item.get("RunTimeTicks"), play_state.get("PositionTicks")
        progress = round(position / runtime * 100, 1) if runtime and position is not None else None
        bitrate = (s.get("TranscodingInfo") or {}).get("Bitrate") or _source_bitrate(item)
        title = item.get("Name", "")
        if item.get("SeriesName"):
            title = f"{item['SeriesName']} - {title}"
        return Session(
            session_key=f"{s.get('Id', '')}:{item.get('Id', '')}",
            server=self.name,
            user=s.get("UserName", "Unknown"),
            title=title,
            media_type=str(item.get("Type", "unknown")).lower(),
            device=s.get("DeviceName") or "Unknown",
            platform=s.get("Client") or "Unknown",
            play_method=method,
            bandwidth_kbps=round(bitrate / 1000) if bitrate else None,
            progress_percent=progress,
            state="paused" if play_state.get("IsPaused") else "playing",
            poster=_poster(item),
            position_ms=position // TICKS_PER_MS if position is not None else None,
            duration_ms=runtime // TICKS_PER_MS if runtime else None,
            **self._details(s, item, play_state, method),
        )

    @staticmethod
    def _details(s: dict, item: dict, play_state: dict, method: PlayMethod) -> dict:
        ti = s.get("TranscodingInfo") or {}
        streams = item.get("MediaStreams") or []

        def stream_of(kind: str, index: int | None = None) -> dict | None:
            of_kind = [x for x in streams if x.get("Type") == kind]
            if index is not None:
                for x in of_kind:
                    if x.get("Index") == index:
                        return x
            return of_kind[0] if of_kind and kind != "Subtitle" else None

        def label(direct: bool | None) -> str:
            if method == "direct_play" or not ti:
                return "Direct Play"
            return "Direct Stream" if direct else "Transcode"

        video_stream = stream_of("Video") or {}
        video_label = label(ti.get("IsVideoDirect"))
        video = words((video_stream.get("Codec") or "").upper(),
                      resolution(video_stream.get("Height")))
        if video_label == "Transcode" and ti.get("VideoCodec"):
            video = f"{video} → {ti['VideoCodec'].upper()}"

        audio_stream = stream_of("Audio", play_state.get("AudioStreamIndex"))
        audio = ""
        if audio_stream:
            audio = f"{_language(audio_stream)} - " + words(
                (audio_stream.get("Codec") or "").upper(),
                channels(audio_stream.get("Channels")))
        sub_index = play_state.get("SubtitleStreamIndex")
        sub_stream = stream_of("Subtitle", sub_index) if sub_index not in (None, -1) else None
        subtitle = None
        if sub_stream:
            subtitle = with_label(
                "Direct Play" if method == "direct_play" else "Direct Stream",
                f"{_language(sub_stream)} - {(sub_stream.get('Codec') or '').upper()}"
                .rstrip(" -"),
            )

        container = (ti.get("Container") or (s["NowPlayingItem"].get("Container") or "")).upper()
        original = rate((_source_bitrate(item) or 0) / 1000)
        quality = (
            f"Converted ({rate(ti['Bitrate'] / 1000)})"
            if video_label == "Transcode" and ti.get("Bitrate")
            else f"Original ({original})" if original else "Original"
        )
        return {
            "product": s.get("Client"),
            "quality": quality,
            "container": with_label(label(ti.get("IsVideoDirect") and ti.get("IsAudioDirect")),
                                    container),
            "video": with_label(video_label, video),
            "audio": with_label(label(ti.get("IsAudioDirect")), audio),
            "subtitle": subtitle,
            "location": _location(s.get("RemoteEndPoint")),
        }
