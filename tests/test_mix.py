"""Äänenkäsittely. automixeria ei ajeta täällä — se on hidas ja valinnainen.

Testattavana on se osa joka voi rikkoa synkan: polkujen johtaminen,
tuoreuden tunnistus ja näytemäärän tarkistus.
"""

import os
import pathlib
import time

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


def test_adopt_takes_the_processed_files_already_on_disk(
    fixture_dir, monkeypatch, tmp_path
):
    """Käsittely on kerran tehty työ; nappi ei saa olla sen ehto.

    Ilman tätä sama jakso uudestaan avattuna vietäisiin raakana, vaikka
    valmis ``[mix]`` on lähteen vieressä.
    """
    from autoraffkat.fcpxml.read import read_fcpxml
    from autoraffkat.model import ROLE_MIC, TrackConfig
    from autoraffkat.analysis import resolve_roles

    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    tracks = {
        t.key: TrackConfig(role=ROLE_MIC, speaker=t.key.split()[0].capitalize())
        for t in tl.tracks
        if not t.has_video
    }
    roles = resolve_roles(tl, tracks)
    settings = AudioSettings(enabled=True)
    monkeypatch.setattr(mix, "stamp_dir", lambda: tmp_path)

    # Mitään ei ole vielä levyllä.
    assert not mix.adopt(tl, roles, settings).replacements

    # Jäljet siivotaan: fixture on istunnon mittainen ja jaettu.
    stubs = [
        pathlib.Path(mix.sibling(item.path, mix.MIX_SUFFIX))
        for item in tl.media
        if item.path and item.path.endswith(".wav")
    ]
    try:
        for stub in stubs:
            stub.write_bytes(b"x")
        # Ilman merkintää tiedoston syntyhistoriaa ei tiedetä, eikä sitä
        # oteta: se voi olla mistä tahansa asetuksista.
        assert not mix.adopt(tl, roles, settings).replacements
        for job in mix._jobs(tl, roles, settings):
            mix.write_stamp(job, settings)
        found = mix.adopt(tl, roles, settings)
        assert found.replacements
        assert found.skipped == len(found.replacements)
        assert all(p.endswith(" [mix].wav") for p in found.replacements.values())

        # Pois kytkettynä ei mitään: vienti ei saa poiketa ruudusta.
        assert not mix.adopt(tl, roles, AudioSettings(enabled=False)).replacements
    finally:
        for stub in stubs:
            stub.unlink(missing_ok=True)


def test_weight_follows_file_size(tmp_path):
    """Yhtä suuriksi oletetut tiedostot antavat väärän arvion.

    Samassa jaksossa on 20 minuutin ja 64 minuutin tiedosto, joten «2/4» ei
    kerro mistään.
    """
    small = tmp_path / "a.wav"
    big = tmp_path / "b.wav"
    small.write_bytes(b"x" * 100)
    big.write_bytes(b"x" * 400)
    assert mix.weight_of(str(big)) == 4 * mix.weight_of(str(small))
    # Puuttuva tiedosto ei saa kaataa eikä nollata jakajaa.
    assert mix.weight_of(str(tmp_path / "ei-ole.wav")) > 0


def test_eta_exists_before_the_first_file_is_done():
    """Arvio ensimmäisestä vaiheesta, ei ensimmäisestä tiedostosta.

    Vanha arvio laskettiin valmiista tiedostoista, joten se oli nolla koko
    ensimmäisen — mahdollisesti kymmenen minuutin — tiedoston ajan.
    """
    started = time.perf_counter() - 10.0
    # 20 % tehty kymmenessä sekunnissa -> noin 40 s jäljellä.
    assert 35 < mix._eta(started, 0.2) < 45
    # Nollaosuudella ei ole mitään mistä arvioida.
    assert mix._eta(started, 0.0) == 0.0


