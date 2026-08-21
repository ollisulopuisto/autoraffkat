"""Kirjoitus: aikajanalle ei jää aukkoja ja tulos on luettavissa takaisin."""

from fractions import Fraction
from xml.etree import ElementTree as ET

import pytest

from autoraffkat.fcpxml.read import read_fcpxml
from autoraffkat.fcpxml.write import (WriteError, build_fcpxml,
                                      build_multicam_fcpxml, sanitize_role)
from autoraffkat.model import Segment
from autoraffkat.timeline import parse_time


def _cut(fixture_dir, name="sync.fcpxml", fd=Fraction(1, 25)):
    tl = read_fcpxml(str(fixture_dir / name))
    by_key = {m.key: m for m in tl.media}
    segments = [
        Segment("WIDE.mp4", "Laaja", 0.0, 3.3),
        Segment("CLOSE_A.mp4", "Host", 3.3, 9.77),
        Segment("CLOSE_B.mp4", "Guest", 9.77, 20.01),
        Segment("WIDE.mp4", "Laaja", 20.01, 35.0),
    ]
    xml = build_fcpxml(by_key, segments,
                       [("MIC_A.wav", "Host"), ("MIC_B.wav", "Guest")],
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
    xml = build_fcpxml(by_key, segments, [("MIC_A.wav", "Host")],
                       tl.frame_duration, Fraction(0), Fraction(10), "Testi")
    clip = ET.fromstring(xml).find(".//spine/asset-clip")
    assert clip.get("srcEnable") == "video"


def test_mics_are_connected_with_roles(fixture_dir):
    _, xml = _cut(fixture_dir)
    first = ET.fromstring(xml).find(".//spine/asset-clip")
    mics = first.findall("asset-clip")
    assert [m.get("lane") for m in mics] == ["-1", "-2"]
    assert [m.get("audioRole") for m in mics] == ["dialogue.Host", "dialogue.Guest"]
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
    assert sanitize_role("Host") == "Host"
    assert sanitize_role("Host.S") == "Host S"
    assert sanitize_role("  ") == "Puhuja"


# ------------------------------------------------------------------ multicam


def _multicam_cut(fixture_dir, segments=None):
    """Monikameraleikkaus fixturesta. Kolmas kuva ylittää osien rajan 18 s."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    segments = segments or [
        Segment("WIDE", "Laaja", 0.0, 4.0),
        Segment("CLOSE_A", "Host", 4.0, 12.0),
        Segment("CLOSE_B", "Guest", 12.0, 30.0),
        Segment("WIDE", "Laaja", 30.0, 36.0),
    ]
    xml = build_multicam_fcpxml(
        tl, segments, [("host Track1", "Host"), ("guest Track2", "Guest")],
        Fraction(0), Fraction(36), "Monikameratesti")
    return tl, xml


def test_multicam_output_is_mc_clips(fixture_dir):
    _, xml = _multicam_cut(fixture_dir)
    spine = ET.fromstring(xml).find(".//spine")
    assert [c.tag for c in spine] == ["mc-clip"] * 5      # rajaylitys pilkkoutui
    cursor = Fraction(0)
    for clip in spine:
        assert parse_time(clip.get("offset")) == cursor
        cursor += parse_time(clip.get("duration"))
    assert cursor == 36


def test_multicam_shot_splits_at_part_boundary(fixture_dir):
    """Sama kuva osien yli on kaksi klippiä: eri multicam, eri angleID."""
    _, xml = _multicam_cut(fixture_dir)
    clips = ET.fromstring(xml).findall(".//spine/mc-clip")
    crossing = [c for c in clips if c.get("name", "").startswith("Guest")]
    assert len(crossing) == 2
    assert [c.get("ref") for c in crossing] == ["mA", "mB"]
    assert parse_time(crossing[0].get("duration")) == 6      # 12 s -> 18 s
    assert parse_time(crossing[1].get("offset")) == 18
    # Osan B klippi alkaa multicamin omasta ajasta 18 s, ei nollasta.
    assert parse_time(crossing[1].get("start")) == 18
    video = [c.find('mc-source[@srcEnable="video"]').get("angleID")
             for c in crossing]
    assert video[0] != video[1]


def test_multicam_mic_angles_get_speaker_roles(fixture_dir):
    _, xml = _multicam_cut(fixture_dir)
    clip = ET.fromstring(xml).find(".//spine/mc-clip")
    audio = clip.findall('mc-source[@srcEnable="audio"]')
    assert [a.find("audio-role-source").get("role") for a in audio] == [
        "dialogue.Host", "dialogue.Guest"]
    # Kuvakulman oma ääni jää pois päältä, kuten Final Cut sen kirjoittaa.
    video = clip.find('mc-source[@srcEnable="video"]')
    assert video.find("audio-role-source").get("active") == "0"


def test_multicam_output_reads_back(fixture_dir, tmp_path):
    """Vietyä monikameraleikkausta on voitava lukea samalla lukijalla."""
    tl, xml = _multicam_cut(fixture_dir)
    path = tmp_path / "out.fcpxml"
    path.write_text(xml, encoding="utf-8")
    again = read_fcpxml(str(path))
    assert again.kind == "multicam"
    assert again.frame_duration == tl.frame_duration
    assert [t.key for t in again.tracks] == [t.key for t in tl.tracks]


def test_multicam_refuses_a_plain_timeline(fixture_dir):
    tl = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    with pytest.raises(WriteError):
        build_multicam_fcpxml(tl, [Segment("WIDE.mp4", "Laaja", 0.0, 5.0)], [],
                              Fraction(0), Fraction(5), "Ei monikameraa")


# --------------------------------------------------- Final Cutin oma mittapuu


def test_multicam_output_passes_the_fcp_dtd(fixture_dir, validate_fcpxml):
    """Oma lukija hyväksyy enemmän kuin tuonti; DTD on se raja joka ratkaisee.

    Tämä testi on olemassa siksi, että ``mc-clip``iin kirjoitettiin kerran
    ``tcFormat``, jota DTD ei tunne. Lukija ei siitä välittänyt, Final Cut
    hylkäsi koko tiedoston.
    """
    _, xml = _multicam_cut(fixture_dir)
    validate_fcpxml(xml, "multicam.fcpxml")


def test_flat_output_passes_the_fcp_dtd(fixture_dir, validate_fcpxml):
    _, xml = _cut(fixture_dir)
    validate_fcpxml(xml, "flat.fcpxml")


def test_multicam_gap_output_passes_the_fcp_dtd(fixture_dir, validate_fcpxml):
    """Osien väliin jäävä aukko on omaa merkintäänsä, ei mc-clip."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    # Ohjelma jatkuu multicamien loputtua, joten loppuun tulee <gap>.
    segments = [Segment("WIDE", "Laaja", 0.0, 36.0),
                Segment("CLOSE_A", "Host", 36.0, 44.0)]
    xml = build_multicam_fcpxml(tl, segments, [("host Track1", "Host")],
                                Fraction(0), Fraction(44), "Aukolla")
    assert "<gap" in xml
    validate_fcpxml(xml, "gap.fcpxml")


# ------------------------------------------------------- käsitelty ääni


def test_replacement_redirects_and_drops_the_bookmark():
    """``<bookmark>`` voittaa ``src``:n, joten sen on lähdettävä.

    Ilman poistoa Final Cut avaisi alkuperäisen käsittelemättömän tiedoston
    eikä kertoisi siitä mitään.
    """
    from autoraffkat.fcpxml.write import _redirect_asset
    asset = ET.fromstring(
        '<asset id="r3"><media-rep kind="original-media" src="file:///a/vanha.wav">'
        '<bookmark>Ym9va21hcms=</bookmark></media-rep></asset>')
    _redirect_asset(asset, "/a/uusi [mix].wav")
    rep = asset.find("media-rep")
    assert rep.get("src") == "file:///a/uusi%20%5Bmix%5D.wav"
    assert rep.find("bookmark") is None


def test_multicam_export_uses_the_processed_audio(fixture_dir, validate_fcpxml):
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    replacements = {k: f"/mix/{k[:-4]} [mix].wav"
                    for k in tl.media_by_key() if k.endswith(".wav")}
    xml = build_multicam_fcpxml(
        tl, [Segment("WIDE", "Laaja", 0.0, 36.0)],
        [("host Track1", "Host")], Fraction(0), Fraction(36), "Käsitelty",
        replacements=replacements)
    assert xml.count("%5Bmix%5D.wav") == len(replacements)
    # Kameroihin ei kosketa: kuva tulee yhä alkuperäisistä tiedostoista.
    assert "WIDE 01.mp4" in xml
    validate_fcpxml(xml, "mixed.fcpxml")


def test_multicam_room_tone_is_one_lane_with_its_own_role(fixture_dir,
                                                          validate_fcpxml):
    """Tilaääni ei ole kulma vaan liitetty klippi: kuva vaihtuu, ääni jatkuu."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    room = [(k, f"/mix/{k[:-4]} [room].wav")
            for k in tl.media_by_key() if k.startswith("WIDE")]
    xml = build_multicam_fcpxml(
        tl, [Segment("CLOSE_A", "Host", 0.0, 18.0),
             Segment("CLOSE_B", "Guest", 18.0, 36.0)],
        [("host Track1", "Host")], Fraction(0), Fraction(36), "Tilaäänellä",
        room=room)
    root = ET.fromstring(xml)
    clips = root.findall(".//mc-clip/asset-clip")
    assert len(clips) == len(room)
    # Osat eivät mene päällekkäin, joten ne kuuluvat samalle lanelle.
    assert {c.get("lane") for c in clips} == {"-1"}
    assert {c.get("audioRole") for c in clips} == {"effects.Tilaääni"}
    # Molemmat liitetään ensimmäiseen klippiin, ei omiinsa.
    hosts = [c for c in root.findall(".//spine/mc-clip") if c.find("asset-clip") is not None]
    assert len(hosts) == 1
    validate_fcpxml(xml, "room.fcpxml")


def test_room_asset_has_no_video(fixture_dir):
    """Tilaääni on WAV, joten sen assetissa ei saa luvata kuvaa."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    room = [(k, f"/mix/{k[:-4]} [room].wav")
            for k in tl.media_by_key() if k.startswith("WIDE 01")]
    xml = build_multicam_fcpxml(
        tl, [Segment("WIDE", "Laaja", 0.0, 36.0)], [], Fraction(0), Fraction(36),
        "Tilaääni", room=room)
    asset = next(a for a in ET.fromstring(xml).iter("asset")
                 if (a.get("name") or "").endswith("tilaääni"))
    assert asset.get("hasAudio") == "1"
    assert asset.get("hasVideo") is None and asset.get("format") is None


