from autoraffkat import project
from autoraffkat.model import Globals, TrackConfig


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
