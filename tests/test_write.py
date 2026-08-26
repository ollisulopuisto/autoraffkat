"""Kirjoitus: aikajanalle ei jää aukkoja ja tulos on luettavissa takaisin."""

from fractions import Fraction
from xml.etree import ElementTree as ET

import pytest

from autoraffkat.fcpxml.read import read_fcpxml
from autoraffkat.fcpxml.write import (
    WriteError,
    build_fcpxml,
    build_multicam_fcpxml,
    sanitize_role,
)
from autoraffkat.model import Segment
from autoraffkat.timeline import parse_time


def _cut(fixture_dir, name="sync.fcpxml", fd=Fraction(1, 25), settings=None):
    tl = read_fcpxml(str(fixture_dir / name))
    by_key = {m.key: m for m in tl.media}
    segments = [
        Segment("WIDE.mp4", "Laaja", 0.0, 3.3),
        Segment("CLOSE_A.mp4", "Host", 3.3, 9.77),
        Segment("CLOSE_B.mp4", "Guest", 9.77, 20.01),
        Segment("WIDE.mp4", "Laaja", 20.01, 35.0),
    ]
    xml = build_fcpxml(
        by_key,
        segments,
        [("MIC_A.wav", "Host"), ("MIC_B.wav", "Guest")],
        tl.frame_duration,
        tl.start,
        tl.start + Fraction(35),
        "Testi",
        settings=settings,
        source="jakso.fcpxml",
    )
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
    xml = build_fcpxml(
        by_key,
        segments,
        [("MIC_A.wav", "Host")],
        tl.frame_duration,
        Fraction(0),
        Fraction(10),
        "Testi",
    )
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
        "WIDE.mp4",
        "CLOSE_A.mp4",
        "CLOSE_B.mp4",
        "MIC_A.wav",
        "MIC_B.wav",
    }


def test_subframe_cuts_are_quantized(fixture_dir):
    """Puolikkaan kehyksen kohdalle osuvat leikkaukset eivät saa mennä päällekkäin."""
    tl = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    by_key = {m.key: m for m in tl.media}
    segments, t = [], 0.0
    while t < 30.0:
        segments.append(
            Segment(
                "WIDE.mp4" if len(segments) % 2 else "CLOSE_A.mp4", "x", t, t + 0.019
            )
        )
        t += 0.019
    segments[-1].end = 30.0
    xml = build_fcpxml(
        by_key, segments, [], tl.frame_duration, Fraction(0), Fraction(30), "Tiheä"
    )
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
        build_fcpxml(
            {m.key: m for m in tl.media},
            [],
            [],
            tl.frame_duration,
            Fraction(0),
            Fraction(10),
            "Tyhjä",
        )


def test_role_sanitizing():
    assert sanitize_role("Host") == "Host"
    assert sanitize_role("Host.S") == "Host S"
    assert sanitize_role("  ") == "Puhuja"


# ------------------------------------------------------------------ multicam


