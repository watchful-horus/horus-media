import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media.main import create_app

TOKEN = "SECRET-TOKEN-abc123"

PLEX_IDENTITY = {"MediaContainer": {"machineIdentifier": "x", "version": "1.41.0"}}
PLEX_SESSIONS = {"MediaContainer": {"size": 2, "Metadata": [
    {"type": "episode", "title": "Pilot", "grandparentTitle": "Some Show",
     "duration": 2_000_000, "viewOffset": 500_000,
     "User": {"title": "alice"},
     "Player": {"title": "Living Room TV", "platform": "Roku", "state": "playing"},
     "Session": {"bandwidth": 8000},
     "TranscodeSession": {"videoDecision": "transcode", "audioDecision": "copy"}},
    {"type": "movie", "title": "A Film", "duration": 1000, "viewOffset": 100,
     "User": {"title": "bob"},
     "Player": {"title": "Phone", "platform": "Android", "state": "paused"},
     "Session": {"bandwidth": 3000}},
]}}
EMBY_INFO = {"ServerName": "EmbyBox", "Version": "4.8.0.0"}
EMBY_SESSIONS = [
    {"UserName": "carol", "DeviceName": "Chrome", "Client": "Emby Web",
     "NowPlayingItem": {"Name": "Ep 1", "SeriesName": "Other Show", "Type": "Episode",
                        "RunTimeTicks": 10_000_000_000},
     "PlayState": {"PlayMethod": "Transcode", "PositionTicks": 2_500_000_000, "IsPaused": True},
     "TranscodingInfo": {"Bitrate": 4_000_000}},
    {"UserName": "dave", "DeviceName": "TV", "Client": "Emby for Android TV"},
]


def fake_servers(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    sent = request.headers.get("x-plex-token") or request.headers.get("x-emby-token")
    if sent != TOKEN:
        return httpx.Response(401)
    if path == "/identity":
        return httpx.Response(200, json=PLEX_IDENTITY)
    if path == "/status/sessions":
        return httpx.Response(200, json=PLEX_SESSIONS)
    if path == "/System/Info/Public":
        return httpx.Response(200, json=EMBY_INFO)
    if path == "/Sessions":
        return httpx.Response(200, json=EMBY_SESSIONS)
    return httpx.Response(404)


@pytest.fixture
def transport():
    return httpx.MockTransport(fake_servers)


@pytest.fixture
def client(tmp_path, monkeypatch, transport):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    return TestClient(create_app(transport=transport))


@pytest.fixture(autouse=True)
def login_off_by_default(monkeypatch):
    """Most tests exercise features, not the login. The auth tests switch it back on."""
    monkeypatch.setenv("HORUS_AUTH_DISABLED", "1")
