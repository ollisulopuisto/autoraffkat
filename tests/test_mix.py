"""Äänenkäsittely. automixeria ei ajeta täällä — se on hidas ja valinnainen.

Testattavana on se osa joka voi rikkoa synkan: polkujen johtaminen,
tuoreuden tunnistus ja näytemäärän tarkistus.
"""

import os

import pytest

import numpy as np

from autoraffkat.audio import mix
from autoraffkat.model import HOP, AudioSettings
from conftest import needs_ffmpeg


def test_sibling_is_always_wav():
    assert mix.sibling("/x/host a.wav", mix.MIX_SUFFIX) == "/x/host a [mix].wav"
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
    result = mix.process(
        timeline, resolve_roles(timeline, {}), AudioSettings(enabled=False)
    )
    assert result.replacements == {} and result.room == [] and result.ok


def test_missing_plugin_is_reported_not_raised(fixture_dir):
    """Puuttuva liitännäinen on viesti käyttöliittymään, ei poikkeus.

    Virhe tulee ennen kuin yhtään tiedostoa on käsitelty: minuuttien
    odottaminen ja vasta sitten kaatuminen olisi huonoin vaihtoehto.
    """
    from autoraffkat.analysis import resolve_roles
    from autoraffkat.fcpxml.read import read_fcpxml
    from autoraffkat.model import ROLE_MIC, TrackConfig

    timeline = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    tracks = {"host Track1": TrackConfig(role=ROLE_MIC, speaker="Host")}
    result = mix.process(
        timeline,
        resolve_roles(timeline, tracks),
        AudioSettings(enabled=True, plugin_path="/ei/ole/mitaan.vst3"),
    )
    assert not result.ok
    assert "liitännäistä ei löydy" in " ".join(result.errors.values()).lower()
    assert result.processed == 0


def test_plugins_are_found_by_extension(tmp_path, monkeypatch):
    """Liitännäisluettelo tulee vakiopaikoista, ei mistä tahansa."""
    from autoraffkat.audio import chain

    (tmp_path / "Hieno.vst3").mkdir()
    (tmp_path / "Toinen.component").mkdir()
    (tmp_path / "eiTama.txt").write_text("x")
    monkeypatch.setattr(chain, "PLUGIN_DIRS", (str(tmp_path),))
    found = {p["name"]: p["path"] for p in chain.plugins()}
    assert set(found) == {"Hieno", "Toinen"}
    assert found["Hieno"] == str(tmp_path / "Hieno.vst3")


def test_same_plugin_in_both_formats_is_listed_once(tmp_path, monkeypatch):
    """VST3 ja AU samasta liitännäisestä ovat sama asia valikossa."""
    from autoraffkat.audio import chain

    vst = tmp_path / "vst3"
    au = tmp_path / "components"
    vst.mkdir()
    au.mkdir()
    (vst / "dxRevive.vst3").mkdir()
    (au / "dxRevive.component").mkdir()
    monkeypatch.setattr(chain, "PLUGIN_DIRS", (str(vst), str(au)))
    found = chain.plugins()
    assert len(found) == 1
    assert found[0]["path"].endswith(".vst3")


@needs_ffmpeg
def test_frame_count_matches_the_asset(fixture_dir):
    """Näytemäärä on se luku, jolla synkka tarkistetaan."""
    from make_fixture import DURATION

    path = fixture_dir / "MIC_A.wav"
    if not path.exists():
        pytest.skip("fixturen mediaa ei ole")
    assert mix.frame_count(str(path)) == int(DURATION * 48000)


# ------------------------------------------------- toisen mikin vaimennus


def _grid(on_a, on_b, level_a, level_b, n=500):
    """Kaksi puhujaa ruudukolla, annetuilla maskeilla ja tasoilla."""
    import numpy as np
    from autoraffkat.decide import Grid, SpeakerLanes

    def lane(name, on, level):
        mask = np.zeros(n, dtype=bool)
        db = np.full(n, -60.0, dtype=np.float32)
        for start, end in on:
            mask[start:end] = True
            db[start:end] = level
        return SpeakerLanes(name, db, mask, f"C{name}")

    return Grid(
        n=n,
        program_start=0.0,
        wide_key="W",
        speakers=[lane("A", on_a, level_a), lane("B", on_b, level_b)],
    )


def _quiet_knobs(**kw):
    """Ajat pois päältä, jotta testi mittaa sääntöä eikä liukuja."""
    base = dict(
        duck=True,
        duck_dominance_db=6.0,
        duck_lookahead=0.0,
        duck_hold=0.0,
        duck_min_open=0.0,
        duck_min_closed=0.0,
        duck_release=0.0,
    )
    base.update(kw)
    return AudioSettings(**base)


