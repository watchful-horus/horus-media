import io

import pytest

from horus_media import __main__ as entry
from horus_media import auth
from horus_media.db import connect, migrate


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "horus.db"
    monkeypatch.setenv("HORUS_DB_PATH", str(path))
    return path


def run(monkeypatch, password: str, username: str = "owner") -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    entry.main(["create-admin", "--username", username])


def test_creates_a_working_admin(db_file, monkeypatch, capsys):
    run(monkeypatch, "correct horse battery")
    conn = connect(db_file)
    try:
        migrate(conn)
        assert auth.admin_exists(conn)
        user = auth.authenticate(conn, "owner", "correct horse battery")
        assert user and user["role"] == "admin"
    finally:
        conn.close()
    out = capsys.readouterr()
    assert "owner" in out.out
    assert "correct horse battery" not in out.out + out.err


def test_refuses_when_an_admin_exists(db_file, monkeypatch):
    run(monkeypatch, "correct horse battery")
    with pytest.raises(SystemExit) as stopped:
        run(monkeypatch, "another long password", username="second")
    assert "already exists" in str(stopped.value)
    conn = connect(db_file)
    try:
        assert [u["username"] for u in auth.list_users(conn)] == ["owner"]
    finally:
        conn.close()


def test_rejects_a_short_password_or_bad_name(db_file, monkeypatch):
    with pytest.raises(SystemExit) as stopped:
        run(monkeypatch, "short")
    assert "at least" in str(stopped.value)
    with pytest.raises(SystemExit):
        run(monkeypatch, "correct horse battery", username="a b")
    conn = connect(db_file)
    try:
        migrate(conn)
        assert not auth.admin_exists(conn)
    finally:
        conn.close()
