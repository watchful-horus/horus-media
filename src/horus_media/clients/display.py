"""Small wording helpers shared by the Plex and Emby clients."""

CHANNELS = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}


def rate(kbps: float | None) -> str | None:
    if not kbps:
        return None
    return f"{kbps / 1000:.1f} Mbps" if kbps >= 1000 else f"{round(kbps)} kbps"


def resolution(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return f"{text}p" if text.isdigit() else text.upper()


def channels(count: int | None) -> str:
    if not count:
        return ""
    return CHANNELS.get(count, f"{count}ch")


def words(*parts: object) -> str:
    return " ".join(str(p) for p in parts if p)


def episode_label(season: object, episode: object) -> str | None:
    """1, 1 -> 'S1:E1'; None when either number is missing."""
    if season is None or episode is None:
        return None
    return f"S{season}:E{episode}"


def with_label(label: str, detail: str) -> str | None:
    """'Direct Play' + 'H264 720p' -> 'Direct Play (H264 720p)'; None when there is no detail."""
    return f"{label} ({detail})" if detail else None
