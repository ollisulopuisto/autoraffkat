"""Lähteen valinta. Ei palvelinta, ei mediaa — pelkkää hakemiston lukua."""

import os

from autoraffkat import pick


def _touch(path, text="<fcpxml/>"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def test_bundle_resolves_to_its_xml(tmp_path):
    inner = _touch(str(tmp_path / "jakso.fcpxmld" / "Info.fcpxml"))
    assert pick.resolve(str(tmp_path / "jakso.fcpxmld")) == inner
    # Suora polku kelpaa sellaisenaan.
    assert pick.resolve(inner) == inner


def test_candidates_finds_both_muodot(tmp_path):
    plain = _touch(str(tmp_path / "kasin.fcpxml"))
    inner = _touch(str(tmp_path / "jakso.fcpxmld" / "Info.fcpxml"))
    assert set(pick.candidates(str(tmp_path))) == {plain, inner}


def test_own_export_is_not_a_candidate(tmp_path):
    """Silmukassa palataan lähteeseen, ei valmiiseen leikkaukseen."""
    source = _touch(str(tmp_path / "jakso.fcpxml"))
    _touch(str(tmp_path / "jakso-leikattu.fcpxml"))
    _touch(str(tmp_path / "jakso-leikattu.fcpxmld" / "Info.fcpxml"))
    assert pick.candidates(str(tmp_path)) == [source]


def test_candidates_are_newest_first(tmp_path):
    old = _touch(str(tmp_path / "vanha.fcpxml"))
    new = _touch(str(tmp_path / "uusi.fcpxml"))
    os.utime(old, (1_000_000, 1_000_000))
    assert pick.candidates(str(tmp_path))[0] == new


def test_label_names_the_bundle_not_its_contents(tmp_path):
    inner = _touch(str(tmp_path / "episode 12.fcpxmld" / "Info.fcpxml"))
    assert pick.label(inner) == "episode 12.fcpxmld"
    assert pick.label(str(tmp_path / "kasin.fcpxml")) == "kasin.fcpxml"


def test_single_candidate_needs_no_question(tmp_path):
    only = _touch(str(tmp_path / "jakso.fcpxml"))
    assert pick.pick(str(tmp_path)) == only


def test_without_a_terminal_nothing_is_asked(tmp_path, monkeypatch):
    """Putkessa ei saa jäädä odottamaan vastausta eikä avata ikkunaa."""
    monkeypatch.setattr(pick, "interactive", lambda: False)
    newest = _touch(str(tmp_path / "b.fcpxml"))
    older = _touch(str(tmp_path / "a.fcpxml"))
    os.utime(older, (1_000_000, 1_000_000))
    assert pick.ask([newest, older]) == newest
    assert pick.native(str(tmp_path)) is None
    # Tyhjä hakemisto ei avaa ikkunaa vaan palauttaa tyhjän.
    empty = tmp_path / "tyhja"
    empty.mkdir()
    assert pick.pick(str(empty)) is None


def test_missing_directory_is_not_an_error(tmp_path):
    assert pick.candidates(str(tmp_path / "ei-ole")) == []
