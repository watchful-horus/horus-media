import httpx

from ..models import ServerType
from .base import ConnectionResult, MediaServer
from .emby import EmbyClient
from .jellyfin import JellyfinClient
from .plex import PlexClient

__all__ = ["ConnectionResult", "EmbyClient", "JellyfinClient", "MediaServer", "PlexClient", "make_client"]

CLIENTS: dict[str, type[MediaServer]] = {
    "plex": PlexClient, "emby": EmbyClient, "jellyfin": JellyfinClient}


def make_client(type_: ServerType, name: str, url: str, token: str,
                transport: httpx.BaseTransport | None = None) -> MediaServer:
    return CLIENTS[type_](name, url, token, transport=transport)
