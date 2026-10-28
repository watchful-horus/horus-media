#!/usr/bin/env python3
"""Call a Horus instance's API with a control key, without ever printing the key.

For testing a copy of Horus from a terminal or a script. Make a control key under
Settings > API > Control keys, then put these in a git-ignored `.env` file (or the environment):

    HORUS_TEST_URL=http://192.168.1.20:8000
    HORUS_TEST_CONTROL_KEY=hk_...

Examples:

    python scripts/horusctl.py GET /api/settings
    python scripts/horusctl.py POST /api/backups
    python scripts/horusctl.py PUT /api/settings --json '{"log_level": "DEBUG"}'
    python scripts/horusctl.py GET /api/logs/download --out horus.log
    python scripts/horusctl.py POST /api/backups/upload --upload horus-backup.zip

The key is sent only in the Authorization header to HORUS_TEST_URL. It is never printed, and
anything that looks like a key is hidden in the output. Only /api/ addresses are allowed.
"""

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

URL_VAR, KEY_VAR = "HORUS_TEST_URL", "HORUS_TEST_CONTROL_KEY"
MAX_SHOWN = 6000  # characters of a response to print
_KEYLIKE = re.compile(r"hk_[A-Za-z0-9_-]{8,}")


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=value lines (no export, no quotes needed). Missing file means no values."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        values[name.strip()] = value.strip().strip("'\"")
    return values


def settings(env_file: Path | None) -> tuple[str, str]:
    """(url, key) from the environment, else from the .env file. Never echoes either."""
    file_values = read_env_file(env_file or Path(".env"))
    url = (os.environ.get(URL_VAR) or file_values.get(URL_VAR) or "").rstrip("/")
    key = os.environ.get(KEY_VAR) or file_values.get(KEY_VAR) or ""
    missing = [n for n, v in ((URL_VAR, url), (KEY_VAR, key)) if not v]
    if missing:
        raise SystemExit("Missing " + " and ".join(missing) + " (set them in the environment or in .env)")
    if not re.fullmatch(r"https?://[^\s/]+(:\d+)?", url):
        raise SystemExit(f"{URL_VAR} must look like http://host:port")
    return url, key


def hide_keys(text: str, key: str = "") -> str:
    text = _KEYLIKE.sub("hk_<hidden>", text)
    return text.replace(key, "<hidden>") if key else text


def call(method: str, url: str, key: str, path: str, body: bytes | None = None,
         content_type: str = "application/json", timeout: float = 60.0) -> tuple[int, bytes, str]:
    """One request. Returns (status, body, content type). Raises SystemExit for a bad address."""
    if not path.startswith("/api/") or " " in path:
        raise SystemExit("The path must start with /api/")
    request = urllib.request.Request(url + path, data=body, method=method.upper())
    request.add_header("Authorization", f"Bearer {key}")
    if body is not None:
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # the URL is http(s), checked in settings()
            return response.status, response.read(), response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")
    except urllib.error.URLError as e:
        raise SystemExit(f"Could not reach {url}: {hide_keys(str(e.reason), key)}") from None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Call a Horus API with a control key (never printed).")
    p.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    p.add_argument("path", help="for example /api/settings")
    p.add_argument("--json", dest="json_body", help="a JSON body to send")
    p.add_argument("--upload", type=Path, help="send this file as the body (for restore uploads)")
    p.add_argument("--out", type=Path, help="save the response body to this file instead of printing it")
    p.add_argument("--env-file", type=Path, help="where to read the settings from (default .env)")
    args = p.parse_args(argv)

    url, key = settings(args.env_file)
    body, content_type = None, "application/json"
    if args.json_body is not None:
        try:
            json.loads(args.json_body)
        except ValueError as e:
            raise SystemExit(f"--json is not valid JSON: {e}") from None
        body = args.json_body.encode("utf-8")
    elif args.upload is not None:
        body, content_type = args.upload.read_bytes(), "application/octet-stream"

    status, data, ctype = call(args.method, url, key, args.path, body, content_type)
    print(f"HTTP {status}")
    if args.out:
        args.out.write_bytes(data)
        print(f"saved {len(data)} bytes to {args.out}")
    elif "json" in ctype or "text" in ctype or not data:
        text = data.decode("utf-8", errors="replace")
        try:
            text = json.dumps(json.loads(text), indent=2) if text else text
        except ValueError:
            pass
        text = hide_keys(text, key)
        print(text[:MAX_SHOWN] + ("\n... (cut)" if len(text) > MAX_SHOWN else ""))
    else:
        print(f"{len(data)} bytes of {ctype or 'binary data'}; use --out FILE to save it")
    return 0 if status < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
