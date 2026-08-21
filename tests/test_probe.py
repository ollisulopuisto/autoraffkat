"""Tiedostojen tekniset tiedot. Puuttuva tiedosto ei ole virhe."""

import os

from autoraffkat import probe
from conftest import needs_ffmpeg


def test_missing_file_gives_nothing(tmp_path):
    assert probe.info(str(tmp_path / "ei-ole.wav")) == {}
    assert probe.info("") == {}


def test_fps_is_read_as_a_fraction():
    assert probe._fps("25/1") == 25.0
    assert probe._fps("30000/1001") == 30000 / 1001
    assert probe._fps("0/0") is None
    assert probe._fps(None) is None


@needs_ffmpeg
def test_audio_facts(fixture_dir):
    path = fixture_dir / "MIC_A.wav"
    if not path.exists():
        import pytest
        pytest.skip("fixturen mediaa ei ole")
    facts = probe.info(str(path))
    assert facts["audio"]["channels"] == 1
    assert facts["audio"]["rate"] == 48000
    assert facts["size"] == os.path.getsize(path)
    assert facts["duration"] > 0


@needs_ffmpeg
def test_result_is_cached(fixture_dir):
    """Tila haetaan sekunnin välein; joka haku ei saa ajaa ffprobea."""
    path = fixture_dir / "MIC_A.wav"
    if not path.exists():
        import pytest
        pytest.skip("fixturen mediaa ei ole")
    first = probe.info(str(path))
    assert probe.info(str(path)) is first