def _multicam_cut(fixture_dir, segments=None, settings=None):
    """Monikameraleikkaus fixturesta. Kolmas kuva ylittää osien rajan 18 s."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    segments = segments or [
        Segment("WIDE", "Laaja", 0.0, 4.0),
        Segment("CLOSE_A", "Host", 4.0, 12.0),
        Segment("CLOSE_B", "Guest", 12.0, 30.0),
        Segment("WIDE", "Laaja", 30.0, 36.0),
    ]
    xml = build_multicam_fcpxml(
        tl,
        segments,
        [("host Track1", "Host"), ("guest Track2", "Guest")],
        Fraction(0),
        Fraction(36),
        "Monikameratesti",
        settings=settings,
        source="multicam.fcpxml",
    )
    return tl, xml


def test_multicam_output_is_mc_clips(fixture_dir):
    _, xml = _multicam_cut(fixture_dir)
    spine = ET.fromstring(xml).find(".//spine")
    assert [c.tag for c in spine] == ["mc-clip"] * 5  # rajaylitys pilkkoutui
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
    assert parse_time(crossing[0].get("duration")) == 6  # 12 s -> 18 s
    assert parse_time(crossing[1].get("offset")) == 18
    # Osan B klippi alkaa multicamin omasta ajasta 18 s, ei nollasta.
    assert parse_time(crossing[1].get("start")) == 18
    video = [c.find('mc-source[@srcEnable="video"]').get("angleID") for c in crossing]
    assert video[0] != video[1]


def test_multicam_mic_angles_get_speaker_roles(fixture_dir):
    _, xml = _multicam_cut(fixture_dir)
    clip = ET.fromstring(xml).find(".//spine/mc-clip")
    audio = clip.findall('mc-source[@srcEnable="audio"]')
    assert [a.find("audio-role-source").get("role") for a in audio] == [
        "dialogue.Host",
        "dialogue.Guest",
    ]
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
        build_multicam_fcpxml(
            tl,
            [Segment("WIDE.mp4", "Laaja", 0.0, 5.0)],
            [],
            Fraction(0),
            Fraction(5),
            "Ei monikameraa",
        )


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
    segments = [
        Segment("WIDE", "Laaja", 0.0, 36.0),
        Segment("CLOSE_A", "Host", 36.0, 44.0),
    ]
    xml = build_multicam_fcpxml(
        tl, segments, [("host Track1", "Host")], Fraction(0), Fraction(44), "Aukolla"
    )
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
        "<bookmark>Ym9va21hcms=</bookmark></media-rep></asset>"
    )
    _redirect_asset(asset, "/a/uusi [mix].wav")
    rep = asset.find("media-rep")
    assert rep.get("src") == "file:///a/uusi%20%5Bmix%5D.wav"
    assert rep.find("bookmark") is None


def test_multicam_export_uses_the_processed_audio(fixture_dir, validate_fcpxml):
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    replacements = {
        k: f"/mix/{k[:-4]} [mix].wav" for k in tl.media_by_key() if k.endswith(".wav")
    }
    xml = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 36.0)],
        [("host Track1", "Host")],
        Fraction(0),
        Fraction(36),
        "Käsitelty",
        replacements=replacements,
    )
    assert xml.count("%5Bmix%5D.wav") == len(replacements)
    # Kameroihin ei kosketa: kuva tulee yhä alkuperäisistä tiedostoista.
    assert "WIDE 01.mp4" in xml
    validate_fcpxml(xml, "mixed.fcpxml")


def test_multicam_export_keeps_the_raw_audio_as_a_muted_angle(
    fixture_dir, validate_fcpxml
):
    """Käsitelty soi, raaka on rinnalla vaimennettuna.

    Ohjaus vie alkuperäisen viittauksen mennessään, ja liitännäisen jäljen
    kuulee vasta Final Cutissa — jolloin leikkaus on jo tehty. Kaksoiskulma on
    se paluutie, jota uusi vienti ei anna.
    """
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    replacements = {
        k: f"/mix/{k[:-4]} [mix].wav" for k in tl.media_by_key() if k.endswith(".wav")
    }
    xml = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 36.0)],
        [("host Track1", "Host")],
        Fraction(0),
        Fraction(36),
        "Käsitelty",
        replacements=replacements,
    )
    root = ET.fromstring(xml)
    clip = root.find(".//spine/mc-clip")
    live = [
        (a.get("angleID"), a.find("audio-role-source"))
        for a in clip.findall('mc-source[@srcEnable="audio"]')
    ]
    # Mykkä kulma on «none», ei «audio» + active="0". Final Cut ei kirjoita
    # jälkimmäistä koskaan ja ratkaisee ristiriidan srcEnablen hyväksi, eli
    # kulma soi vaikka rooli sanoo toista.
    muted = [
        (a.get("angleID"), a.find("audio-role-source"))
        for a in clip.findall('mc-source[@srcEnable="none"]')
    ]
    assert [r.get("role") for _, r in live] == ["dialogue.Host"]
    assert [r.get("role") for _, r in muted] == ["dialogue.Host raw"]
    assert all(r.get("active") == "0" for _, r in muted)
    assert all(r.get("active") != "0" for _, r in live)
    # Kaksonen on eri kulma, ei sama kahdesti.
    assert live[0][0] != muted[0][0]

    # Käsitelty on siinä kulmassa jota soitetaan, raaka kaksosessa.
    assets = {a.get("id"): a for a in root.iter("asset")}

    def src_of(angle_id):
        angle = next(a for a in root.iter("mc-angle") if a.get("angleID") == angle_id)
        ref = next(c.get("ref") for c in angle.iter() if c.get("ref") in assets)
        return assets[ref].find("media-rep").get("src")

    assert "%5Bmix%5D" in src_of(live[0][0])
    assert "%5Bmix%5D" not in src_of(muted[0][0])
    validate_fcpxml(xml, "raw-twin.fcpxml")


def test_the_angle_carries_the_role_its_mc_source_names(fixture_dir):
    """``mc-source`` nimeää roolin; kulman on kannettava sitä.

    Kulma kopioidaan lähteestä, joten sen ääni jää Final Cutin
    oletusaliroolille ``dialogue.dialogue-1``. Jos ``mc-source`` viittaa
    puhujakohtaiseen aliroolin jota kulmassa ei ole, mitään ei tapahdu: XML
    kelpaa DTD:lle, tuonti onnistuu, eikä ``active="0"`` osu mihinkään.
    Raaka kaksonen soi käsitellyn päällä, ja sen kuulee vasta kuuntelemalla.

    Näin kävi kerran oikeassa jaksossa.
    """
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    replacements = {
        k: f"/mix/{k[:-4]} [mix].wav" for k in tl.media_by_key() if k.endswith(".wav")
    }
    xml = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 36.0)],
        [("host Track1", "Host")],
        Fraction(0),
        Fraction(36),
        "Roolit",
        replacements=replacements,
    )
    root = ET.fromstring(xml)
    by_angle = {a.get("angleID"): a for a in root.iter("mc-angle")}
    named = [
        (src.get("angleID"), src.find("audio-role-source").get("role"))
        for src in root.iter("mc-source")
        if src.get("srcEnable") == "audio" and src.find("audio-role-source") is not None
    ]
    assert named, "mikkikulmia ei löytynyt"
    for angle_id, role in named:
        angle = by_angle[angle_id]
        carried = {c.get("audioRole") for c in angle.iter() if c.get("audioRole")}
        assert carried == {role}, (
            f"kulma {angle.get('name')!r} kantaa roolia {carried} mutta "
            f"mc-source viittaa rooliin {role!r}"
        )


def test_a_redirected_asset_stops_claiming_to_be_the_old_media(fixture_dir):
    """``uid`` voittaa ``src``:n, aivan kuten ``bookmark``.

    Final Cut tunnistaa median tunnuksesta eikä polusta. Jos käsitelty
    tiedosto kantaa alkuperäisen tunnusta, se väittää olevansa sama media —
    ja koska raaka kaksonen kantaa samaa tunnusta ja lisäksi bookmarkin,
    Final Cut yhdistää ne ja valitsee raa'an.

    Vienti kuulosti silloin oikealta mutta mittasi -43 LUFS: jokainen
    «käsitelty» kulma soitti raakaa ääntä, eikä mikään kertonut siitä.
    """
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    replacements = {
        k: f"/mix/{k[:-4]} [mix].wav" for k in tl.media_by_key() if k.endswith(".wav")
    }
    xml = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 36.0)],
        [("host Track1", "Host")],
        Fraction(0),
        Fraction(36),
        "Tunnukset",
        replacements=replacements,
    )
    root = ET.fromstring(xml)
    processed, raw = [], []
    for asset in root.iter("asset"):
        rep = asset.find("media-rep")
        if rep is None or not rep.get("src", "").endswith(".wav"):
            continue
        (processed if "%5Bmix%5D" in rep.get("src") else raw).append(asset)
    assert processed and raw

    for asset in processed:
        assert "uid" not in asset.attrib, (
            f"käsitelty {asset.get('name')!r} väittää yhä olevansa vanha media"
        )
        assert asset.find("media-rep").find("bookmark") is None

    # Kaksonen saa pitää tunnuksensa: se osoittaa oikeasti alkuperäiseen.
    used = {a.get("uid") for a in raw if a.get("uid")}
    assert not (used & {a.get("uid") for a in processed if a.get("uid")})


def test_the_role_is_on_the_channel_source_not_only_the_attribute(
    fixture_dir, validate_fcpxml
):
    """``audioRole`` yksin ei riitä monikameran kulmassa.

    Final Cut ohittaa sen ja jättää kulman oletusaliroolille
    «Dialogue-1» — mitattu tuomalla molemmat versiot sisään. Toimiva tapa on
    ``<audio-channel-source>``, joka nimeää komponentin kanavittain. Ilman
    sitä puhujakohtaisia rooleja ei ole, eikä ``mc-source``in nimeämää
    roolia ole olemassa siinä kulmassa johon se viittaa.
    """
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    replacements = {
        k: f"/mix/{k[:-4]} [mix].wav" for k in tl.media_by_key() if k.endswith(".wav")
    }
    xml = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 36.0)],
        [("host Track1", "Host")],
        Fraction(0),
        Fraction(36),
        "Kanavaroolit",
        replacements=replacements,
    )
    root = ET.fromstring(xml)
    named = {}
    for src in root.iter("mc-source"):
        source = src.find("audio-role-source")
        if source is not None and source.get("role") != "dialogue.dialogue-1":
            named[src.get("angleID")] = source.get("role")
    assert named, "mikkikulmia ei löytynyt"

    for angle in root.iter("mc-angle"):
        role = named.get(angle.get("angleID"))
        if not role:
            continue
        sources = list(angle.iter("audio-channel-source"))
        assert sources, f"kulmalta {angle.get('name')!r} puuttuu audio-channel-source"
        assert {s.get("role") for s in sources} == {role}
        # Kanavat luetellaan assetin mukaan; monosta tulee "1".
        assert all(s.get("srcCh") for s in sources)
    validate_fcpxml(xml, "channel-roles.fcpxml")


def test_raw_twin_only_appears_for_processed_tracks(fixture_dir):
    """Ilman käsittelyä ei ole mitään mistä varmistua: ei kaksosta."""
    _, xml = _multicam_cut(fixture_dir)
    assert "-raw" not in xml
    assert ET.fromstring(xml).find(".//spine/mc-clip") is not None


def test_multicam_room_tone_is_one_lane_with_its_own_role(fixture_dir, validate_fcpxml):
    """Tilaääni ei ole kulma vaan liitetty klippi: kuva vaihtuu, ääni jatkuu."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    room = [
        (k, f"/mix/{k[:-4]} [room].wav")
        for k in tl.media_by_key()
        if k.startswith("WIDE")
    ]
    xml = build_multicam_fcpxml(
        tl,
        [
            Segment("CLOSE_A", "Host", 0.0, 18.0),
            Segment("CLOSE_B", "Guest", 18.0, 36.0),
        ],
        [("host Track1", "Host")],
        Fraction(0),
        Fraction(36),
        "Tilaäänellä",
        room=room,
    )
    root = ET.fromstring(xml)
    clips = root.findall(".//mc-clip/asset-clip")
    assert len(clips) == len(room)
    # Osat eivät mene päällekkäin, joten ne kuuluvat samalle lanelle.
    assert {c.get("lane") for c in clips} == {"-1"}
    assert {c.get("audioRole") for c in clips} == {"effects.Tilaääni"}
    # Molemmat liitetään ensimmäiseen klippiin, ei omiinsa.
    hosts = [
        c for c in root.findall(".//spine/mc-clip") if c.find("asset-clip") is not None
    ]
    assert len(hosts) == 1
    validate_fcpxml(xml, "room.fcpxml")


