from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

ServerType = Literal["plex", "emby"]
PlayMethod = Literal["direct_play", "direct_stream", "transcode"]
PlayState = Literal["playing", "paused", "buffering"]  # buffering: Plex only


class Session(BaseModel):
    session_key: str = ""  # stable while one play lasts; used to tell plays apart in history
    server: str
    user: str
    title: str
    episode: str | None = None  # "S1:E1" for a TV episode, otherwise None
    media_type: str
    device: str
    platform: str
    play_method: PlayMethod
    bandwidth_kbps: int | None = None
    progress_percent: float | None = None
    state: PlayState
    poster: str | None = None  # server-relative image path; fetched via the poster proxy
    # Detail shown on the activity card, already worded for display (e.g. "Direct Play (H264 720p)").
    product: str | None = None
    quality: str | None = None
    container: str | None = None
    video: str | None = None
    audio: str | None = None
    subtitle: str | None = None
    location: str | None = None
    position_ms: int | None = None
    duration_ms: int | None = None
    # How fast the server converts compared with playback: 1.0 keeps up, below 1.0 falls behind.
    # None when nothing is converted or the server does not say.
    transcode_speed: float | None = None
    # The server has paused converting because it is far enough ahead; a low speed is then fine.
    transcode_throttled: bool = Field(False, exclude=True)
    transcode_reason: str | None = None  # why it is converted, in words (Emby only)
    direct_play_failed: bool = Field(False, exclude=True)  # Emby fell back after an error
    # Playback problems Horus has confirmed for this stream (see problems.py); filled in for
    # the Activity page, empty straight from a client.
    problems: list[str] = []
    # The server's own id for this stream, used to stop it. Never sent to the browser.
    native_id: str = Field("", exclude=True)


class StopIn(BaseModel):
    server_id: int
    session_key: str = Field(min_length=1, max_length=200)
    reason: str = Field("", max_length=200)  # optional message shown to the viewer

    @field_validator("reason")
    @classmethod
    def _tidy(cls, v: str) -> str:
        return v.strip()


class HistoryRecord(BaseModel):
    """One finished play read from a server's own history (for the optional past-history import)."""

    key: str  # stable per play on that server, so re-importing never duplicates
    user: str
    title: str
    media_type: str
    device: str = "Unknown"
    platform: str = "Unknown"
    play_method: str = "unknown"
    started_at: datetime
    duration_seconds: int = 0
    date_only: bool = False  # the server gave only a date; the time of day is made up on import


def _server_name(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("Name cannot be empty")
    return v


def _server_url(v: str) -> str:
    """A media server's address: http or https with a host, and nothing odd in it."""
    v = v.strip()
    try:
        parts = urlsplit(v)
        scheme, host = parts.scheme.lower(), parts.hostname
    except ValueError:
        scheme, host = "", None
    if scheme not in ("http", "https") or not host or any(c.isspace() for c in v):
        raise ValueError("The address must start with http:// or https:// and name a server")
    return v


class ServerIn(BaseModel):
    type: ServerType
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2048)
    token: str = Field(min_length=1, max_length=4096)

    _name = field_validator("name")(_server_name)
    _url = field_validator("url")(_server_url)


class ServerRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    _name = field_validator("name")(_server_name)


class ServerTestIn(BaseModel):
    type: ServerType
    url: str = Field(min_length=1, max_length=2048)
    token: str = Field(min_length=1, max_length=4096)

    _url = field_validator("url")(_server_url)


class ServerOut(BaseModel):
    """Public view of a server. Deliberately has no token field."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    type: ServerType
    name: str
    url: str
    created_at: str


class TestResult(BaseModel):
    ok: bool
    server_name: str | None = None
    version: str | None = None
    error: str | None = None


class ServerActivity(BaseModel):
    """Live sessions for one server; `ok` is False when it could not be reached."""

    id: int
    name: str
    type: ServerType
    ok: bool
    error: str | None = None
    sessions: list[Session] = []


class Activity(BaseModel):
    servers: list[ServerActivity]
    sessions: list[Session]


class PlexLinkStart(BaseModel):
    pin_id: int
    auth_url: str


class PlexConnection(BaseModel):
    url: str
    local: bool


class PlexResource(BaseModel):
    id: str
    name: str
    owned: bool
    connections: list[PlexConnection]


class PlexLinkStatus(BaseModel):
    status: Literal["pending", "done", "expired"]
    servers: list[PlexResource] = []


class PlexLinkAdd(BaseModel):
    resource_id: str
    url: str
