"""ffmpeg- ja ffprobe-binäärien etsinnän ja suorituksen testit."""

import os
import sys
from unittest.mock import patch

import pytest

from autoraffkat.audio import binaries


def test_get_binary_path_from_meipass(monkeypatch, tmp_path):
    """MEIPASS:sta (PyInstaller OneFile / Bundled) löytyvä binääri asetetaan etusijalle."""
    fake_meipass = tmp_path / "meipass"
    bin_dir = fake_meipass / "bin"
    bin_dir.mkdir(parents=True)
    fake_ffmpeg = bin_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    fake_ffmpeg.touch()
    fake_ffmpeg.chmod(0o755)

    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    resolved = binaries.get_binary_path("ffmpeg")
    assert resolved == str(fake_ffmpeg)


def test_get_binary_path_from_executable_dir(monkeypatch, tmp_path):
    """Suoritettavan tiedoston vierestä/hakemistosta löytyvä binääri toimii."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    fake_exec = app_dir / "autoraffkat"
    fake_exec.touch()

    fake_ffmpeg = app_dir / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    fake_ffmpeg.touch()
    fake_ffmpeg.chmod(0o755)

    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exec))

    resolved = binaries.get_binary_path("ffmpeg")
    assert resolved == str(fake_ffmpeg)


def test_get_binary_path_fallback_to_path(monkeypatch):
    """Jos pakattua binääriä ei ole, etsitään järjestelmän PATH-muuttujasta."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    with patch("shutil.which", return_value="/usr/local/bin/ffmpeg"):
        resolved = binaries.get_binary_path("ffmpeg")
        assert resolved == "/usr/local/bin/ffmpeg"


def test_get_binary_path_missing_raises_error(monkeypatch):
    """Puuttuva binääri nostaa selkeän FileNotFoundError-virheen."""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    with patch("shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError, match="ffmpeg"):
            binaries.get_binary_path("ffmpeg")


def test_require_ffmpeg_checks_both(monkeypatch):
    """require_ffmpeg tarkistaa sekä ffmpeg- että ffprobe-työkalut."""
    calls = []

    def mock_get(name):
        calls.append(name)
        return f"/mock/{name}"

    monkeypatch.setattr(binaries, "get_binary_path", mock_get)
    binaries.require_ffmpeg()
    assert "ffmpeg" in calls
    assert "ffprobe" in calls
