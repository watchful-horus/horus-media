import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media.main import create_app

from .conftest import TOKEN, fake_servers
from .test_auth import add_user, set_up


@pytest.fixture
def secured(tmp_path, monkeypatch):
    monkeypatch.delenv("HORUS_AUTH_DISABLED", raising=False)
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    app = create_app(transport=httpx.MockTransport(fake_servers))
    admin = TestClient(app, client=("127.0.0.1", 50000))
    set_up(admin)
    admin.post("/api/servers", json={"type": "plex", "name": "Plex", "url": "http://p",
                                     "token": TOKEN})
    admin.post("/api/servers", json={"type": "emby", "name": "Emby", "url": "http://e",
                                     "token": TOKEN})
    admin.post("/api/servers", json={"type": "plex", "name": "Broken", "url": "http://b",
                                     "token": "wrong"})
    return app, admin


def test_metrics_in_prometheus_format(secured):
    _, admin = secured
    r = admin.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    text = r.text
    assert '# TYPE horus_server_up gauge' in text
    assert 'horus_server_up{server="Plex",type="plex"} 1' in text
    assert 'horus_server_up{server="Broken",type="plex"} 0' in text
    assert 'horus_streams{server="Plex",type="plex",play_method="transcode"} 1' in text
    assert 'horus_stream_bandwidth_kbps{server="Plex",type="plex"} 11000' in text
    assert 'horus_plays_recorded_total{server="Emby",type="emby"} 0' in text
    # Counts only: no people or titles leak into the labels.
    assert "alice" not in text and "Pilot" not in text and TOKEN not in text


def test_metrics_needs_a_login_or_a_global_key(secured):
    app, admin = secured
    anon = TestClient(app, client=("10.0.0.2", 1))
    assert anon.get("/metrics").status_code == 401
    key = admin.post("/api/keys", json={"name": "prometheus"}).json()["key"]
    ok = anon.get("/metrics", headers={"Authorization": f"Bearer {key}"})
    assert ok.status_code == 200 and "horus_info" in ok.text


def test_a_normal_persons_key_cannot_read_metrics(secured):
    app, admin = secured
    add_user(admin, "viewer", "viewer password")
    user = TestClient(app, client=("10.0.0.3", 1))
    user.post("/api/auth/login", json={"username": "viewer", "password": "viewer password"})
    key = user.post("/api/my-keys", json={"name": "k"}).json()["key"]
    anon = TestClient(app, client=("10.0.0.4", 1))
    assert anon.get("/metrics", headers={"X-API-Key": key}).status_code == 401
    assert user.get("/metrics").status_code == 403