def test_room_asset_has_no_video(fixture_dir):
    """Tilaääni on WAV, joten sen assetissa ei saa luvata kuvaa."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    room = [
        (k, f"/mix/{k[:-4]} [room].wav")
        for k in tl.media_by_key()
        if k.startswith("WIDE 01")
    ]
    xml = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 36.0)],
        [],
        Fraction(0),
        Fraction(36),
        "Tilaääni",
        room=room,
    )
    asset = next(
        a
        for a in ET.fromstring(xml).iter("asset")
        if (a.get("name") or "").endswith("tilaääni")
    )
    assert asset.get("hasAudio") == "1"
    assert asset.get("hasVideo") is None and asset.get("format") is None


def test_flat_export_uses_the_processed_audio(fixture_dir, validate_fcpxml):
    tl = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    by_key = {m.key: m for m in tl.media}
    xml = build_fcpxml(
        by_key,
        [Segment("WIDE.mp4", "Laaja", 0.0, 20.0)],
        [("MIC_A.wav", "Host")],
        tl.frame_duration,
        Fraction(0),
        Fraction(20),
        "Käsitelty",
        replacements={"MIC_A.wav": "/mix/MIC_A [mix].wav"},
    )
    assert "MIC_A%20%5Bmix%5D.wav" in xml
    assert "WIDE.mp4" in xml
    validate_fcpxml(xml, "flat-mixed.fcpxml")


def test_flat_export_keeps_the_raw_audio_on_a_disabled_lane(
    fixture_dir, validate_fcpxml
):
    """Littanassa kaksonen on lane, ei kulma — muuten sama sääntö.

    Kaksoset menevät alimmille laneille, jotta käsittelyn kytkeminen päälle ei
    siirrä sitä mikkiä jota leikkaaja katsoo lanella −1.
    """
    tl = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    by_key = {m.key: m for m in tl.media}
    xml = build_fcpxml(
        by_key,
        [Segment("WIDE.mp4", "Laaja", 0.0, 20.0)],
        [("MIC_A.wav", "Host"), ("MIC_B.wav", "Guest")],
        tl.frame_duration,
        Fraction(0),
        Fraction(20),
        "Käsitelty",
        replacements={"MIC_A.wav": "/mix/MIC_A [mix].wav"},
    )
    root = ET.fromstring(xml)
    attached = root.findall(".//spine/asset-clip/asset-clip")
    lanes = {c.get("audioRole"): c.get("lane") for c in attached}
    # Kaksonen on viimeisenä: lomitettuna se olisi lanella −2 ja työntäisi
    # Guestin alas pelkästään siksi että Hostin ääni käsiteltiin.
    assert lanes["dialogue.Host"] == "-1"
    assert lanes["dialogue.Guest"] == "-2"
    assert lanes["dialogue.Host raw"] == "-3"

    raw = next(c for c in attached if c.get("audioRole") == "dialogue.Host raw")
    assert raw.get("enabled") == "0"
    # Vain käsitelty saa kaksosen: MIC_B meni läpi raakana eikä tarvitse sitä.
    assert "dialogue.Guest raw" not in lanes

    # Kaksonen osoittaa alkuperäiseen, käsitelty ei.
    assets = {a.get("id"): a for a in root.iter("asset")}

    def src_of(clip):
        return assets[clip.get("ref")].find("media-rep").get("src")

    live = next(c for c in attached if c.get("audioRole") == "dialogue.Host")
    assert "%5Bmix%5D" in src_of(live)
    assert "%5Bmix%5D" not in src_of(raw)
    assert raw.get("start") == live.get("start")
    assert raw.get("duration") == live.get("duration")
    assert raw.get("offset") == live.get("offset")
    validate_fcpxml(xml, "flat-raw.fcpxml")


def test_flat_raw_twin_only_appears_for_processed_tracks(fixture_dir):
    """Ilman käsittelyä ei ole mitään mistä varmistua: ei kaksosta."""
    _, xml = _cut(fixture_dir)
    assert " raw" not in xml
    assert 'enabled="0"' not in xml


def test_room_asset_declares_mono(fixture_dir):
    """Tilaääni kirjoitetaan monona, joten assetti ei saa luvata stereota."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    room = [
        (k, f"/mix/{k[:-4]} [room].wav")
        for k in tl.media_by_key()
        if k.startswith("WIDE 01")
    ]
    xml = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 36.0)],
        [],
        Fraction(0),
        Fraction(36),
        "Mono",
        room=room,
    )
    asset = next(
        a
        for a in ET.fromstring(xml).iter("asset")
        if (a.get("name") or "").endswith("tilaääni")
    )
    assert asset.get("audioChannels") == "1"
    assert asset.get("audioSources") == "1"


