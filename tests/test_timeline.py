from fractions import Fraction

import pytest

from autoraffkat import timeline as t


@pytest.mark.parametrize("text,expected", [
    ("1001/30000s", Fraction(1001, 30000)),
    ("5s", Fraction(5)),
    ("0s", Fraction(0)),
    ("-1001/30000s", Fraction(-1001, 30000)),
    ("", Fraction(0)),
    (None, Fraction(0)),
])
def test_parse_time(text, expected):
    assert t.parse_time(text) == expected


def test_format_round_trip():
    for value in ("1001/30000s", "5s", "0s", "3603601/30000s"):
        assert t.format_time(t.parse_time(value)) == value


def test_quantize_is_exact_over_many_frames():
    """Liukuluvuilla tämä kertyisi virheeksi; Fractionilla ei."""
    fd = Fraction(1001, 30000)
    for frames in (1, 7, 1234, 216_000):
        assert t.to_frames(fd * frames, fd) == frames


def test_half_rounds_up():
    fd = Fraction(1, 25)
    assert t.to_frames(Fraction(1, 50), fd) == 1


def test_format_name():
    assert t.format_name(1920, 1080, Fraction(1, 25)) == "FFVideoFormat1080p25"
    assert t.format_name(1920, 1080, Fraction(1001, 30000)) == "FFVideoFormat1080p2997"
