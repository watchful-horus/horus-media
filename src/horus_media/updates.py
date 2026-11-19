"""Optional "is there a newer version" check.

Contacts only the address set in Settings (or HORUS_UPDATE_URL): a GitHub-style "latest
release" JSON endpoint with a `tag_name`. It sends no identifying data and installs nothing
itself (the "Update now" button is in self_update). With no URL configured nothing is ever
contacted. The answer is not trusted: the version must look like a version and the release
link must be https, because both are shown in Settings.
"""

import os
import re

import httpx

from . import __version__, self_update

VERSION = re.compile(r"v?(\d+(?:\.\d+){1,3})")


class UpdateCheckError(Exception):
    """Message is safe to show."""


def effective_url(saved: str = "") -> str:
    """HORUS_UPDATE_URL wins; otherwise the address saved in Settings (may be empty)."""
    return os.environ.get("HORUS_UPDATE_URL", "").strip() or (saved or "").strip()


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text.split("-")[0])) or (0,)


def check(url: str, transport: httpx.BaseTransport | None = None) -> dict:
    if not url:
        raise UpdateCheckError("No update source is configured")
    try:
        with httpx.Client(timeout=10.0, transport=transport, follow_redirects=True,
                          headers={"User-Agent": f"horus-media/{__version__}"}) as http:
            resp = http.get(url)
            if resp.status_code == 429 or (
                    resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"):
                # GitHub allows 60 requests an hour without a login, counted per public address,
                # so several copies of Horus (or other tools) at one home share them.
                raise UpdateCheckError("The release address is limiting requests from your "
                                       "network right now. Try again in an hour")
            resp.raise_for_status()
            body = resp.json()
        if not isinstance(body, dict):
            raise TypeError("not a release document")
        tag = VERSION.fullmatch(str(body["tag_name"]).strip())
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise UpdateCheckError("Could not check for updates") from e
    if tag is None:
        raise UpdateCheckError("The release address gave a version Horus does not understand")
    latest = tag.group(1)
    link = body.get("html_url")
    asset_url, asset_sha256 = self_update.asset_for(body, latest) or (None, None)
    return {
        "current": __version__,
        "latest": latest,
        "newer": parse_version(latest) > parse_version(__version__),
        "release_url": link if isinstance(link, str) and link.startswith("https://") else None,
        "asset_url": asset_url,
        "asset_sha256": asset_sha256,
    }