# ------------------------------------------------------ säätimet mukaan XML:ään


def _settings():
    from autoraffkat.model import Globals, TrackConfig
    from autoraffkat.project import ProjectSettings

    settings = ProjectSettings(
        tracks={"MIC_A.wav": TrackConfig(role="mic", speaker="Host", gain_db=-3.0)},
        globals=Globals(rhythm="hectic", min_shot=1.4, overlap_rule="louder"),
    )
    settings.audio.enabled = True
    return settings


def test_sequence_children_stay_in_dtd_order(fixture_dir):
    """DTD: ``sequence (note?, spine, metadata?)``. Järjestys on osa sääntöä."""
    _, xml = _cut(fixture_dir, settings=_settings())
    sequence = ET.fromstring(xml).find(".//sequence")
    assert [c.tag for c in sequence] == ["note", "spine", "metadata"]


def test_note_says_what_the_settings_were(fixture_dir):
    """Notes-kenttä on se paikka jonka Final Cut näyttää ilman kaivamista."""
    _, xml = _cut(fixture_dir, settings=_settings())
    note = ET.fromstring(xml).find(".//sequence/note").text
    assert "autoraffkat" in note
    assert "1.4" in note  # lyhin kuva


def test_metadata_carries_every_setting_back(fixture_dir):
    """Koko asetusjoukko on luettavissa viennistä, ei vain tiivistelmä."""
    import json

    from autoraffkat.project import ProjectSettings

    settings = _settings()
    _, xml = _cut(fixture_dir, settings=settings)
    md = {
        m.get("key"): m.get("value")
        for m in ET.fromstring(xml).findall(".//sequence/metadata/md")
    }
    assert md["fi.autoraffkat.rhythm"] == "hectic"
    assert md["fi.autoraffkat.min_shot"] == "1.4"
    assert md["fi.autoraffkat.overlap_rule"] == "louder"
    assert md["fi.autoraffkat.audio.enabled"] == "1"
    assert md["fi.autoraffkat.source"] == "jakso.fcpxml"

    again = ProjectSettings.from_json(json.loads(md["fi.autoraffkat.settings"]))
    assert again.to_json() == settings.to_json()


