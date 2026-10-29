import os
import socket

import pytest
from fastapi.testclient import TestClient

from horus_media import __main__ as entry
from horus_media import auth, listen, routes_settings
from horus_media.db import connect, migrate
from horus_media.main import create_app


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    for name in ("HORUS_PORT", "HORUS_HOST", "HORUS_RUNNING_PORT", "HORUS_RUNNING_HOST",
                 "HORUS_PORT_SOURCE", "HORUS_HOST_SOURCE"):
        monkeypatch.delenv(name, raising=False)
    # A browser connected to Horus on port 8000, as a real one would be.
    return TestClient(create_app(), base_url="http://127.0.0.1:8000"), tmp_path


def started_via_module(monkeypatch, port=8000, host="127.0.0.1", source="setting"):
    monkeypatch.setenv("HORUS_RUNNING_PORT", str(port))
    monkeypatch.setenv("HORUS_RUNNING_HOST", host)
    monkeypatch.setenv("HORUS_PORT_SOURCE", source)
    monkeypatch.setenv("HORUS_HOST_SOURCE", source)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def info(client):
    return client.get("/api/settings").json()["info"]["listen"]


# --- what the page shows --------------------------------------------------------------------

def test_started_with_uvicorn_can_still_change_the_port(api):
    client, _ = api
    shown = info(client)
    assert shown["port_source"] == "command" and shown["port_editable"] is True
    assert shown["restart_needed"] is False
    port = free_port()
    assert client.put("/api/settings", json={"http_port": port}).status_code == 200
    assert info(client)["restart_needed"] is True  # applies when Horus restarts via python -m
    # Putting it back means nothing needs restarting any more.
    assert client.put("/api/settings", json={"http_port": 8000}).status_code == 200
    assert info(client)["restart_needed"] is False
    # Saving the form without touching the port is fine.
    assert client.put("/api/settings", json={"http_port": 8000, "log_level": "INFO"}).status_code == 200


def test_changing_the_port_when_started_with_python_m(api, monkeypatch):
    client, _ = api
    started_via_module(monkeypatch)
    assert info(client) | {} == info(client)
    assert info(client)["restart_needed"] is False
    port = free_port()
    assert client.put("/api/settings", json={"http_port": port}).status_code == 200
    assert client.get("/api/settings").json()["settings"]["http_port"] == port
    assert info(client)["restart_needed"] is True and info(client)["port_running"] == 8000


def test_bad_ports_are_refused(api, monkeypatch):
    client, _ = api
    started_via_module(monkeypatch)
    for bad in (80, 70000, "eighty", None, -1):
        assert client.put("/api/settings", json={"http_port": bad}).status_code == 422, bad
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        taken = busy.getsockname()[1]
        r = client.put("/api/settings", json={"http_port": taken})
    assert r.status_code == 422 and "already in use" in r.json()["detail"]
    assert client.get("/api/settings").json()["settings"]["http_port"] == 8000  # nothing saved


def test_environment_variable_wins_and_locks_the_setting(api, monkeypatch):
    client, _ = api
    started_via_module(monkeypatch, source="environment")
    assert info(client)["port_source"] == "environment"
    assert client.put("/api/settings", json={"http_port": free_port()}).status_code == 409
    assert client.put("/api/settings", json={"listen_all": True}).status_code == 409


# --- other computers ------------------------------------------------------------------------

def test_network_access_needs_an_admin_login_first(api, monkeypatch):
    client, tmp = api
    started_via_module(monkeypatch)
    # (Without an admin the settings are unreachable anyway, and startup refuses to open the
    # network until one exists: see test_resolve_precedence_and_safe_fallback.)
    conn = connect(tmp / "horus.db")
    migrate(conn)
    auth.create_user(conn, "admin", "a long enough password", "admin")
    conn.close()
    assert client.put("/api/settings", json={"listen_all": True}).status_code == 200
    assert info(client)["restart_needed"] is True
    assert client.put("/api/settings", json={"listen_all": False}).status_code == 200
    assert info(client)["restart_needed"] is False  # back to what is running


def test_resolve_precedence_and_safe_fallback(api, monkeypatch):
    _, tmp = api
    conn = connect(tmp / "horus.db")
    migrate(conn)
    where = listen.resolve(conn)
    assert (where.host, where.port, where.port_source) == ("127.0.0.1", 8000, "setting")

    from horus_media import app_settings
    app_settings.update(conn, {"listen_all": True, "http_port": 9123})
    where = listen.resolve(conn)
    assert where.host == "127.0.0.1" and where.port == 9123  # no admin yet: stays closed
    auth.create_user(conn, "admin", "a long enough password", "admin")
    assert listen.resolve(conn).host == "0.0.0.0"

    monkeypatch.setenv("HORUS_PORT", "9555")
    monkeypatch.setenv("HORUS_HOST", "127.0.0.1")
    where = listen.resolve(conn)
    assert (where.host, where.port, where.port_source, where.host_source) == (
        "127.0.0.1", 9555, "environment", "environment")
    conn.close()


