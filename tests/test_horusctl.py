"""scripts/horusctl.py: calls a Horus API with a control key and never prints the key."""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "horusctl.py"
KEY = "hk_" + "A1b2C3d4E5f6G7h8I9j0" * 2


@pytest.fixture(scope="module")
def ctl():
    spec = importlib.util.spec_from_file_location("horusctl", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server():
    """A tiny stand-in for Horus that records what it was sent, and echoes the key in its reply
    (to prove the script hides it)."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen.append({"method": self.command, "path": self.path,
                         "auth": self.headers.get("Authorization"), "body": self.rfile.read(length)})
            if self.path == "/api/denied":
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail": "no"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True, "echo": self.headers.get("Authorization")}).encode())

        do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _reply

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", seen
    httpd.shutdown()


def env_file(tmp_path, url):
    f = tmp_path / ".env"
    f.write_text(f"# test\nHORUS_TEST_URL={url}\nHORUS_TEST_CONTROL_KEY='{KEY}'\nOTHER=1\n", encoding="utf-8")
    return f


def test_sends_the_key_only_as_a_bearer_header_and_never_prints_it(ctl, server, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HORUS_TEST_URL", raising=False)
    monkeypatch.delenv("HORUS_TEST_CONTROL_KEY", raising=False)
    url, seen = server
    code = ctl.main(["GET", "/api/settings", "--env-file", str(env_file(tmp_path, url))])
    out = capsys.readouterr()
    assert code == 0 and "HTTP 200" in out.out
    assert seen[0]["auth"] == f"Bearer {KEY}" and seen[0]["path"] == "/api/settings"
    assert KEY not in out.out + out.err  # the server echoed it back; the script hid it
    assert "<hidden>" in out.out


def test_sends_json_and_uploads_and_saves_downloads(ctl, server, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("HORUS_TEST_URL", raising=False)
    monkeypatch.delenv("HORUS_TEST_CONTROL_KEY", raising=False)
    url, seen = server
    env = ["--env-file", str(env_file(tmp_path, url))]
    assert ctl.main(["PUT", "/api/settings", "--json", '{"log_level": "DEBUG"}', *env]) == 0
    assert json.loads(seen[-1]["body"]) == {"log_level": "DEBUG"} and seen[-1]["method"] == "PUT"
    upload = tmp_path / "backup.zip"
    upload.write_bytes(b"PK-not-really")
    assert ctl.main(["POST", "/api/backups/upload", "--upload", str(upload), *env]) == 0
    assert seen[-1]["body"] == b"PK-not-really"
    saved = tmp_path / "out.json"
    assert ctl.main(["GET", "/api/logs/download", "--out", str(saved), *env]) == 0
    assert KEY not in capsys.readouterr().out and saved.is_file()


def test_a_refusal_gives_a_nonzero_exit_code(ctl, server, tmp_path, monkeypatch):
    monkeypatch.delenv("HORUS_TEST_URL", raising=False)
    monkeypatch.delenv("HORUS_TEST_CONTROL_KEY", raising=False)
    url, _ = server
    assert ctl.main(["GET", "/api/denied", "--env-file", str(env_file(tmp_path, url))]) == 1


def test_refuses_addresses_outside_the_api_and_bad_settings(ctl, tmp_path, monkeypatch):
    monkeypatch.delenv("HORUS_TEST_URL", raising=False)
    monkeypatch.delenv("HORUS_TEST_CONTROL_KEY", raising=False)
    with pytest.raises(SystemExit, match="/api/"):
        ctl.call("GET", "http://127.0.0.1:1", KEY, "/metrics")
    with pytest.raises(SystemExit, match="Missing"):
        ctl.settings(tmp_path / "nothing.env")
    bad = tmp_path / ".env"
    bad.write_text(f"HORUS_TEST_URL=ftp://x\nHORUS_TEST_CONTROL_KEY={KEY}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="http://host:port"):
        ctl.settings(bad)


def test_an_unreachable_host_does_not_leak_the_key(ctl):
    with pytest.raises(SystemExit) as stopped:
        ctl.call("GET", "http://127.0.0.1:9", KEY, "/api/settings", timeout=2)
    assert KEY not in str(stopped.value)


def test_hide_keys(ctl):
    assert ctl.hide_keys(f"a {KEY} b") == "a hk_<hidden> b"
    assert ctl.hide_keys("plain text") == "plain text"
