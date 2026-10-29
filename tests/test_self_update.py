import io
import sys
import tarfile

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import __version__, routes_settings, self_update
from horus_media.main import create_app

LATEST = "9.9.9"
ASSET = f"https://github.com/o/r/releases/download/v{LATEST}/horus-media-v{LATEST}.tar.gz"
RELEASE = {"tag_name": f"v{LATEST}", "html_url": "https://github.com/o/r/releases/tag/v9.9.9",
           "assets": [{"name": f"horus-media-v{LATEST}.tar.gz", "browser_download_url": ASSET}]}


def tarball(*names: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in names:
            data = b"wheel bytes"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    monkeypatch.setenv("HORUS_UPDATE_URL", "https://api.github.com/repos/o/r/releases/latest")
    monkeypatch.setattr(routes_settings, "_restart_process", lambda: None)
    served = {"asset": tarball(f"./horus_media-{LATEST}-py3-none-any.whl"), "asset_host": "github.com"}

    def handler(request: httpx.Request):
        if request.url.host == "api.github.com":
            return httpx.Response(200, json=RELEASE)
        if request.url.host == served["asset_host"]:
            return httpx.Response(200, content=served["asset"])
        return httpx.Response(404)

    app = create_app(transport=httpx.MockTransport(handler))
    c = TestClient(app)
    c.served = served
    return c


def as_proxmox(monkeypatch, runs=None):
    monkeypatch.setattr(self_update, "why_not", lambda: None)
    monkeypatch.setattr(self_update, "_installer", lambda: [sys.executable, "-c", "pass"])
    monkeypatch.setattr("horus_media.install_info.kind", lambda: "proxmox")


def test_the_check_offers_update_now_only_where_it_can_work(client, monkeypatch):
    assert LATEST != __version__
    assert client.get("/api/update-check").json()["can_apply"] is False  # not a Proxmox container
    as_proxmox(monkeypatch)
    body = client.get("/api/update-check").json()
    assert body["newer"] and body["can_apply"] is True
    assert "asset_url" not in body


def test_update_now_backs_up_installs_and_restarts(client, monkeypatch, tmp_path):
    as_proxmox(monkeypatch)
    restarted = []
    monkeypatch.setattr(routes_settings, "_restart_process", lambda: restarted.append(1))
    r = client.post("/api/update/apply")
    assert r.status_code == 200 and r.json() == {"updated_to": LATEST, "restarting": True}
    assert list((tmp_path / "backups").glob("horus-*.zip"))  # backed up first


def test_it_refuses_where_it_cannot_update(client):
    r = client.post("/api/update/apply")
    assert r.status_code == 409 and "cannot update itself" in r.json()["detail"]


def test_a_release_that_is_not_on_github_is_not_downloaded(client, monkeypatch):
    as_proxmox(monkeypatch)
    client.served["asset_host"] = "example.com"
    monkeypatch.setitem(RELEASE["assets"][0], "browser_download_url",
                        "https://example.com/horus-media-v9.9.9.tar.gz")
    r = client.post("/api/update/apply")
    assert r.status_code == 502 and "not hosted on GitHub" in r.json()["detail"]


def test_a_tarball_without_exactly_one_matching_wheel_is_refused(client, monkeypatch):
    as_proxmox(monkeypatch)
    client.served["asset"] = tarball("./horus_media-1.0.0-py3-none-any.whl")  # wrong version
    assert client.post("/api/update/apply").status_code == 502
    client.served["asset"] = tarball("evil.whl", "../../etc/passwd")
    assert client.post("/api/update/apply").status_code == 502


def test_a_failing_install_does_not_restart(client, monkeypatch):
    as_proxmox(monkeypatch)
    monkeypatch.setattr(self_update, "_installer", lambda: [sys.executable, "-c", "import sys; sys.exit(3)"])
    restarted = []
    monkeypatch.setattr(routes_settings, "_restart_process", lambda: restarted.append(1))
    r = client.post("/api/update/apply")
    assert r.status_code == 502 and "install step failed" in r.json()["detail"]
    assert not restarted


def test_no_newer_version_means_nothing_to_install(client, monkeypatch):
    as_proxmox(monkeypatch)
    monkeypatch.setitem(RELEASE, "tag_name", f"v{__version__}")
    assert client.post("/api/update/apply").status_code == 409
