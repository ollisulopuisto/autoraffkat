"""Pikkukuvat. Puolivälistä tiedostoa, välimuistiin, epäonnistuminen sallittu."""

import os

from autoraffkat import thumbs
from conftest import needs_ffmpeg


def test_missing_file_is_not_an_error(tmp_path):
    """Pikkukuva on mukavuus: sen puuttuminen ei saa estää roolitusta."""
    assert thumbs.thumbnail(str(tmp_path / "ei-ole.mp4"), 100.0) is None
    assert thumbs.thumbnail("", 100.0) is None


def test_zero_duration_is_refused(tmp_path):
    path = tmp_path / "tyhja.mp4"
    path.write_bytes(b"")
    assert thumbs.thumbnail(str(path), 0.0) is None


def test_cache_path_follows_the_file(tmp_path):
    """Korvattu tiedosto ei osu vanhaan pikkukuvaan."""
    path = tmp_path / "a.mp4"
    path.write_bytes(b"x" * 10)
    first = thumbs.cache_path(str(path), 50.0)
    path.write_bytes(b"x" * 20)
    os.utime(path, (10**9, 10**9))
    assert thumbs.cache_path(str(path), 50.0) != first
    # Eri kohta samasta tiedostosta on eri pikkukuva.
    assert thumbs.cache_path(str(path), 60.0) != thumbs.cache_path(str(path), 50.0)


def test_audio_only_media_has_no_thumbnail(fixture_dir):
    from autoraffkat.fcpxml.read import read_fcpxml

    timeline = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    mic = timeline.media_by_key()["host a Track1.wav"]
    assert thumbs.for_item(mic) is None


@needs_ffmpeg
def test_thumbnail_is_taken_from_the_middle(fixture_dir):
    """Alku on asettelua ja loppu pakkaamista; puoliväli on kuvausta."""
    from autoraffkat.fcpxml.read import read_fcpxml

    timeline = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    item = timeline.media_by_key()["WIDE 01.mp4"]
    if not os.path.exists(item.path):
        import pytest

        pytest.skip("fixturen mediaa ei ole")
    path = thumbs.for_item(item)
    assert path and os.path.getsize(path) > 0
    assert f"-{int(float(item.asset_duration) / 2)}.jpg" in path
    # Toinen kutsu tulee välimuistista.
    assert thumbs.for_item(item) == path
