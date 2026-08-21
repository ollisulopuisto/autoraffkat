"""Pääohjelman käynnistyksen ja komentoriviparametrien testit."""

from unittest.mock import MagicMock, patch

import pytest

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
