"""Äänenkäsittely. automixeria ei ajeta täällä — se on hidas ja valinnainen.

Testattavana on se osa joka voi rikkoa synkan: polkujen johtaminen,
tuoreuden tunnistus ja näytemäärän tarkistus.
"""

import os
from pathlib import Path

import pytest

from autoraffkat.audio import mix
from autoraffkat.model import AudioSettings
from conftest import needs_ffmpeg


def test_sibling_is_always_wav():
    assert mix.sibling("/x/nyman a.wav", mix.MIX_SUFFIX) == "/x/nyman a [mix].wav"
    # Myös mp4:stä tulee WAV: purettu ääni ei mene takaisin säiliöön.
    assert mix.sibling("/x/CAM 1.mp4", mix.ROOM_SUFFIX) == "/x/CAM 1 [room].wav"


def test_original_is_never_the_target():
    """Alkuperäiseen ei kosketa, ja se näkyy jo polusta."""
    for suffix in (mix.MIX_SUFFIX, mix.ROOM_SUFFIX):
        source = "/x/mic.wav"
        assert mix.sibling(source, suffix) != source


def test_is_current_follows_modification_time(tmp_path):
    source = tmp_path / "a.wav"
    target = tmp_path / "a [mix].wav"
    source.write_bytes(b"x")
    assert not mix.is_current(str(source), str(target))
    target.write_bytes(b"y")
    assert mix.is_current(str(source), str(target))
    # Lähteen muuttuminen vanhentaa käsittelyn.
    os.utime(source, (10**9, 10**9))
    os.utime(target, (10**9 - 100, 10**9 - 100))
    assert not mix.is_current(str(source), str(target))


def test_readable_formats_pass_through(tmp_path):
    """WAV kelpaa sellaisenaan; purkuun ei mennä turhaan."""
    source = tmp_path / "a.wav"
    source.write_bytes(b"RIFF")
    assert mix.ensure_readable(str(source)) == str(source)


def test_disabled_does_nothing(fixture_dir):
    from autoraffkat.analysis import resolve_roles
    from autoraffkat.fcpxml.read import read_fcpxml
    timeline = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    result = mix.process(timeline, resolve_roles(timeline, {}),
                         AudioSettings(enabled=False))
    assert result.replacements == {} and result.room == [] and result.ok


def test_missing_automixer_is_reported_not_raised(fixture_dir, monkeypatch):
    """Puuttuva automixer on viesti käyttöliittymään, ei poikkeus."""
    from autoraffkat.analysis import resolve_roles
    from autoraffkat.fcpxml.read import read_fcpxml
    from autoraffkat.model import ROLE_MIC, TrackConfig

    monkeypatch.setattr(mix, "automixer_path", lambda: "")
    timeline = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    tracks = {"olli Track1": TrackConfig(role=ROLE_MIC, speaker="Olli")}
    result = mix.process(timeline, resolve_roles(timeline, tracks),
                         AudioSettings(enabled=True))
    assert not result.ok
    assert "automixer" in " ".join(result.errors.values()).lower()


def test_automixer_path_rejects_a_stranger(tmp_path, monkeypatch):
    """Väärä hakemisto on pahempi kuin ei hakemistoa."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "jokumuu"\n')
    monkeypatch.setenv(mix.ENV_VAR, str(tmp_path))
    assert mix.automixer_path() == ""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "automixer"\n')
    assert mix.automixer_path() == str(tmp_path)


@needs_ffmpeg
def test_frame_count_matches_the_asset(fixture_dir):
    """Näytemäärä on se luku, jolla synkka tarkistetaan."""
    from make_fixture import DURATION
    path = fixture_dir / "MIC_A.wav"
    if not path.exists():
        pytest.skip("fixturen mediaa ei ole")
    assert mix.frame_count(str(path)) == int(DURATION * 48000)