def test_settings_are_optional(fixture_dir):
    """Ilman asetuksia vienti on entisensä."""
    _, xml = _cut(fixture_dir)
    sequence = ET.fromstring(xml).find(".//sequence")
    assert [c.tag for c in sequence] == ["spine"]


def test_multicam_sequence_carries_the_settings(fixture_dir):
    _, xml = _multicam_cut(fixture_dir, settings=_settings())
    sequence = ET.fromstring(xml).find(".//sequence")
    assert [c.tag for c in sequence] == ["note", "spine", "metadata"]
    assert (
        sequence.find("metadata/md[@key='fi.autoraffkat.rhythm']").get("value")
        == "hectic"
    )


def test_settings_output_passes_the_fcp_dtd(fixture_dir, validate_fcpxml):
    _, xml = _multicam_cut(fixture_dir, settings=_settings())
    validate_fcpxml(xml, "settings.fcpxml")


# ---------------------------------------------------------------- reaktiot


def _roles_for(tl):
    from autoraffkat.analysis import resolve_roles
    from autoraffkat.model import ROLE_CLOSE, ROLE_MIC, ROLE_WIDE, TrackConfig

    return resolve_roles(tl, {
        "WIDE": TrackConfig(role=ROLE_WIDE),
        "CLOSE_A": TrackConfig(role=ROLE_CLOSE, speaker="Host"),
        "CLOSE_B": TrackConfig(role=ROLE_CLOSE, speaker="Guest"),
        "host Track1": TrackConfig(role=ROLE_MIC, speaker="Host"),
        "guest Track2": TrackConfig(role=ROLE_MIC, speaker="Guest"),
    })