def test_progress_reports_stages_and_a_rising_fraction(fixture_dir, monkeypatch):
    """Palkki ei saa seisoa yhden tiedoston ajan paikallaan.

    Liitännäinen käsittelee tiedoston yhtenä palana eikä kerro itsestään
    mitään, joten vaihe on se tarkkuus joka edistymisestä on saatavissa.
    """
    from autoraffkat.analysis import resolve_roles
    from autoraffkat.fcpxml.read import read_fcpxml
    from autoraffkat.model import ROLE_MIC, TrackConfig

    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    tracks = {
        t.key: TrackConfig(role=ROLE_MIC, speaker=t.key.split()[0].capitalize())
        for t in tl.tracks
        if not t.has_video
    }
    seen: list[dict] = []
    monkeypatch.setattr(mix.chain, "load_plugin", lambda *a, **k: None)
    try:
        result = mix.process(
            tl,
            resolve_roles(tl, tracks),
            AudioSettings(enabled=True, plugin_path=""),
            progress=seen.append,
        )
    finally:
        # Fixture on istunnon mittainen ja jaettu: valmis [mix] näyttäisi
        # seuraaville testeille siltä että käsittely on jo tehty.
        for item in tl.media:
            if item.path and item.path.endswith(".wav"):
                pathlib.Path(mix.sibling(item.path, mix.MIX_SUFFIX)).unlink(
                    missing_ok=True
                )
    assert result.ok, result.errors
    assert result.processed

    stages = [s["stage"] for s in seen if s["stage"]]
    assert "read" in stages and "write" in stages
    # Osuus kasvaa monotonisesti ja päätyy täyteen: puolivalmis palkki
    # jälkeenpäin olisi pahempi kuin ei palkkia ollenkaan.
    fractions = [s["fraction"] for s in seen]
    assert fractions == sorted(fractions)
    assert fractions[-1] == 1.0
    # Yhden tiedoston sisällä liikutaan: muuten «2/4» olisi kaikki mitä on.
    within = {s["fraction"] for s in seen if s["done"] == 0}
    assert len(within) > 2


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


def test_fingerprint_covers_every_setting():
    """Uusi säädin ei saa jäädä pois tuoreuden tarkistuksesta.

    Jos jää, sen muuttaminen ei vanhenna mitään ja käsittely palaa hiljaa
    tekemättä mitään — juuri se vika joka sai painikkeen näyttämään
    rikkinäiseltä.
    """
    fields = set(AudioSettings.__dataclass_fields__)
    # `enabled` ja `room_track` päättävät tehdäänkö työtä, eivät miltä
    # tulos kuulostaa: ne eivät kuulu sormenjälkeen.
    assert fields - {"enabled", "room_track"} == set(mix.FINGERPRINT_FIELDS)


def test_every_setting_changes_the_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setattr(mix, "stamp_dir", lambda: tmp_path)
    source = tmp_path / "a.wav"
    source.write_bytes(b"x")
    job = {
        "source": str(source),
        "target": str(tmp_path / "a [mix].wav"),
        "target_lufs": -20.0,
        "gain_db": 0.0,
        "speech": True,
        "weight": 1.0,
    }
    base = AudioSettings()
    before = mix.fingerprint(job, base)
    for name in mix.FINGERPRINT_FIELDS:
        value = getattr(base, name)
        if isinstance(value, bool):
            other = not value
        elif isinstance(value, (int, float)):
            other = value + 1.5
        elif isinstance(value, dict):
            other = {"Input Gain": 3.0}
        else:
            other = "/tmp/toinen.vst3"
        changed = AudioSettings(**{**base.to_json(), name: other})
        assert mix.fingerprint(job, changed) != before, name


def test_is_fresh_notices_a_settings_change(tmp_path, monkeypatch):
    """Muokkausaika ei tiedä liitännäisen vaihdosta mitään.

    Tämä on se vika: käsitelty tiedosto oli lähdettä tuoreempi, joten ajo
    ohitti sen — vaikka se oli tehty toisilla asetuksilla. Painike ei
    tulostanut mitään eikä kirjoittanut mitään, ja näytti rikkinäiseltä.
    """
    monkeypatch.setattr(mix, "stamp_dir", lambda: tmp_path)
    source = tmp_path / "a.wav"
    target = tmp_path / "a [mix].wav"
    source.write_bytes(b"x")
    target.write_bytes(b"y")
    job = {"source": str(source), "target": str(target), "target_lufs": -20.0}
    settings = AudioSettings(target_lufs=-20.0)

    # Merkintää ei ole: tuntematon tulos on vanhentunut.
    assert mix.is_current(str(source), str(target))
    assert not mix.is_fresh(job, settings)

    mix.write_stamp(job, settings)
    assert mix.is_fresh(job, settings)

    louder = AudioSettings(target_lufs=-14.0)
    assert mix.is_current(str(source), str(target))
    assert not mix.is_fresh(job, louder)


