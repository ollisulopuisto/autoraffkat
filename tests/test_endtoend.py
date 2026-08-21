"""Koko putki: XML sisään, päätös, XML ulos. Vaatii ffmpegin."""

import time
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from autoraffkat.analysis import analyze, build_grid, resolve_roles
from autoraffkat.decide import decide
from autoraffkat.fcpxml.read import read_fcpxml
from autoraffkat.model import (ROLE_CLOSE, ROLE_MIC, ROLE_WIDE, Globals,
                               TrackConfig)
from autoraffkat.server.app import AppState, create_app
from conftest import needs_ffmpeg
from make_fixture import SPEECH_A, SPEECH_B


def _tracks():
    return {
        "WIDE.mp4": TrackConfig(role=ROLE_WIDE),
        "CLOSE_A.mp4": TrackConfig(role=ROLE_CLOSE, speaker="Olli"),
        "CLOSE_B.mp4": TrackConfig(role=ROLE_CLOSE, speaker="Vieras"),
        "MIC_A.wav": TrackConfig(role=ROLE_MIC, speaker="Olli"),
        "MIC_B.wav": TrackConfig(role=ROLE_MIC, speaker="Vieras"),
    }


def angle_at(segments, seconds):
    for seg in segments:
        if seg.start <= seconds < seg.end:
            return seg.label
    return None


def source_to_timeline(timeline, key="MIC_A.wav"):
    """Lähdeaika aikajanan ajaksi.

    Projektifixture alkaa lähteen sekunnista 1, synkkaklippi nollasta, joten
    puhejaksojen ajat on käännettävä ennen vertailua.
    """
    item = next(m for m in timeline.media if m.key == key)
    placement = item.placements[0]
    shift = float(placement.start - item.asset_start - placement.offset)
    return lambda t: t - shift


@needs_ffmpeg
@pytest.mark.parametrize("source", ["sync.fcpxml", "project.fcpxml"])
def test_speech_selects_the_right_camera(fixture_dir, source):
    timeline = read_fcpxml(str(fixture_dir / source))
    analysis = analyze(timeline)
    assert not analysis.errors
    tracks = _tracks()
    grid, start, end = build_grid(analysis, tracks, resolve_roles(timeline, tracks))
    decision = decide(grid, Globals(min_shot=1.5, lead=0.15, confirm=0.3,
                                    min_overlap=0.4))
    to_timeline = source_to_timeline(timeline)

    # Yksinpuhelun keskellä pitää olla puhujan lähikuva.
    for spans, other, expected in ((SPEECH_A, SPEECH_B, "Olli"),
                                   (SPEECH_B, SPEECH_A, "Vieras")):
        for lo, hi in spans:
            mid = (lo + hi) / 2
            if any(o0 < mid < o1 for o0, o1 in other):
                continue                          # päällekkäispuhe, oma sääntönsä
            at = to_timeline(mid)
            if not (float(start) + 1 < at < float(end) - 1):
                continue
            assert angle_at(decision.segments, at) == expected, f"kohta {mid}"


@needs_ffmpeg
def test_overlap_goes_wide(fixture_dir):
    """A puhuu 12–14 ja B 13,5–19: päällekkäisyys vie laajaan."""
    timeline = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    analysis = analyze(timeline)
    tracks = _tracks()
    grid, _, _ = build_grid(analysis, tracks, resolve_roles(timeline, tracks))
    decision = decide(grid, Globals(min_shot=1.5, lead=0.15, confirm=0.3,
                                    min_overlap=0.3, overlap_rule="wide"))
    at = source_to_timeline(timeline)(13.8)
    assert angle_at(decision.segments, at) == "Laaja"


@needs_ffmpeg
def test_envelope_cache_makes_the_second_pass_free(fixture_dir):
    timeline = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    analyze(timeline)                                     # lämmitys levylle
    started = time.perf_counter()
    analyze(timeline)
    assert (time.perf_counter() - started) < 0.4


