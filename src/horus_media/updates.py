"""Optional "is there a newer version" check.

Contacts only the address set in Settings (or HORUS_UPDATE_URL): a GitHub-style "latest
release" JSON endpoint with a `tag_name`. It sends no identifying data and never installs anything. With no URL configured
(as before the project is published) nothing is ever contacted.
"""

import os
import re

import httpx

from . import __version__


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
            resp.raise_for_status()
            body = resp.json()
        latest = str(body["tag_name"]).lstrip("v")
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise UpdateCheckError("Could not check for updates") from e
    return {
        "current": __version__,
        "latest": latest,
        "newer": parse_version(latest) > parse_version(__version__),
        "release_url": body.get("html_url") if isinstance(body, dict) else None,
    }
