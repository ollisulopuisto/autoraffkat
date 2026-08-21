import json
import os

from autoraffkat import project
from autoraffkat.model import Globals, TrackConfig
from autoraffkat.project import ProjectSettings


def test_round_trip(tmp_path):
    xml = tmp_path / "jakso.fcpxml"
    xml.write_text("<fcpxml/>")
    settings = project.ProjectSettings(
        tracks={"MIC_A.wav": TrackConfig(role="mic", speaker="Olli",
                                         sensitivity_db=9.5, gain_db=-3.0)},
        globals=Globals(min_shot=4.0, overlap_rule="louder"))
    project.save(str(xml), settings)
    again = project.load(str(xml))
    assert again.tracks["MIC_A.wav"].speaker == "Olli"
    assert again.tracks["MIC_A.wav"].sensitivity_db == 9.5
    assert again.globals.min_shot == 4.0
    assert again.globals.overlap_rule == "louder"


def test_settings_live_next_to_xml(tmp_path):
    xml = tmp_path / "jakso.fcpxml"
    assert project.settings_path(str(xml)) == str(tmp_path / "jakso.autoraffkat.json")
    assert project.default_output_path(str(xml)) == str(tmp_path / "jakso-leikattu.fcpxml")


def test_broken_file_does_not_block(tmp_path):
    xml = tmp_path / "jakso.fcpxml"
    xml.write_text("<fcpxml/>")
    (tmp_path / "jakso.autoraffkat.json").write_text("{ rikki")
    assert project.load(str(xml)).globals.min_shot == Globals().min_shot


def test_unknown_keys_are_ignored(tmp_path):
    xml = tmp_path / "jakso.fcpxml"
    xml.write_text("<fcpxml/>")
    (tmp_path / "jakso.autoraffkat.json").write_text(
        '{"version": 99, "globals": {"min_shot": 3, "tuntematon": 1}, '
        '"tracks": {"a": {"role": "mic", "outo": true}}}')
    settings = project.load(str(xml))
    assert settings.globals.min_shot == 3
    assert settings.tracks["a"].role == "mic"


def _write_settings(path, tracks, min_shot=2.5):
    """Asetustiedosto suoraan levylle, ilman lähde-XML:ää."""
    settings = ProjectSettings(
        tracks={k: TrackConfig(**v) for k, v in tracks.items()},
        globals=Globals(min_shot=min_shot))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(settings.to_json(), fh)
    return path


def test_previous_is_found_beside_and_above(tmp_path):
    """Sarjan edellinen jakso on joko naapurissa tai naapuripaketissa."""
    bundle = tmp_path / "jakso54.fcpxmld"
    bundle.mkdir()
    xml = bundle / "Info.fcpxml"
    older = tmp_path / "jakso53.fcpxmld"
    older.mkdir()
    previous = _write_settings(older / "Info.autoraffkat.json",
                               {"CAM 1": {"role": "close", "speaker": "Olli"}})
    assert project.find_previous(str(xml)) == str(previous)


def test_previous_ignores_our_own_settings(tmp_path):
    xml = tmp_path / "jakso.fcpxml"
    _write_settings(tmp_path / "jakso.autoraffkat.json", {"CAM 1": {"role": "wide"}})
    assert project.find_previous(str(xml)) is None


def test_previous_takes_the_newest(tmp_path):
    xml = tmp_path / "uusi.fcpxml"
    old = _write_settings(tmp_path / "a.autoraffkat.json", {"CAM 1": {"role": "wide"}})
    new = _write_settings(tmp_path / "b.autoraffkat.json", {"CAM 1": {"role": "close"}})
    os.utime(old, (1_000_000, 1_000_000))
    assert project.find_previous(str(xml)) == str(new)


def test_broken_settings_read_as_none(tmp_path):
    path = tmp_path / "rikki.autoraffkat.json"
    path.write_text("{ ei tätä voi lukea", encoding="utf-8")
    assert project.read(str(path)) is None
    assert project.read(str(tmp_path / "ei-ole.json")) is None