@needs_ffmpeg
def test_server_round_trip(scratch_xml):
    """Sama silmukka kuin käyttöliittymässä: säädä, katso, vie."""
    source = scratch_xml()
    state = AppState(xml_path=str(source))
    state.load()
    for _ in range(200):
        if state.progress.get("ready"):
            break
        time.sleep(0.05)
    assert state.progress["ready"], "verhokäyrät eivät valmistuneet"

    client = TestClient(create_app(state))
    assert client.get("/").status_code == 200
    assert client.get("/api/state").json()["kind"] == "sync-clip"

    payload = {
        "tracks": {k: v.to_json() for k, v in _tracks().items()},
        "globals": Globals(min_shot=1.5, lead=0.15, confirm=0.3,
                           min_overlap=0.4, project_name="Testi").to_json(),
    }
    result = client.post("/api/settings", json=payload).json()
    assert result["ok"], result.get("problems")
    assert len(result["segments"]) > 4
    assert result["preview"]["speakers"][0]["name"] == "Olli"
    assert result["ms"] < 500

    exported = client.post("/api/export", json=payload).json()
    assert exported["ok"]
    written = ET.parse(exported["path"]).getroot()
    assert written.find(".//project").get("name") == "Testi"
    assert len(written.findall(".//spine/asset-clip")) == exported["cuts"]

    # Asetukset jäivät XML:n viereen seuraavaa jaksoa varten.
    assert source.with_suffix(".autoraffkat.json").exists()


def test_defaults_are_guessed_but_speakers_are_asked(scratch_xml):
    """Ensiavaus arvaa roolit nimistä; puhujat on silti nimettävä itse."""
    state = AppState(xml_path=str(scratch_xml()))
    state.load()
    assert state.settings.tracks["WIDE.mp4"].role == "wide"
    assert state.settings.tracks["MIC_A.wav"].role == "mic"

    client = TestClient(create_app(state))
    result = client.post("/api/settings", json={"tracks": {}, "globals": {}}).json()
    assert not result["ok"]
    assert any("puhujaa" in problem for problem in result["problems"])


def test_no_wide_is_reported(scratch_xml):
    state = AppState(xml_path=str(scratch_xml()))
    state.load()
    client = TestClient(create_app(state))
    payload = {"tracks": {k: v.to_json() for k, v in _tracks().items()},
               "globals": {}}
    payload["tracks"]["WIDE.mp4"]["role"] = "unused"
    result = client.post("/api/settings", json=payload).json()
    assert not result["ok"]
    assert any("laajaksi" in problem for problem in result["problems"])


# ------------------------------------------------------------------ multicam


def _multicam_tracks():
    """Roolit raita-avaimilla: kulma on yksi raita, vaikka osia on kaksi."""
    return {
        "WIDE": TrackConfig(role=ROLE_WIDE),
        "CLOSE_A": TrackConfig(role=ROLE_CLOSE, speaker="Olli"),
        "CLOSE_B": TrackConfig(role=ROLE_CLOSE, speaker="Vieras"),
        "olli Track1": TrackConfig(role=ROLE_MIC, speaker="Olli"),
        "vieras Track2": TrackConfig(role=ROLE_MIC, speaker="Vieras"),
    }


@needs_ffmpeg
def test_multicam_speech_selects_the_right_camera(fixture_dir):
    """Sama tarkistus kuin synkkaklipille, mutta puhe jatkuu osien yli."""
    timeline = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    analysis = analyze(timeline)
    assert not analysis.errors
    tracks = _multicam_tracks()
    grid, start, end = build_grid(analysis, tracks, resolve_roles(timeline, tracks))
    # Ohjelma kattaa molemmat osat, ei vain jälkimmäistä.
    assert float(start) == 0.0 and float(end) > 30.0
    decision = decide(grid, Globals(min_shot=1.5, lead=0.15, confirm=0.3,
                                    min_overlap=0.4))
    to_timeline = source_to_timeline(timeline, "olli a Track1.wav")

    for spans, other, expected in ((SPEECH_A, SPEECH_B, "Olli"),
                                   (SPEECH_B, SPEECH_A, "Vieras")):
        for lo, hi in spans:
            mid = (lo + hi) / 2
            if any(o0 < mid < o1 for o0, o1 in other):
                continue
            at = to_timeline(mid)
            if not (float(start) + 1 < at < float(end) - 1):
                continue
            assert angle_at(decision.segments, at) == expected, f"kohta {mid}"


