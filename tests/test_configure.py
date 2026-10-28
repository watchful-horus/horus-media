import pytest

from horus_media import __main__ as entry
from horus_media import app_settings
from horus_media.db import connect, migrate


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    path = tmp_path / "horus.db"
    monkeypatch.setenv("HORUS_DB_PATH", str(path))
    return path


def saved(path):
    conn = connect(path)
    try:
        migrate(conn)
        return app_settings.load(conn)
    finally:
        conn.close()


def test_saves_port_and_network(db_file):
    entry.main(["configure", "--port", "8033", "--listen-all"])
    cfg = saved(db_file)
    assert (cfg.http_port, cfg.listen_all) == (8033, True)


def test_without_listen_all_it_stays_local(db_file):
    entry.main(["configure", "--port", "9000"])
    assert saved(db_file).listen_all is False


def test_refuses_a_port_out_of_range(db_file):
    with pytest.raises(SystemExit):
        entry.main(["configure", "--port", "80"])
