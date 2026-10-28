import httpx

from ..models import ServerType
from .base import ConnectionResult, MediaServer
from .emby import EmbyClient
from .plex import PlexClient

__all__ = ["ConnectionResult", "EmbyClient", "MediaServer", "PlexClient", "make_client"]

CLIENTS: dict[str, type[MediaServer]] = {"plex": PlexClient, "emby": EmbyClient}


def make_client(type_: ServerType, name: str, url: str, token: str,
                transport: httpx.BaseTransport | None = None) -> MediaServer:
    return CLIENTS[type_](name, url, token, transport=transport)