def _with_reactions(fixture_dir, spans):
    from autoraffkat.reactions import Reaction

    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    xml = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 4.0),
         Segment("CLOSE_A", "Host", 4.0, 36.0)],
        [("host Track1", "Host"), ("guest Track2", "Guest")],
        Fraction(0), Fraction(36), "Reaktiotesti",
        source="multicam.fcpxml",
        reactions=[Reaction(*s) for s in spans],
        roles=_roles_for(tl),
    )
    return tl, xml


def test_reactions_go_on_their_own_lane_not_into_the_multicam(fixture_dir):
    """Spekulatiivinen kuva ei kuulu peruleikkaukseen.

    Kulmanvaihtona sen poistaminen vaatisi uuden viennin, ja siihen mennessä
    edellinen on yleensä jo tuotu Final Cutiin ja leikattu käsin. Omalta
    lanelta sen poistaa yhdellä valinnalla, ja alla oleva leikkaus on
    koskematon.
    """
    _, xml = _with_reactions(fixture_dir, [("Guest", 8.0, 9.6, 2.1)])
    root = ET.fromstring(xml)
    clips = [c for c in root.iter("mc-clip") if "reaktio" in (c.get("name") or "")]
    assert len(clips) == 1, "reaktiokuvaa ei kirjoitettu"
    assert int(clips[0].get("lane")) > 0, "reaktio ei ole omalla lanellaan"
    # Sisäkkäinen mc-clip, ei asset-clip: rakenne on Final Cutin oma.
    assert clips[0].find("mc-source") is not None
    assert clips[0].find("mc-source").get("angleID")


