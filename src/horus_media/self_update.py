"""The "Update now" button for a Proxmox container.

Docker updates are done with Docker and manual installs by hand: only an LXC container that
Horus itself can write to is updated from here. The admin presses the button on a signed-in
session (never an API or control key), a backup is written first, and the new release is
installed into the environment Horus is running from, then Horus restarts.

The download comes only from the official Horus repository's releases on GitHub, over https,
whatever release address is set in Settings. Its SHA-256 must match the one GitHub reports for
that release file, and only the wheel inside the release tarball is used.
"""

import hashlib
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import __version__, backup, install_info
from .config import db_path

log = logging.getLogger(__name__)

# The only place "Update now" installs from. A constant on purpose: the release address in
# Settings can be changed, this cannot.
OFFICIAL_HOST = "github.com"
OFFICIAL_PATH = "/watchful-horus/horus-media/releases/download/"
# Where github.com may redirect the download to.
ALLOWED_HOSTS = {OFFICIAL_HOST, "objects.githubusercontent.com", "release-assets.githubusercontent.com"}
SHA256 = re.compile(r"[0-9a-f]{64}")
MAX_DOWNLOAD = 150 * 1024 * 1024
WHEEL = re.compile(r"horus_media-(\d+(?:\.\d+)*)-py3-none-any\.whl")
INSTALL_TIMEOUT = 600


class SelfUpdateError(Exception):
    """The message is safe to show."""


def _installer() -> list[str] | None:
    """The command that installs a wheel into this Python environment (pip, or uv for the
    environments the community-scripts installer makes, which have no pip)."""
    probe = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, check=False)
    if probe.returncode == 0:
        return [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall",
                "--no-cache-dir"]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, "--reinstall"]
    return None


def why_not() -> str | None:
    """None when this copy can update itself, otherwise a short reason (for the log)."""
    if install_info.kind() != "proxmox":
        return "not a Proxmox container"
    if sys.prefix == sys.base_prefix:
        return "not running from its own environment"
    if not os.access(sys.prefix, os.W_OK):
        return "the Horus user cannot write to its environment"
    if _installer() is None:
        return "neither pip nor uv is available"
    return None


def asset_for(body: dict, latest: str) -> tuple[str, str | None] | None:
    """The release tarball's address and SHA-256 (None when the document gives none) from a
    GitHub "latest release" document."""
    for asset in body.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        url = str(asset.get("browser_download_url", ""))
        if name == f"horus-media-v{latest}.tar.gz":
            digest = str(asset.get("digest") or "").removeprefix("sha256:").lower()
            return url, digest if SHA256.fullmatch(digest) else None
    return None


def is_official(url: str | None) -> bool:
    """True for a release file of the official Horus repository on GitHub, over https."""
    if not url:
        return False
    parts = urlparse(url)
    try:
        port = parts.port
    except ValueError:
        return False
    # No "..": the path is compared as text, and the server would resolve it to somewhere else.
    return (parts.scheme == "https" and parts.hostname == OFFICIAL_HOST and port is None
            and not parts.username and parts.path.startswith(OFFICIAL_PATH)
            and ".." not in parts.path and "\\" not in url)


def _check_url(url: str) -> None:
    if not is_official(url):
        raise SelfUpdateError("The release is not from the official Horus repository on GitHub, "
                              "so it was not downloaded")


def _check_redirect(url: str) -> None:
    parts = urlparse(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS:
        raise SelfUpdateError("The download was sent somewhere other than GitHub, so it was not used")


def _download(url: str, sha256: str | None, transport: httpx.BaseTransport | None) -> bytes:
    _check_url(url)
    if not sha256 or not SHA256.fullmatch(sha256):
        raise SelfUpdateError("The release has no checksum to verify the download against, "
                              "so it was not downloaded")
    try:
        with httpx.Client(timeout=60.0, transport=transport, follow_redirects=True,
                          headers={"User-Agent": f"horus-media/{__version__}"}) as http,                 http.stream("GET", url) as resp:
            resp.raise_for_status()
            _check_redirect(str(resp.url))
            data = io.BytesIO()
            for chunk in resp.iter_bytes():
                data.write(chunk)
                if data.tell() > MAX_DOWNLOAD:
                    raise SelfUpdateError("The download is larger than expected")
            content = data.getvalue()
            if hashlib.sha256(content).hexdigest() != sha256:
                raise SelfUpdateError("The download does not match the release's checksum, "
                                      "so it was not installed")
            return content
    except httpx.HTTPError as e:
        raise SelfUpdateError("Could not download the new version") from e


def _wheel_from(tarball: bytes, version: str, folder: Path) -> Path:
    """Write the one wheel in the tarball to `folder` under a name that matched the pattern."""
    try:
        with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
            found = [m for m in tar.getmembers()
                     if m.isfile() and WHEEL.fullmatch(os.path.basename(m.name))]
            if len(found) != 1:
                raise SelfUpdateError("The release does not contain exactly one Horus package")
            member = found[0]
            name = os.path.basename(member.name)
            if WHEEL.fullmatch(name).group(1) != version:  # type: ignore[union-attr]
                raise SelfUpdateError("The package in the release is not the version announced")
            target = folder / name
            source = tar.extractfile(member)
            if source is None:
                raise SelfUpdateError("The release package could not be read")
            target.write_bytes(source.read())
            return target
    except (tarfile.TarError, OSError, EOFError) as e:
        raise SelfUpdateError("The downloaded release could not be unpacked") from e


def apply(latest: str, asset_url: str, asset_sha256: str | None,
          transport: httpx.BaseTransport | None = None) -> None:
    """Back up, download and install `latest`. Raises SelfUpdateError with a safe message.
    Restarting is the caller's job, once the response has been sent."""
    reason = why_not()
    if reason:
        raise SelfUpdateError("This copy cannot update itself: " + reason)
    installer = _installer()
    assert installer is not None  # why_not() checked
    _check_url(asset_url)  # before the backup: an unofficial source is refused with no side effects
    backup.write_full_backup(db_path())
    log.info("update to %s: backup written, downloading", latest)
    tarball = _download(asset_url, asset_sha256, transport)
    with tempfile.TemporaryDirectory() as tmp:
        wheel = _wheel_from(tarball, latest, Path(tmp))
        result = subprocess.run([*installer, str(wheel)], capture_output=True, text=True,
                                timeout=INSTALL_TIMEOUT, check=False)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-5:]
        log.error("update to %s: the install step failed: %s", latest, " | ".join(tail))
        raise SelfUpdateError("The install step failed, so Horus was not restarted. "
                              "See Settings > Logs for the reason.")
    log.info("update to %s installed", latest)
