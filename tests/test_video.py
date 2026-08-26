"""Kuvakerros: välimuisti ja rajaus.

Tunnistin on vaihdettava osa, joten testit käyttävät omaa tynkäänsä. Se ei
ole oikotie vaan sopimuksen tarkistus: jos oikea tunnistin ei kelpaisi
tähän rooliin, sitä ei voisi vaihtaa toiseen.
"""

import os
import subprocess

import numpy as np
import pytest

from autoraffkat.video import analyse, detect, measure

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


class Stub:
    """Tunnistin joka lukee ruudun keskiarvon. Ei kasvoja, ei riippuvuuksia."""

    name = "tynka"
    version = 1
    fields = ("yaw", "smile", "eyes", "size", "cx", "cy")

    def __init__(self, blind_every=0):
        self.blind_every = blind_every
        self.seen = 0

    def measure(self, path):
        self.seen += 1
        if self.blind_every and self.seen % self.blind_every == 0:
            return None
        return {name: float(self.seen) / 100.0 for name in self.fields}


def test_an_unknown_detector_is_an_error_not_a_silent_skip():
    """Kirjoitusvirhe asetuksissa ei saa tarkoittaa «ei reaktioita»."""
    with pytest.raises(detect.DetectError):
        detect.load("ei-tallaista")


def test_the_cache_key_changes_with_the_detector(tmp_path):
    """Vaihdettu tunnistin tuottaa eri sarakkeet eri merkityksillä.

    Ilman tätä uusi tunnistin lukisi edellisen jäljet: kelvollinen tulos,
    hyväksytty ja väärä.
    """
    target = tmp_path / "a.mp4"
    target.write_bytes(b"x" * 10)
    one, two = Stub(), Stub()
    two.name = "toinen"
    assert measure.cache_key(str(target), one) != measure.cache_key(str(target), two)
    three = Stub()
    three.version = 2
    assert measure.cache_key(str(target), one) != measure.cache_key(str(target), three)


@pytest.fixture
def clip(tmp_path):
    """Lyhyt video, jossa avainruutu joka sekunti."""
    if not subprocess.run([FFMPEG, "-version"], capture_output=True).returncode == 0:
        pytest.skip("ffmpeg puuttuu")
    target = tmp_path / "clip.mp4"
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "testsrc2=size=320x180:rate=25:duration=6",
         "-c:v", "libx264", "-g", "25", "-preset", "ultrafast",
         "-pix_fmt", "yuv420p", str(target)],
        check=True, capture_output=True)
    return target


def test_only_keyframes_are_measured(clip):
    """Kuusi sekuntia 25 kuvan nopeudella on 150 ruutua; avainruutuja kuusi.

    Jos tämä palauttaa 150, ``-skip_frame nokey`` on lakannut toimimasta ja
    purku on 25-kertainen — mikä ei kaada mitään, vain hidastaa hiljaa.
    """
    stub = Stub()
    table = measure.measure_file(str(clip), stub)
    assert 5 <= len(table["times"]) <= 8, f"{len(table['times'])} ruutua"
    assert stub.seen == len(table["times"])
    assert np.all(np.diff(table["times"]) > 0.5)


def test_a_frame_without_a_face_stays_in_the_table(clip):
    """Poistaminen siirtäisi indeksit, eikä aikaleimoja voisi enää pariuttaa.

    Rivi jää nolliksi ja ``found`` kertoo totuuden — pisteytys osaa hylätä
    sen, kun taas puuttuva rivi siirtäisi kaiken jälkeensä väärään hetkeen.
    """
    table = measure.measure_file(str(clip), Stub(blind_every=2))
    assert len(table["found"]) == len(table["times"])
    assert not table["found"].all() and table["found"].any()
    assert np.all(table["yaw"][~table["found"]] == 0)


def test_the_cache_returns_the_same_table_without_decoding(clip, monkeypatch, tmp_path):
    monkeypatch.setattr(measure, "cache_dir", lambda: tmp_path)
    first = measure.table(str(clip), Stub())

    def explode(*args, **kwargs):
        raise AssertionError("purettiin uudestaan vaikka välimuisti oli")

    monkeypatch.setattr(measure, "measure_file", explode)
    again = measure.table(str(clip), Stub())
    assert np.array_equal(first["times"], again["times"])
    assert np.array_equal(first["found"], again["found"])


def test_a_broken_cache_file_is_recomputed(clip, monkeypatch, tmp_path):
    """Rikkinäinen välimuisti ei saa olla umpikuja."""
    monkeypatch.setattr(measure, "cache_dir", lambda: tmp_path)
    stub = Stub()
    measure.table(str(clip), stub)
    for junk in tmp_path.glob("*.npz"):
        junk.write_bytes(b"ei ole npz")
    again = measure.table(str(clip), stub)
    assert len(again["times"]) > 0


class _Lane:
    def __init__(self, name, on):
        self.name, self.on = name, np.asarray(on, dtype=bool)


class _Grid:
    def __init__(self, *lanes):
        self.speakers = list(lanes)


class _Item:
    def __init__(self, key, path):
        self.key, self.path, self.has_video = key, path, True


class _Timeline:
    def __init__(self, media):
        self._media = media

    def track_media(self, key):
        return self._media.get(key, [])


class _Roles:
    closes = {"A": "camA", "B": "camB"}


def test_a_speaker_who_never_listens_is_never_decoded():
    """Purku on koko työn hinta, joten rajaus on tehtävä ennen sitä.

    Puhuja joka ei ole kertaakaan vaiti ei voi tuottaa reaktiokuvaa, ja
    hänen kameransa purkaminen olisi minuutteja tyhjää.
    """
    grid = _Grid(_Lane("A", [1, 1, 1]), _Lane("B", [0, 0, 0]))
    timeline = _Timeline({"camA": [_Item("a", "/x/a.mp4")],
                          "camB": [_Item("b", "/x/b.mp4")]})
    picked = analyse.close_up_files(grid, _Roles(), timeline)
    assert [key for _, key, _ in picked] == ["b"]


def test_missing_media_is_reported_not_swallowed(monkeypatch):
    """Levy voi olla irrotettu. Se on tavallista — mutta se on kerrottava."""
    from autoraffkat.model import Globals

    grid = _Grid(_Lane("A", [1, 1]), _Lane("B", [0, 0]))
    timeline = _Timeline({"camB": [_Item("b", "/ei/ole/mitaan.mp4")]})
    monkeypatch.setattr(detect, "load", lambda name: Stub())
    tables, errors = analyse.tables(
        grid, _Roles(), timeline, Globals(reactions=True))
    assert tables == {}
    assert "b" in errors and "mitaan.mp4" in errors["b"]