def test_a_reaction_clip_carries_no_audio(fixture_dir):
    """Lähikuvan liitetty klippi kantaisi sen kameran äänen, joka summautuisi
    käsiteltyjen mikkien päälle.

    Sama perhe kuin ``uid``in romahdus ja ``srcEnable``in voitto
    ``active``ista: kelvollinen XML, siisti tuonti, väärä ääni — ja sen
    huomaa vasta kuuntelemalla.
    """
    _, xml = _with_reactions(fixture_dir, [("Guest", 8.0, 9.6, 2.1)])
    clip = next(c for c in ET.fromstring(xml).iter("mc-clip")
                if "reaktio" in (c.get("name") or ""))
    source = clip.find("mc-source")
    # Verrokissa Final Cut kirjoittaa "all"; meille se toisi kameramikin
    # käsiteltyjen mikkien päälle.
    assert source.get("srcEnable") == "video"
    assert clip.get("audioRole") is None


def test_reactions_are_clipped_to_the_programme(fixture_dir):
    """Ohjelman ulkopuolelle jäävästä ei kirjoiteta mitään: siitä ei ole
    tietoa eikä vienti käytä sitä."""
    _, xml = _with_reactions(fixture_dir, [("Guest", 35.0, 40.0, 2.0),
                                           ("Guest", -5.0, -1.0, 2.0)])
    clips = [c for c in ET.fromstring(xml).iter("mc-clip")
             if "reaktio" in (c.get("name") or "")]
    assert len(clips) == 1
    assert parse_time(clips[0].get("duration")) <= Fraction(1)


def test_no_reactions_writes_nothing_new(fixture_dir):
    """Oletus ei saa muuttaa vientiä millään tavalla."""
    _, plain = _multicam_cut(fixture_dir)
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    same = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 4.0),
         Segment("CLOSE_A", "Host", 4.0, 12.0),
         Segment("CLOSE_B", "Guest", 12.0, 30.0),
         Segment("WIDE", "Laaja", 30.0, 36.0)],
        [("host Track1", "Host"), ("guest Track2", "Guest")],
        Fraction(0), Fraction(36), "Monikameratesti",
        source="multicam.fcpxml", reactions=[], roles=_roles_for(tl),
    )
    assert same == plain