def test_entry_point_starts_uvicorn_with_the_saved_settings(api, monkeypatch):
    _, tmp = api
    conn = connect(tmp / "horus.db")
    migrate(conn)
    from horus_media import app_settings
    app_settings.update(conn, {"http_port": 9234})
    conn.close()
    calls = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    entry.main()
    assert calls == [("horus_media.main:app", {"host": "127.0.0.1", "port": 9234})]
    assert listen.running()["port"] == 9234 and listen.running()["can_restart"]
    monkeypatch.delenv("HORUS_RUNNING_PORT")


def test_entry_point_stops_with_a_clear_message_when_the_port_is_taken(api, monkeypatch):
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        monkeypatch.setenv("HORUS_PORT", str(busy.getsockname()[1]))
        monkeypatch.setattr(entry.listen, "wait_until_free", lambda *a, **k: False)
        with pytest.raises(SystemExit) as stop:
            entry.main()
    assert "already in use" in str(stop.value)


# --- restart --------------------------------------------------------------------------------

def test_restart_and_shutdown(api, monkeypatch):
    client, _ = api
    fired = []

    class Immediate:
        def __init__(self, delay, fn):
            self.fn = fn

        def start(self):
            self.fn()

    monkeypatch.setattr(routes_settings.threading, "Timer", Immediate)
    monkeypatch.setattr(routes_settings, "_restart_process", lambda: fired.append("restart"))
    monkeypatch.setattr(routes_settings, "_shutdown_process", lambda: fired.append("stop"))
    # Not in a container, even if these tests themselves run inside one (Docker has /.dockerenv).
    monkeypatch.setattr(listen, "in_container", lambda: False)

    # Works however Horus was started (here: not via python -m, like a plain uvicorn start).
    assert info(client)["can_restart"] and info(client)["can_shutdown"]
    assert client.post("/api/restart").json() == {"restarting": True}
    assert client.post("/api/shutdown").json() == {"stopping": True}
    assert fired == ["restart", "stop"]


def test_containers_own_start_and_stop(api, monkeypatch):
    client, _ = api
    monkeypatch.setenv("HORUS_CONTAINER", "1")
    assert info(client)["in_container"] and not info(client)["can_restart"]
    for path in ("/api/restart", "/api/shutdown"):
        r = client.post(path)
        assert r.status_code == 409 and "container" in r.json()["detail"]


def test_shutdown_needs_an_admin(tmp_path, monkeypatch):
    monkeypatch.delenv("HORUS_AUTH_DISABLED")
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    c = TestClient(create_app())
    assert c.post("/api/shutdown").status_code == 401


def test_restart_needs_a_login(tmp_path, monkeypatch):
    monkeypatch.delenv("HORUS_AUTH_DISABLED")
    monkeypatch.setenv("HORUS_DB_PATH", str(tmp_path / "horus.db"))
    assert TestClient(create_app()).post("/api/restart").status_code == 401


def test_port_with_lingering_closed_connections_counts_as_free():
    """After a restart the old process's closed connections linger; the port is still free."""
    server = socket.socket()
    if os.name != "nt":
        # Like uvicorn's listener. On Linux a lingering connection only lets the port be bound
        # again when the socket that made it had this option too.
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port))
    conn, _ = server.accept()
    conn.close()  # the server side closes first, which leaves it in TIME_WAIT
    client.close()
    server.close()
    assert listen.port_is_free("127.0.0.1", port)
    holder = socket.socket()
    holder.bind(("127.0.0.1", 0))
    holder.listen()
    try:
        assert not listen.port_is_free("127.0.0.1", holder.getsockname()[1])
    finally:
        holder.close()


def test_installers_can_open_the_first_run_screen_to_the_network(api, monkeypatch):
    _, tmp = api
    conn = connect(tmp / "horus.db")
    migrate(conn)
    from horus_media import app_settings
    app_settings.update(conn, {"listen_all": True})
    assert listen.resolve(conn).host == "127.0.0.1"  # the safe default: no admin, stays closed
    monkeypatch.setenv("HORUS_OPEN_FIRST_RUN", "1")  # set by the Proxmox installers
    assert listen.resolve(conn).host == "0.0.0.0"
    app_settings.update(conn, {"listen_all": False})
    assert listen.resolve(conn).host == "127.0.0.1"  # still needs 'other computers may connect'


def test_a_container_can_allow_restart_but_never_shutdown_or_a_new_port(api, monkeypatch):
    client, _ = api
    fired = []

    class Immediate:
        def __init__(self, delay, fn):
            self.fn = fn

        def start(self):
            self.fn()

    monkeypatch.setattr(routes_settings.threading, "Timer", Immediate)
    monkeypatch.setattr(routes_settings, "_restart_process", lambda: fired.append("restart"))
    monkeypatch.setenv("HORUS_CONTAINER", "1")
    monkeypatch.setenv("HORUS_ALLOW_SELF_RESTART", "1")
    got = info(client)
    assert got["in_container"] and got["can_restart"] and got["self_restart"]
    assert not got["can_shutdown"]
    assert client.post("/api/restart").json() == {"restarting": True}
    assert fired == ["restart"]
    assert client.post("/api/shutdown").status_code == 409


def test_self_restart_setting_does_nothing_outside_a_container(api, monkeypatch):
    client, _ = api
    monkeypatch.setattr(listen, "in_container", lambda: False)
    monkeypatch.setenv("HORUS_ALLOW_SELF_RESTART", "1")
    assert info(client)["self_restart"] is False
