"""Pääohjelman käynnistyksen ja komentoriviparametrien testit."""

import subprocess
import sys
from unittest.mock import MagicMock

from autoraffkat import __main__


def test_main_gui_mode_called(monkeypatch):
    """Oletuksena tai --gui-lipulla kutsutaan gui.launch_gui."""
    mock_launch = MagicMock()
    monkeypatch.setattr("autoraffkat.gui.launch_gui", mock_launch)
    monkeypatch.setattr("autoraffkat.pick.pick", lambda here: None)

    ret = __main__.main(["--gui"])
    assert ret == 0
    mock_launch.assert_called_once()


def test_main_headless_mode(monkeypatch, scratch_xml):
    """--no-gui tai --headless ajaa uvicorn.run komentorivillä."""
    mock_run = MagicMock()
    monkeypatch.setattr("uvicorn.run", mock_run)
    xml = str(scratch_xml("multicam.fcpxml"))

    ret = __main__.main([xml, "--no-gui", "--no-browser"])
    assert ret == 0
    mock_run.assert_called_once()


def test_main_direct_script_execution():
    """__main__.py voidaan ajaa suoraan skriptinä (kuten PyInstaller tekee)."""
    res = subprocess.run(
        [sys.executable, "src/autoraffkat/__main__.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "autoraffkat" in res.stdout