def test_a_reaction_export_validates_against_final_cut(fixture_dir, validate_fcpxml):
    """Oma lukijamme hyväksyy paljon enemmän kuin tuoja."""
    _, xml = _with_reactions(fixture_dir, [("Guest", 8.0, 9.6, 2.1),
                                           ("Host", 20.0, 21.6, 1.8)])
    validate_fcpxml(xml)


def test_a_reaction_matches_final_cuts_own_structure(fixture_dir):
    """Verrattuna siihen mitä Final Cut itse kirjoittaa.

    Ensimmäinen yritys oli ``asset-clip`` joka viittasi kulman assettiin:
    kelvollista DTD:tä, ja aikajanalla ei näkynyt mitään. Käsin tehty
    verrokki paljasti oikean muodon — sisäkkäinen ``mc-clip``, jonka
    ``mc-source`` valitsee kulman ``angleID``:llä, samalla ``ref``illä kuin
    isäntä. Tämä testi pitää sen muodon.
    """
    tl, xml = _with_reactions(fixture_dir, [("Guest", 8.0, 9.6, 2.1)])
    root = ET.fromstring(xml)
    host = root.find(".//spine/mc-clip")
    clip = next(c for c in root.iter("mc-clip")
                if "reaktio" in (c.get("name") or ""))
    assert clip.get("ref") == host.get("ref"), "eri media kuin isäntä"
    assert clip.find("mc-source").get("angleID"), "kulmaa ei valittu"
    # Synkroninen sijoitus: offset ja start ovat median omaa aikaa, joten
    # ne ovat sama luku. Verrokissa ne eroavat vain käsin raahaamisen takia.
    assert clip.get("offset") == clip.get("start")


def test_a_reaction_is_skipped_when_the_angle_is_not_in_this_part(fixture_dir):
    """Kulma voi puuttua osasta kokonaan.

    Silloin siihen leikkaaminen tuottaisi kuvan jota ei ole. Kirjoittamatta
    jättäminen on oikea vastaus — mutta vain siksi että kulma puuttuu, ei
    hiljaisena epäonnistumisena muusta syystä.
    """
    from autoraffkat.reactions import Reaction

    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    roles = _roles_for(tl)
    roles.closes["Guest"] = "EI_OLE_TALLAISTA_RAITAA"
    xml = build_multicam_fcpxml(
        tl,
        [Segment("WIDE", "Laaja", 0.0, 4.0), Segment("CLOSE_A", "Host", 4.0, 36.0)],
        [("host Track1", "Host"), ("guest Track2", "Guest")],
        Fraction(0), Fraction(36), "Reaktiotesti", source="multicam.fcpxml",
        reactions=[Reaction("Guest", 8.0, 9.6, 2.1)], roles=roles,
    )
    assert not [c for c in ET.fromstring(xml).iter("mc-clip")
                if "reaktio" in (c.get("name") or "")]


def test_clips_carry_the_speaker_as_a_keyword(fixture_dir, validate_fcpxml):
    """Selaimessa monikameraklipin nimi on median oma, ei meidän antamamme.

    Kaikki kuvat näyttivät siksi samalta — «A-osa», «B-osa» — ja
    hakemiston Tags-välilehti oli tyhjä. Avainsana on se paikka jossa
    Final Cut oikeasti erottaa ne, ja DTD vaatii sen **lanejen jälkeen**:
    mc-source, sisäkkäiset klipit, vasta sitten avainsanat.
    """
    _, xml = _with_reactions(fixture_dir, [("Guest", 8.0, 9.6, 2.1)])
    root = ET.fromstring(xml)
    host = root.find(".//spine/mc-clip")
    tags = [k.get("value") for k in host.findall("keyword")]
    assert tags, "puhujaa ei merkitty avainsanaksi"
    kids = [c.tag for c in host]
    assert kids.index("keyword") > kids.index("mc-clip"), kids
    reaction = next(c for c in root.iter("mc-clip")
                    if "reaktio" in (c.get("name") or ""))
    assert any("Reaktio" in (k.get("value") or "")
               for k in reaction.findall("keyword"))
    validate_fcpxml(xml)
