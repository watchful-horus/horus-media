"""Jellyfin. Its sessions, items and pictures have the same shape as Emby's (the two share an
ancestor), so this client reuses the Emby reading code and changes only what differs: how the
key is sent, what a conversion reports, and how the Playback Reporting plugin is read."""

import logging
import re
from datetime import UTC, datetime

import httpx

from ..models import HistoryRecord
from .base import ConnectionResult
from .emby import HISTORY_DAYS, TICKS_PER_MS, EmbyClient, _method, _reason_text

log = logging.getLogger(__name__)
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# The plugin gives a play's time of day as text in the server's own style.
TIME_FORMATS = ("%I:%M %p", "%H:%M", "%H:%M:%S", "%I:%M:%S %p")
PLUGIN_MISSING = (400, 401, 403, 404, 500)


def _started(day: str, time_of_day: str) -> tuple[datetime, bool]:
    """When a play began, and whether only its day is known."""
    text = " ".join(str(time_of_day or "").upper().replace(".", "").split())
    for fmt in TIME_FORMATS:
        try:
            clock = datetime.strptime(text, fmt).time()
        except ValueError:
            continue
        return datetime.combine(datetime.fromisoformat(day).date(), clock, tzinfo=UTC), False
    return datetime.fromisoformat(day).replace(tzinfo=UTC), True


class JellyfinClient(EmbyClient):
    def _headers(self, token: str) -> dict[str, str]:
        # Jellyfin's documented way to send an API key. A quote would end the value early.
        return {"Authorization": f'MediaBrowser Token="{token.replace(chr(34), "")}"',
                "Accept": "application/json"}

    def test_connection(self) -> ConnectionResult:
        # Unlike the public page Emby is asked for, this one needs the key, so a wrong key fails.
        info = self._get("/System/Info")
        return ConnectionResult(info.get("ServerName") or self.name, info.get("Version"))

    def _health(self, ti: dict, item: dict) -> dict:
        """Why Jellyfin converts the stream and how far into the file the conversion has got,
        from the share it says is done. Jellyfin never says when a conversion is resting because
        it is far enough ahead, so its frame rate is not used as a speed: the poller works one
        out from how far the conversion moves between two polls (see problems.py)."""
        reasons = [r for r in ti.get("TranscodeReasons") or [] if isinstance(r, str)]
        out: dict = {"transcode_reason": _reason_text(reasons),
                     "direct_play_failed": "DirectPlayError" in reasons}
        done, runtime = ti.get("CompletionPercentage"), item.get("RunTimeTicks")
        if (ti.get("IsVideoDirect") is False and isinstance(done, int | float)
                and not isinstance(done, bool) and 0 <= done < 100
                and isinstance(runtime, int) and runtime > 0):
            out["transcoded_ms"] = int(runtime * done / 100) // TICKS_PER_MS
            out["throttle_reported"] = False
        return out

    def get_history(self) -> list[HistoryRecord]:
        """Plays recorded by Jellyfin's Playback Reporting plugin. Raises LookupError if it is
        not installed (or the key is not an administrator's). Only reads: the plugin's query
        call is not used because it can also change data."""
        try:
            users = self._get("/user_usage_stats/user_list")
            kinds = self._get("/user_usage_stats/type_filter_list")
        except httpx.HTTPStatusError as e:
            if e.response.status_code in PLUGIN_MISSING:
                raise LookupError("Playback Reporting plugin not available") from e
            raise
        kinds = ",".join(k for k in kinds if isinstance(k, str) and k) if isinstance(kinds, list) else ""
        if not kinds:
            return []
        names = {u.get("id"): u.get("name") for u in users if isinstance(u, dict)}
        # One call says which days each person played something; only those days are read.
        activity = self._get("/user_usage_stats/PlayActivity", {
            "days": HISTORY_DAYS, "endDate": datetime.now(UTC).date().isoformat(),
            "dataType": "count", "filter": kinds})
        records: list[HistoryRecord] = []
        for entry in activity if isinstance(activity, list) else []:
            user_id = entry.get("user_id")
            if not user_id or user_id == "labels_user" or not re.fullmatch(r"[0-9A-Fa-f-]+", user_id):
                continue
            user = names.get(user_id) or entry.get("user_name") or "Unknown"
            for day, count in sorted((entry.get("user_usage") or {}).items()):
                if not count or not DAY.fullmatch(day):
                    continue
                rows = self._get(f"/user_usage_stats/{user_id}/{day}/GetItems", {"filter": kinds})
                for row in rows if isinstance(rows, list) else []:
                    records.append(self._record(row, user, day))
        return records

    @staticmethod
    def _record(row: dict, user: str, day: str) -> HistoryRecord:
        started, date_only = _started(day, row.get("Time"))
        name = row.get("Name") or ""
        try:
            seconds = int(float(row.get("Duration") or 0))
        except (TypeError, ValueError):
            seconds = 0
        return HistoryRecord(
            key=f"{row.get('RowId') or row.get('Id') or name}:{started.isoformat()}",
            user=user,
            title=name,
            media_type=str(row.get("Type") or "unknown").lower(),
            device=row.get("Device") or "Unknown",
            platform=row.get("Client") or "Unknown",
            play_method=_method(str(row.get("Method") or "")),
            started_at=started,
            duration_seconds=max(seconds, 0),
            date_only=date_only,
        )