def _noise_file(path, seconds, rate=48000, gate=None, seed=1):
    """Kohinaa levylle. ``gate`` on funktio joka nollaa osan näytteistä."""
    from pedalboard.io import AudioFile

    rng = np.random.default_rng(seed)
    data = rng.normal(0, 0.05, int(seconds * rate)).astype(np.float32)
    if gate is not None:
        data = gate(data, rate)
    with AudioFile(str(path), "w", rate, 1, bit_depth=24) as out:
        out.write(data.reshape(1, -1))
    return rate


def _mic_job(path, seconds, name):
    from fractions import Fraction

    from autoraffkat.model import MediaItem, Placement

    item = MediaItem(
        key=name,
        name=name,
        path=str(path),
        src="",
        placements=[
            Placement(
                offset=Fraction(0),
                start=Fraction(0),
                duration=Fraction(int(seconds * 1000), 1000),
            )
        ],
    )
    return {
        "key": name,
        "name": name,
        "item": item,
        "source": str(path),
        "target": mix.sibling(str(path), mix.MIX_SUFFIX),
        "speech": True,
        "gain_db": 0.0,
        "target_lufs": -14.0,
    }


@needs_ffmpeg
def test_program_trim_measures_the_sum_not_the_stem(tmp_path):
    """Tavoitetaso koskee ohjelmaa, ei yhtä stemiä.

    Kaksi tavoitteeseen normalisoitua mikkiä ei summaudu tavoitteeseen.
    Kuinka paljon yli, riippuu päällekkäisyydestä — ei ole yhtä oikeaa
    lukua, joten se mitataan. Ääripäät ovat tässä: sama signaali kahdesti
    on 6 dB yli, täydellisesti vuorottelevat puhujat eivät yhtään.
    """
    settings = AudioSettings(target_lufs=-14.0, program_target=True)
    seconds = 8.0

    # Sama signaali kahdesti: summa on 6 dB kovempi.
    same = []
    for i in range(2):
        path = tmp_path / f"same{i}.wav"
        _noise_file(path, seconds, seed=7)
        same.append(_mic_job(path, seconds, f"same{i}"))
    assert mix.program_trim(same, settings) == -mix.MAX_PROGRAM_TRIM

    # Täydellinen vuorottelu: kummankin osuudessa on vain toinen ääni, joten
    # summa on samalla tasolla kuin kumpikin erikseen.
    def first_half(data, rate):
        data[len(data) // 2 :] = 0.0
        return data

    def second_half(data, rate):
        data[: len(data) // 2] = 0.0
        return data

    turns = []
    for i, gate in enumerate((first_half, second_half)):
        path = tmp_path / f"turn{i}.wav"
        _noise_file(path, seconds, gate=gate, seed=3 + i)
        turns.append(_mic_job(path, seconds, f"turn{i}"))
    assert mix.program_trim(turns, settings) == pytest.approx(0.0, abs=0.3)

    # Yksi mikki on jo ohjelma.
    assert mix.program_trim(turns[:1], settings) == 0.0


def test_freshness_counts_what_matches_the_settings(fixture_dir, monkeypatch, tmp_path):
    """Painike erottaa kolme tilaa, jotka näyttivät ennen samalta.

    Ei käsitelty, käsitelty, ja käsitelty mutta asetukset ovat sen jälkeen
    muuttuneet. Ilman tätä lukua painike palasi aina tekstiin «Käsittele
    ääni», eikä minuuttien ajoa voinut välttää katsomalla.
    """
    from autoraffkat.analysis import resolve_roles
    from autoraffkat.fcpxml.read import read_fcpxml
    from autoraffkat.model import ROLE_MIC, TrackConfig

    monkeypatch.setattr(mix, "stamp_dir", lambda: tmp_path)
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    tracks = {
        t.key: TrackConfig(role=ROLE_MIC, speaker=t.key.split()[0].capitalize())
        for t in tl.tracks
        if not t.has_video
    }
    roles = resolve_roles(tl, tracks)
    settings = AudioSettings(enabled=True)
    jobs = mix._jobs(tl, roles, settings)
    stubs = [pathlib.Path(job["target"]) for job in jobs]
    try:
        assert mix.freshness(tl, roles, settings) == (0, len(jobs))

        for stub, job in zip(stubs, jobs):
            stub.write_bytes(b"x")
            mix.write_stamp(job, settings)
        assert mix.freshness(tl, roles, settings) == (len(jobs), len(jobs))

        # Asetuksen muutos vanhentaa valmiin työn saman tien.
        louder = AudioSettings(enabled=True, target_lufs=-9.0)
        assert mix.freshness(tl, roles, louder) == (0, len(jobs))

        # Pois kytkettynä ei ole mitään mitä odottaa.
        assert mix.freshness(tl, roles, AudioSettings(enabled=False)) == (0, 0)
    finally:
        for stub in stubs:
            stub.unlink(missing_ok=True)


def test_force_processes_what_is_already_current(fixture_dir, monkeypatch, tmp_path):
    """Uudelleenkäsittely on käyttäjän tahallinen valinta, ei oletus."""
    from autoraffkat.analysis import resolve_roles
    from autoraffkat.fcpxml.read import read_fcpxml
    from autoraffkat.model import ROLE_MIC, TrackConfig

    monkeypatch.setattr(mix, "stamp_dir", lambda: tmp_path)
    monkeypatch.setattr(mix.chain, "load_pool", lambda *a, **k: None)
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    tracks = {
        t.key: TrackConfig(role=ROLE_MIC, speaker=t.key.split()[0].capitalize())
        for t in tl.tracks
        if not t.has_video
    }
    roles = resolve_roles(tl, tracks)
    settings = AudioSettings(enabled=True, program_target=False)
    jobs = mix._jobs(tl, roles, settings)
    stubs = [pathlib.Path(job["target"]) for job in jobs]
    try:
        for stub, job in zip(stubs, jobs):
            stub.write_bytes(b"x")
            mix.write_stamp(job, settings)

        # Ilman lippua ei tehdä mitään: kaikki on ajan tasalla.
        quiet = mix.process(tl, roles, settings)
        assert quiet.processed == 0 and quiet.skipped == len(jobs)

        # Lipun kanssa jokainen menee käsittelyyn — tässä pysäytettynä
        # tiedoston lukuun, mikä riittää osoittamaan että ohitus jäi pois.
        forced = mix.process(tl, roles, settings, force=True)
        assert forced.skipped == 0
        assert forced.processed + len(forced.errors) == len(jobs)
    finally:
        for stub in stubs:
            stub.unlink(missing_ok=True)


def test_ducking_that_matches_nothing_is_reported(fixture_dir, monkeypatch, tmp_path):
    """Asetus päällä, tuloksessa ei mitään — se ei saa olla hiljainen tila.

    Maskit avaimetaan puhujan nimellä ja työt hakevat samalla nimellä. Kerran
    jo kävi niin, että vaimennus jäi kokonaan pois eikä mikään kertonut:
    lopputuloksessa vuoto oli 4 dB kovempaa suhteessa suoraan ääneen, ja se
    kuului kampasuodatuksena vasta kun molempia raitoja kuunteli yhdessä.
    """
    from autoraffkat.analysis import resolve_roles
    from autoraffkat.fcpxml.read import read_fcpxml
    from autoraffkat.model import ROLE_MIC, TrackConfig

    monkeypatch.setattr(mix, "stamp_dir", lambda: tmp_path)
    monkeypatch.setattr(mix.chain, "load_pool", lambda *a, **k: None)
    monkeypatch.setattr(mix, "duck_masks", lambda grid, settings: {"Kukaan": None})

    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    tracks = {
        t.key: TrackConfig(role=ROLE_MIC, speaker=t.key.split()[0].capitalize())
        for t in tl.tracks
        if not t.has_video
    }
    roles = resolve_roles(tl, tracks)
    settings = AudioSettings(enabled=True, duck=True, program_target=False)
    result = mix.process(tl, roles, settings)
    assert "duck" in result.errors
    assert "Host" in result.errors["duck"] or "host" in result.errors["duck"].lower()


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
