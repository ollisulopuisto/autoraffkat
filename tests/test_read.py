from fractions import Fraction

import pytest

from autoraffkat.fcpxml.read import ReadError, read_fcpxml


def test_sync_clip(fixture_dir):
    tl = read_fcpxml(str(fixture_dir / "sync.fcpxml"))
    assert tl.kind == "sync-clip"
    assert tl.frame_duration == Fraction(1, 25)
    assert [m.key for m in tl.media] == [
        "WIDE.mp4",
        "CLOSE_A.mp4",
        "CLOSE_B.mp4",
        "MIC_A.wav",
        "MIC_B.wav",
    ]
    wide = tl.media[0]
    assert wide.has_video and not wide.has_audio
    assert wide.width == 1920 and wide.height == 1080
    assert wide.placements[0].offset == 0


def test_project_offsets_are_relative_to_parent_start(fixture_dir):
    """Liitetyn klipin offset on isännän paikallisessa ajassa, ei aikajanan."""
    tl = read_fcpxml(str(fixture_dir / "project.fcpxml"))
    assert tl.kind == "project"
    for item in tl.media:
        placement = item.placements[0]
        assert placement.offset == 0, item.key
        assert placement.start == 1  # spinellä start=25/25s
        assert placement.duration == 35


def test_file_time_mapping(fixture_dir):
    tl = read_fcpxml(str(fixture_dir / "project.fcpxml"))
    mic = next(m for m in tl.media if m.key == "MIC_A.wav")
    # Aikajanan hetki 0 vastaa tiedoston sekuntia 1, koska spine alkaa start=1s.
    assert mic.file_time_at(Fraction(0)) == 1
    assert mic.file_time_at(Fraction(10)) == 11
    assert mic.file_time_at(Fraction(100)) is None


def test_bad_root(tmp_path):
    path = tmp_path / "x.fcpxml"
    path.write_text("<notfcpxml/>")
    with pytest.raises(ReadError):
        read_fcpxml(str(path))


def test_no_timeline(tmp_path):
    path = tmp_path / "x.fcpxml"
    path.write_text('<?xml version="1.0"?><fcpxml version="1.10"><resources/></fcpxml>')
    with pytest.raises(ReadError):
        read_fcpxml(str(path))


def test_multicam_groups_angles_across_parts(fixture_dir):
    """Kaksi osaa, viisi kulmaa: kymmenen assettia mutta viisi raitaa."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    assert tl.kind == "multicam"
    assert len(tl.media) == 10
    assert [t.key for t in tl.tracks] == [
        "WIDE",
        "CLOSE_A",
        "CLOSE_B",
        "host Track1",
        "guest Track2",
    ]
    for track in tl.tracks:
        assert len(track.media_keys) == 2, track.key
        # Raidan väli kattaa molemmat osat, ei vain jälkimmäistä.
        assert tl.track_span(track.key) == (0, 36)


def test_multicam_content_is_clipped_to_its_part(fixture_dir):
    """Kulman sisältö on koko multicamin pituinen, mc-clip rajaa sen."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    by_key = tl.media_by_key()
    first, second = by_key["WIDE 01.mp4"], by_key["WIDE 02.mp4"]
    assert [(p.offset, p.duration) for p in first.placements] == [(0, 18)]
    assert [(p.offset, p.duration) for p in second.placements] == [(18, 18)]
    # Osat eivät saa mennä päällekkäin, muuten verhokäyrä kohdistuisi väärin.
    assert first.timeline_end == second.timeline_start


def test_multicam_angle_gap_shifts_source_time(fixture_dir):
    """Kulman alussa oleva aukko siirtää lähdeaikaa, ei aikajanaa."""
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    wide = tl.media_by_key()["WIDE 02.mp4"]  # osassa B sekunnin aukko
    assert wide.file_time_at(Fraction(18)) == 18
    assert wide.file_time_at(Fraction(30)) == 30


def test_multicam_records_its_parts(fixture_dir):
    tl = read_fcpxml(str(fixture_dir / "multicam.fcpxml"))
    assert [(mc.offset, mc.duration, mc.start) for mc in tl.multicams] == [
        (0, 18, 0),
        (18, 18, 18),
    ]
    assert all(len(mc.angle_ids) == 5 for mc in tl.multicams)


def test_windows_path_survives_the_url_round_trip(monkeypatch):
    """Windowsin polku ei kelpaa file-URLiin sellaisenaan.

    ``"file://" + r"C:\\..."`` luki koko polun URLin netlociksi ja jätti polun
    tyhjäksi: yhtään mediatiedostoa ei löytynyt, ja vienti kaatui vasta
    puuttuviin tiedostoihin. Muunnos on alustakohtainen, joten Windowsin
    toteutus ajetaan tässä myös macOSissa — muuten regressio näkyisi vasta
    CI:n Windows-ajossa.
    """
    import nturl2path

    from autoraffkat.fcpxml import read as reader
    from autoraffkat.fcpxml import write as writer

    monkeypatch.setattr(writer, "pathname2url", nturl2path.pathname2url)
    monkeypatch.setattr(reader, "url2pathname", nturl2path.url2pathname)

    path = r"C:\Users\ohjaaja\jakso 2\host [mix].wav"
    url = writer.file_url(path)
    assert url == "file:///C:/Users/ohjaaja/jakso%202/host%20%5Bmix%5D.wav"
    assert reader._src_to_path(url) == path

    # Verkkolevy on ``file://palvelin/jako``: palvelin kuuluu polkuun, ei
    # URLin isäntäkenttään jätettäväksi.
    unc = r"\\arkisto\kuvat\host.mov"
    assert reader._src_to_path(writer.file_url(unc)) == unc
