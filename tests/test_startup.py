from pathlib import Path

import pytest

from horus_media import __main__ as entry
from horus_media import startup


class FakeReg:
    """Just enough of winreg, keeping the Run key's values in a dict."""

    HKEY_CURRENT_USER, KEY_SET_VALUE, KEY_READ, REG_SZ = "HKCU", 2, 1, 1

    def __init__(self):
        self.values: dict[str, str] = {}

    def OpenKey(self, root, path, reserved, access):
        assert root == "HKCU" and path == startup.RUN_KEY
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def QueryValueEx(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        return self.values[name], 1

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError
        del self.values[name]


@pytest.fixture
def reg(monkeypatch):
    fake = FakeReg()
    monkeypatch.setattr(startup, "_registry", lambda: fake)
    return fake


def test_install_status_remove(reg, tmp_path):
    assert startup.status() is None
    cmd = startup.install(tmp_path / "data" / "horus.db")
    assert startup.status() == cmd and reg.values == {"Horus": cmd}
    assert "-m horus_media" in cmd and str((tmp_path / "data" / "horus.db").resolve()) in cmd
    assert startup.remove() is True
    assert startup.status() is None and startup.remove() is False


def test_command_prefers_the_windowless_python(tmp_path):
    (tmp_path / "python.exe").write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    cmd = startup.command(tmp_path / "h.db", python=str(tmp_path / "python.exe"))
    assert cmd.startswith(f'"{tmp_path / "pythonw.exe"}"')


def test_not_windows_gives_a_clear_message(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "winreg", None)
    with pytest.raises(startup.StartupError, match="Windows"):
        startup.status()


def test_cli_install_and_remove(reg, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("HORUS_DB_PATH", raising=False)
    entry.main(["--db", str(tmp_path / "x.db"), "startup", "install"])
    assert str(Path(tmp_path / "x.db").resolve()) in reg.values["Horus"]
    entry.main(["startup", "status"])
    entry.main(["startup", "remove"])
    out = capsys.readouterr().out
    assert "will start when you sign in" in out and "no longer start" in out
    monkeypatch.delenv("HORUS_DB_PATH", raising=False)