@needs_ffmpeg
def test_multicam_server_round_trip(scratch_xml):
    """Sama silmukka kuin käyttöliittymässä, monikameralähteellä."""
    source = scratch_xml("multicam.fcpxml")
    state = AppState(xml_path=str(source))
    state.load()
    for _ in range(200):
        if state.progress.get("ready"):
            break
        time.sleep(0.05)
    assert state.progress["ready"], "verhokäyrät eivät valmistuneet"

    client = TestClient(create_app(state))
    fetched = client.get("/api/state").json()
    assert fetched["kind"] == "multicam"
    assert fetched["parts"] == 2
    assert len(fetched["tracks"]) == 5
    assert all(len(t["parts"]) == 2 for t in fetched["tracks"])

    payload = {
        "tracks": {k: v.to_json() for k, v in _multicam_tracks().items()},
        "globals": Globals(min_shot=1.5, lead=0.15, confirm=0.3,
                           min_overlap=0.4, project_name="Monikamera").to_json(),
    }
    result = client.post("/api/settings", json=payload).json()
    assert result["ok"], result.get("problems")
    assert len(result["segments"]) > 4

    exported = client.post("/api/export", json=payload).json()
    assert exported["ok"]
    written = ET.parse(exported["path"]).getroot()
    clips = written.findall(".//spine/mc-clip")
    assert clips, "vienti ei tuottanut monikameraklippejä"
    # Rajaylitykset pilkkoutuvat, joten klippejä on vähintään yhtä monta.
    assert len(clips) >= exported["cuts"]
    assert {c.get("ref") for c in clips} == {"mA", "mB"}


def test_multicam_defaults_guess_speakers_from_mic_names(scratch_xml):
    """Mikin ensimmäinen sana on käytännössä aina puhujan nimi."""
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    state.load()
    assert state.settings.tracks["olli Track1"].role == "mic"
    assert state.settings.tracks["olli Track1"].speaker == "Olli"
    assert state.settings.tracks["vieras Track2"].speaker == "Vieras"
    # Kameroita ei arvata: kulmat ovat 1, 2, 3 eikä niistä näe mitään.
    assert state.settings.tracks["CLOSE_A"].role == "unused"


def test_all_wide_is_a_problem_not_a_result(scratch_xml):
    """Ilman lähikuvia leikkaus olisi yhtä laajaa kuvaa — se on puute."""
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    state.load()
    client = TestClient(create_app(state))
    tracks = {k: v.to_json() for k, v in _multicam_tracks().items()}
    tracks["CLOSE_A"]["role"] = "unused"
    tracks["CLOSE_B"]["role"] = "unused"
    result = client.post("/api/settings",
                         json={"tracks": tracks, "globals": {}}).json()
    assert not result["ok"]
    assert any("lähikuvaa" in problem for problem in result["problems"])


def test_roles_are_inherited_from_the_previous_episode(fixture_dir, tmp_path):
    """Kamera ei kerro kumpaa puhujaa se kuvaa, mutta viime jakso kertoo."""
    import shutil
    from autoraffkat import project

    previous = tmp_path / "jakso53.fcpxmld"
    previous.mkdir()
    project.save(str(previous / "Info.fcpxml"),
                 project.ProjectSettings(
                     tracks={k: v for k, v in _multicam_tracks().items()},
                     globals=Globals(min_shot=4.0)))

    current = tmp_path / "jakso54.fcpxmld"
    current.mkdir()
    shutil.copy(fixture_dir / "multicam.fcpxml", current / "Info.fcpxml")

    state = AppState(xml_path=str(current / "Info.fcpxml"))
    state.load()
    assert state.settings.tracks["CLOSE_A"].role == "close"
    assert state.settings.tracks["CLOSE_A"].speaker == "Olli"
    assert state.settings.tracks["WIDE"].role == "wide"
    assert state.settings.globals.min_shot == 4.0
    assert state.inherited_from.endswith("jakso53.autoraffkat.json")


def test_own_settings_beat_the_previous_episode(fixture_dir, tmp_path):
    import shutil
    from autoraffkat import project

    other = tmp_path / "jakso53.fcpxmld"
    other.mkdir()
    project.save(str(other / "Info.fcpxml"), project.ProjectSettings(
        tracks={"WIDE": TrackConfig(role=ROLE_CLOSE, speaker="Väärin")}))

    current = tmp_path / "jakso54.fcpxmld"
    current.mkdir()
    xml = current / "Info.fcpxml"
    shutil.copy(fixture_dir / "multicam.fcpxml", xml)
    project.save(str(xml), project.ProjectSettings(
        tracks={"WIDE": TrackConfig(role=ROLE_WIDE)}))

    state = AppState(xml_path=str(xml))
    state.load()
    assert state.settings.tracks["WIDE"].role == "wide"
    assert state.inherited_from == ""


