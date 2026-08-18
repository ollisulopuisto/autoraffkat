"""Kirjoitus: aikajanalle ei jää aukkoja ja tulos on luettavissa takaisin."""

from fractions import Fraction
from xml.etree import ElementTree as ET

import pytest

from autoraffkat.fcpxml.read import read_fcpxml
from autoraffkat.fcpxml.write import WriteError, build_fcpxml, sanitize_role
from autoraffkat.model import Segment
from autoraffkat.timeline import parse_time


def _cut(fixture_dir, name="sync.fcpxml", fd=Fraction(1, 25)):
    tl = read_fcpxml(str(fixture_dir / name))
    by_key = {m.key: m for m in tl.media}
    segments = [
        Segment("WIDE.mp4", "Laaja", 0.0, 3.3),
        Segment("CLOSE_A.mp4", "Olli", 3.3, 9.77),
        Segment("CLOSE_B.mp4", "Vieras", 9.77, 20.01),
        Segment("WIDE.mp4", "Laaja", 20.01, 35.0),
    ]
    xml = build_fcpxml(by_key, segments,
                       [("MIC_A.wav", "Olli"), ("MIC_B.wav", "Vieras")],
                       tl.frame_duration, tl.start, tl.start + Fraction(35),
                       "Testi")
    return tl, xml


def test_spine_has_no_gaps(fixture_dir):
    _, xml = _cut(fixture_dir)
    spine = ET.fromstring(xml).find(".//spine")
    cursor = Fraction(0)
    for clip in spine:
        assert parse_time(clip.get("offset")) == cursor
        cursor += parse_time(clip.get("duration"))
    sequence = ET.fromstring(xml).find(".//sequence")
    assert cursor == parse_time(sequence.get("duration"))


def test_cameras_lose_their_own_audio(fixture_dir):
    """Kameralla jolla on ääntä pitää olla srcEnable="video"."""
    tl = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    by_key = {m.key: m for m in tl.media}
    by_key["WIDE.mp4"].has_audio = True
    segments = [Segment("WIDE.mp4", "Laaja", 0.0, 10.0)]
    xml = build_fcpxml(by_key, segments, [("MIC_A.wav", "Olli")],
                       tl.frame_duration, Fraction(0), Fraction(10), "Testi")
    clip = ET.fromstring(xml).find(".//spine/asset-clip")
    assert clip.get("srcEnable") == "video"


def test_mics_are_connected_with_roles(fixture_dir):
    _, xml = _cut(fixture_dir)
    first = ET.fromstring(xml).find(".//spine/asset-clip")
    mics = first.findall("asset-clip")
    assert [m.get("lane") for m in mics] == ["-1", "-2"]
    assert [m.get("audioRole") for m in mics] == ["dialogue.Olli", "dialogue.Vieras"]
    assert all(parse_time(m.get("duration")) == 35 for m in mics)


def test_output_reads_back(fixture_dir, tmp_path):
    """Vietyä XML:ää on voitava lukea samalla lukijalla."""
    tl, xml = _cut(fixture_dir)
    path = tmp_path / "out.fcpxml"
    path.write_text(xml, encoding="utf-8")
    again = read_fcpxml(str(path))
    assert again.kind == "project"
    assert again.frame_duration == tl.frame_duration
    assert {m.key for m in again.media} == {
        "WIDE.mp4", "CLOSE_A.mp4", "CLOSE_B.mp4", "MIC_A.wav", "MIC_B.wav"}


def test_subframe_cuts_are_quantized(fixture_dir):
    """Puolikkaan kehyksen kohdalle osuvat leikkaukset eivät saa mennä päällekkäin."""
    tl = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    by_key = {m.key: m for m in tl.media}
    segments, t = [], 0.0
    while t < 30.0:
        segments.append(Segment("WIDE.mp4" if len(segments) % 2 else "CLOSE_A.mp4",
                                "x", t, t + 0.019))
        t += 0.019
    segments[-1].end = 30.0
    xml = build_fcpxml(by_key, segments, [], tl.frame_duration,
                       Fraction(0), Fraction(30), "Tiheä")
    spine = ET.fromstring(xml).find(".//spine")
    cursor = Fraction(0)
    for clip in spine:
        assert parse_time(clip.get("offset")) == cursor
        assert parse_time(clip.get("duration")) > 0
        cursor += parse_time(clip.get("duration"))
    assert cursor == 30


def test_empty_segments_refused(fixture_dir):
    tl = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    with pytest.raises(WriteError):
        build_fcpxml({m.key: m for m in tl.media}, [], [], tl.frame_duration,
                     Fraction(0), Fraction(10), "Tyhjä")


def test_role_sanitizing():
    assert sanitize_role("Olli") == "Olli"
    assert sanitize_role("Olli.S") == "Olli S"
    assert sanitize_role("  ") == "Puhuja"
