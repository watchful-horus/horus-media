from horus_media import auth
from horus_media.db import connect, migrate


def fresh(tmp_path):
    conn = connect(tmp_path / "horus.db")
    migrate(conn)
    return conn


def test_admin_is_created_from_a_password_file(tmp_path, monkeypatch):
    pw = tmp_path / "pw"
    pw.write_text("correct horse battery\n")
    monkeypatch.setenv("HORUS_ADMIN_USER", "owner")
    monkeypatch.setenv("HORUS_ADMIN_PASSWORD_FILE", str(pw))
    conn = fresh(tmp_path)
    assert auth.admin_from_environment(conn) == "owner"
    user = auth.authenticate(conn, "owner", "correct horse battery")
    assert user and user["role"] == "admin"


def test_plain_password_variable_works_too(tmp_path, monkeypatch):
    monkeypatch.setenv("HORUS_ADMIN_USER", "owner")
    monkeypatch.setenv("HORUS_ADMIN_PASSWORD", "correct horse battery")
    conn = fresh(tmp_path)
    assert auth.admin_from_environment(conn) == "owner"


def test_nothing_happens_once_an_admin_exists(tmp_path, monkeypatch):
    conn = fresh(tmp_path)
    auth.create_user(conn, "first", "correct horse battery", "admin")
    monkeypatch.setenv("HORUS_ADMIN_USER", "second")
    monkeypatch.setenv("HORUS_ADMIN_PASSWORD", "another long password")
    assert auth.admin_from_environment(conn) is None
    assert [u["username"] for u in auth.list_users(conn)] == ["first"]


def test_problems_leave_the_first_run_screen_open_and_do_not_log_the_password(
        tmp_path, monkeypatch, caplog):
    conn = fresh(tmp_path)
    monkeypatch.setenv("HORUS_ADMIN_USER", "owner")
    monkeypatch.setenv("HORUS_ADMIN_PASSWORD_FILE", str(tmp_path / "missing"))
    assert auth.admin_from_environment(conn) is None
    monkeypatch.delenv("HORUS_ADMIN_PASSWORD_FILE")
    assert auth.admin_from_environment(conn) is None  # no password at all
    monkeypatch.setenv("HORUS_ADMIN_PASSWORD", "short")
    assert auth.admin_from_environment(conn) is None  # too weak
    assert not auth.admin_exists(conn)
    assert "short" not in caplog.text


def test_starting_horus_writes_what_happened_to_the_log_file(tmp_path, monkeypatch):
    """The account is made before the app exists, so logging has to be on by then."""
    from horus_media import __main__ as entry
    from horus_media import logging_setup

    db_file = tmp_path / "data" / "horus.db"
    db_file.parent.mkdir()
    pw = tmp_path / "pw"
    pw.write_text("correct horse battery\n")
    monkeypatch.setenv("HORUS_DB_PATH", str(db_file))
    monkeypatch.setenv("HORUS_PORT", "8765")
    monkeypatch.setenv("HORUS_ADMIN_USER", "owner")
    monkeypatch.setenv("HORUS_ADMIN_PASSWORD_FILE", str(pw))
    monkeypatch.setattr(entry.listen, "wait_until_free", lambda host, port: True)
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: None)
    entry.serve()
    text = logging_setup.log_path(db_file).read_text(encoding="utf-8")
    assert "Admin account 'owner' created from HORUS_ADMIN_USER" in text
    assert "correct horse battery" not in text

    # A second start with a file that cannot be read says so in the log file as well.
    conn = connect(db_file)
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()
    monkeypatch.setenv("HORUS_ADMIN_PASSWORD_FILE", str(tmp_path / "missing"))
    entry.serve()
    text = logging_setup.log_path(db_file).read_text(encoding="utf-8")
    assert "HORUS_ADMIN_PASSWORD_FILE could not be read" in text