def test_flat_export_uses_the_processed_audio(fixture_dir, validate_fcpxml):
    tl = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    by_key = {m.key: m for m in tl.media}
    xml = build_fcpxml(
        by_key, [Segment("WIDE.mp4", "Laaja", 0.0, 20.0)],
        [("MIC_A.wav", "Host")], tl.frame_duration, Fraction(0), Fraction(20),
        "Käsitelty", replacements={"MIC_A.wav": "/mix/MIC_A [mix].wav"})
    assert "MIC_A%20%5Bmix%5D.wav" in xml
    assert "WIDE.mp4" in xml
    validate_fcpxml(xml, "flat-mixed.fcpxml")


def test_room_asset_declares_mono(fixture_dir):
    """Tilaääni kirjoitetaan monona, joten assetti ei saa luvata stereota."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    room = [(k, f"/mix/{k[:-4]} [room].wav")
            for k in tl.media_by_key() if k.startswith("WIDE 01")]
    xml = build_multicam_fcpxml(
        tl, [Segment("WIDE", "Laaja", 0.0, 36.0)], [], Fraction(0), Fraction(36),
        "Mono", room=room)
    asset = next(a for a in ET.fromstring(xml).iter("asset")
                 if (a.get("name") or "").endswith("tilaääni"))
    assert asset.get("audioChannels") == "1"
    assert asset.get("audioSources") == "1"
