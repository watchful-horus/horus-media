from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ServerType = Literal["plex", "emby"]
PlayMethod = Literal["direct_play", "direct_stream", "transcode"]
PlayState = Literal["playing", "paused"]


class Session(BaseModel):
    session_key: str = ""  # stable while one play lasts; used to tell plays apart in history
    server: str
    user: str
    title: str
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


class ServerIn(BaseModel):
    type: ServerType
    name: str
    url: str
    token: str


class ServerRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def _trimmed(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be empty")
        return v


class ServerTestIn(BaseModel):
    type: ServerType
    url: str
    token: str


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
