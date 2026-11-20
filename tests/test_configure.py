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


# ---- the `horus-media` command ----

def test_the_command_is_declared_and_points_at_the_same_code():
    import tomllib
    from importlib import import_module
    from pathlib import Path

    project = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text("utf-8"))
    scripts = project["project"]["scripts"]
    assert list(scripts) == ["horus-media"]  # never `horus`: another package installs that
    module, name = scripts["horus-media"].split(":")
    assert getattr(import_module(module), name) is entry.cli


def test_the_command_takes_its_arguments_from_the_command_line(db_file, monkeypatch):
    monkeypatch.setattr("sys.argv", ["horus-media", "configure", "--port", "8123"])
    entry.cli()
    assert saved(db_file).http_port == 8123


def test_help_names_the_form_that_was_typed(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["horus-media", "--help"])
    with pytest.raises(SystemExit):
        entry.cli()
    assert capsys.readouterr().out.startswith("usage: horus-media ")
    with pytest.raises(SystemExit):
        entry.main(["--help"])
    assert capsys.readouterr().out.startswith("usage: python -m horus_media ")