def test_only_the_loudest_mic_is_ducked():
    """Kaksi mikkiä samassa huoneessa kuulevat molemmat puhujat.

    Kynnys ylittyy siis molemmilla, ja vain tasoero erottaa puhujat. Tämä on
    se kohta joka tekee portista käyttökelpoisen. Maskit ovat «kiinni»-maskeja.
    """
    grid = _grid([(100, 300)], [(100, 300)], level_a=-25.0, level_b=-40.0)
    masks = mix.duck_masks(grid, _quiet_knobs())
    assert not masks["A"][200], "kovempi ei saa vaimentua"
    assert masks["B"][200], "hiljaisemman pitää vaimentua"


def test_genuine_overlap_ducks_neither():
    """Kun tasot ovat lähellä toisiaan, molemmat puhuvat oikeasti."""
    grid = _grid([(100, 300)], [(100, 300)], level_a=-25.0, level_b=-28.0)
    masks = mix.duck_masks(grid, _quiet_knobs())
    assert not masks["A"][200] and not masks["B"][200]


def test_nothing_is_ducked_when_nobody_speaks():
    """Hiljaisuuteen laskeva portti kuuluu aina — sitä ei saa tehdä."""
    grid = _grid([(100, 200)], [(400, 500)], level_a=-25.0, level_b=-25.0)
    masks = mix.duck_masks(grid, _quiet_knobs())
    # Kohdassa 300 kumpikaan ei puhu: kummankaan mikkiä ei vaimenneta.
    assert not masks["A"][300] and not masks["B"][300]
    # Kun A puhuu, B on vaimennettuna — ja päinvastoin.
    assert masks["B"][150] and not masks["A"][150]
    assert masks["A"][450] and not masks["B"][450]


def test_short_ducks_are_dropped():
    """Alle puolen sekunnin kuoppa on naksahdus, ei vaimennus."""
    grid = _grid([(100, 104)], [], level_a=-25.0, level_b=-60.0, n=500)
    masks = mix.duck_masks(grid, _quiet_knobs(duck_min_closed=0.5))
    assert not masks["B"].any()


def test_the_release_finishes_under_the_masking_speech():
    """Nousun on ehdittävä loppuun ennen kuin peittävä ääni loppuu."""
    grid = _grid([(100, 300)], [], level_a=-25.0, level_b=-60.0, n=500)
    masks = mix.duck_masks(grid, _quiet_knobs(duck_release=0.5, duck_min_closed=0.0))
    closed = np.flatnonzero(masks["B"])
    assert closed.size, "B:n pitäisi vaimentua A:n puheen ajaksi"
    # A puhuu indeksiin 300 asti; vaimennuksen on loputtava paluun verran ennen.
    assert closed[-1] <= 300 - int(0.5 / HOP) + 1


def test_ducking_off_produces_no_masks():
    grid = _grid([(100, 300)], [], level_a=-25.0, level_b=-40.0)
    assert mix.duck_masks(grid, AudioSettings(duck=False)) == {}
    assert mix.duck_masks(None, AudioSettings(duck=True)) == {}


def test_closed_ranges_map_timeline_to_file_time(fixture_dir):
    """Ruudukko on aikajanan aikaa, vaimennus tiedoston aikaa."""
    import numpy as np
    from autoraffkat.fcpxml.read import read_fcpxml
    from autoraffkat.model import HOP

    timeline = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    item = timeline.media_by_key()["host a Track1.wav"]
    # Osa A kattaa aikajanan 0–18 s ja tiedoston 0–18 s.
    closed = np.zeros(int(36 / HOP), dtype=bool)
    closed[int(4 / HOP) : int(6 / HOP)] = True  # kiinni 4–6 s
    ranges = mix.closed_ranges(item, closed, 0.0, 48000)
    assert len(ranges) == 1
    start, end = ranges[0]
    assert start == pytest.approx(4 * 48000, abs=48)
    assert end == pytest.approx(6 * 48000, abs=48)


def test_closed_ranges_stay_inside_the_clip(fixture_dir):
    """Esiintymän ulkopuolta ei vaimenneta: siitä ei ole tietoa."""
    import numpy as np
    from autoraffkat.fcpxml.read import read_fcpxml
    from autoraffkat.model import HOP

    timeline = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    item = timeline.media_by_key()["host a Track1.wav"]  # aikajanalla 0–18 s
    closed = np.ones(int(36 / HOP), dtype=bool)  # kaikki kiinni
    ranges = mix.closed_ranges(item, closed, 0.0, 48000)
    assert len(ranges) == 1
    assert ranges[0][1] <= 18 * 48000 + 48