def test_audio_settings_survive_a_round_trip(scratch_xml):
    """Ääniasetukset tallentuvat XML:n viereen kuten muutkin."""
    from autoraffkat import project
    source = scratch_xml("multicam.fcpxml")
    state = AppState(xml_path=str(source))
    state.load()
    client = TestClient(create_app(state))
    payload = {"tracks": {k: v.to_json() for k, v in _multicam_tracks().items()},
               "globals": {},
               "audio": {"enabled": True, "target_lufs": -18.0,
                         "room_track": "WIDE", "room_db": -20.0}}
    client.post("/api/settings", json=payload)
    saved = project.load(str(source)).audio
    assert saved.enabled and saved.target_lufs == -18.0
    assert saved.room_track == "WIDE" and saved.room_db == -20.0


def test_unknown_room_track_is_refused(scratch_xml):
    """Tuntematon raita jäisi hiljaa pois; se nollataan heti."""
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    state.load()
    client = TestClient(create_app(state))
    client.post("/api/settings", json={
        "tracks": {}, "globals": {},
        "audio": {"enabled": True, "room_track": "EI OLE"}})
    assert state.settings.audio.room_track == ""


def test_export_ignores_processed_audio_that_is_not_there(scratch_xml):
    """Puuttuvaan [mix]-tiedostoon ei viitata, vaikka se olisi kirjattu."""
    from autoraffkat.audio import mix as mixer
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    state.load()
    for _ in range(200):
        if state.progress.get("ready"):
            break
        time.sleep(0.05)
    state.mix_result = mixer.MixResult(
        replacements={"olli a Track1.wav": "/ei/ole [mix].wav"})
    state.settings.audio.enabled = True

    client = TestClient(create_app(state))
    payload = {"tracks": {k: v.to_json() for k, v in _multicam_tracks().items()},
               "globals": {}, "audio": {"enabled": True}}
    result = client.post("/api/export", json=payload).json()
    assert result["ok"] and result["mixed"] == 0
    assert "%5Bmix%5D" not in ET.tostring(ET.parse(result["path"]).getroot(),
                                          encoding="unicode")


def test_export_warns_when_audio_is_still_processing(scratch_xml):
    """Kesken käsittelyn vietäessä tulos on ehjä mutta käsittelemätön.

    Sitä ei huomaa Final Cutissa ennen kuin kuuntelee, ja silloin leikkaus on
    jo tehty — uusi vienti ei tuo tehtyjä muokkauksia mukanaan.
    """
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    state.load()
    for _ in range(200):
        if state.progress.get("ready"):
            break
        time.sleep(0.05)
    state.settings.audio.enabled = True
    state.mix_progress["running"] = True

    client = TestClient(create_app(state))
    payload = {"tracks": {k: v.to_json() for k, v in _multicam_tracks().items()},
               "globals": {}, "audio": {"enabled": True}}
    result = client.post("/api/export", json=payload).json()
    assert result["ok"] and result["mixed"] == 0
    assert any("kesken" in w for w in result["warnings"])


def test_export_is_quiet_when_audio_is_off(scratch_xml):
    """Ilman äänenkäsittelyä ei ole mitään varoitettavaa."""
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    state.load()
    for _ in range(200):
        if state.progress.get("ready"):
            break
        time.sleep(0.05)
    client = TestClient(create_app(state))
    result = client.post("/api/export", json={
        "tracks": {k: v.to_json() for k, v in _multicam_tracks().items()},
        "globals": {}, "audio": {"enabled": False}}).json()
    assert result["ok"] and result["warnings"] == []


def test_defaults_are_available_for_resetting(scratch_xml):
    """Säätimiä on kolmisenkymmentä ja ne periytyvät seuraavaan jaksoon.

    Ilman paluuta yhdestä huonosta arvosta ei pääsisi takaisin.
    """
    from autoraffkat.model import AudioSettings, Globals
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    state.load()
    client = TestClient(create_app(state))
    data = client.get("/api/defaults").json()
    assert data["globals"] == Globals().to_json()
    assert data["audio"] == AudioSettings().to_json()
    assert data["audio"]["duck_db"] == -9.0


def test_declick_sensitivity_round_trips(scratch_xml):
    """Naksujen herkkyys riippuu puhujasta, joten se on säädettävissä."""
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    state.load()
    client = TestClient(create_app(state))
    client.post("/api/settings", json={
        "tracks": {}, "globals": {},
        "audio": {"enabled": True, "declick": True, "declick_sensitivity": 0.8}})
    assert state.settings.audio.declick_sensitivity == 0.8
