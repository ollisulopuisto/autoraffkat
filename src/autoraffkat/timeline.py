"""FCPXML:n rationaaliaika.

Final Cut ilmaisee kaikki ajat murtolukuina sekunteja: ``"1001/30000s"``,
``"5s"``, ``"0s"``. Liukuluvut eivät riitä, koska pyöristysvirhe kertyy
tuhansien kehysten yli ja aikajanalle jää aukkoja. Kaikki XML:stä luettu ja
XML:ään kirjoitettu aika kulkee tämän moduulin läpi Fractionina; vain
analyysikerros käyttää sekunteja liukulukuna.
"""

from __future__ import annotations

from fractions import Fraction

ZERO = Fraction(0)

# Final Cutin nimetyt formaatit ruutunopeuden mukaan.
FPS_LABELS = {
    Fraction(24000, 1001): "2398",
    Fraction(24, 1): "24",
    Fraction(25, 1): "25",
    Fraction(30000, 1001): "2997",
    Fraction(30, 1): "30",
    Fraction(50, 1): "50",
    Fraction(60000, 1001): "5994",
    Fraction(60, 1): "60",
    Fraction(120, 1): "120",
}


def parse_time(value: str | None, default: Fraction = ZERO) -> Fraction:
    """Lukee FCPXML-ajan Fractioniksi. ``None`` ja tyhjä antavat oletuksen."""
    if value is None:
        return default
    text = value.strip()
    if not text:
        return default
    if text.endswith("s"):
        text = text[:-1]
    if not text:
        return default
    negative = text.startswith("-")
    if negative:
        text = text[1:]
    if "/" in text:
        num, _, den = text.partition("/")
        result = Fraction(int(num), int(den))
    else:
        result = Fraction(int(text))
    return -result if negative else result


def format_time(value: Fraction) -> str:
    """Kirjoittaa Fractionin FCPXML-ajaksi."""
    value = Fraction(value)
    if value.denominator == 1:
        return f"{value.numerator}s"
    return f"{value.numerator}/{value.denominator}s"


def to_frames(seconds: Fraction | float, frame_duration: Fraction) -> int:
    """Kvantisoi ajan lähimpään kehykseen."""
    if not isinstance(seconds, Fraction):
        seconds = Fraction(seconds).limit_denominator(1_000_000)
    exact = seconds / frame_duration
    # Fractionin pyöristys puoliluvuissa ylöspäin, jotta tulos ei riipu
    # liukulukujen banker's roundingista.
    floor = exact.numerator // exact.denominator
    remainder = exact - floor
    return floor + (1 if remainder >= Fraction(1, 2) else 0)


def frames_to_time(frames: int, frame_duration: Fraction) -> Fraction:
    """Kehysmäärä ajaksi."""
    return frame_duration * frames


def frames_str(frames: int, frame_duration: Fraction) -> str:
    """Kehysmäärä suoraan FCPXML-ajaksi. Tulos on aina kehyksen tarkka."""
    return format_time(frames_to_time(frames, frame_duration))


def fps_of(frame_duration: Fraction) -> Fraction:
    """Ruutunopeus kehyksen kestosta."""
    return 1 / frame_duration


def format_name(width: int, height: int, frame_duration: Fraction) -> str:  # noqa: ARG001
    """Final Cutin formaattinimi, esim. ``FFVideoFormat1080p25``.

    ``width`` on mukana kutsujien vuoksi ja koska nimi on parin (leveys,
    korkeus) nimi; Final Cutin oma nimeäminen käyttää vain korkeutta.
    """
    label = FPS_LABELS.get(fps_of(frame_duration))
    if label is None:
        return "FFVideoFormatRateUndefined"
    return f"FFVideoFormat{height}p{label}"


def parse_fps(text: str) -> Fraction:
    """Lukee käyttäjän tai ffprobin ilmaiseman ruutunopeuden."""
    text = str(text).strip()
    named = {
        "23.976": Fraction(24000, 1001),
        "29.97": Fraction(30000, 1001),
        "59.94": Fraction(60000, 1001),
        "119.88": Fraction(120000, 1001),
    }
    if text in named:
        return named[text]
    if "/" in text:
        num, _, den = text.partition("/")
        return Fraction(int(num), int(den))
    return Fraction(text).limit_denominator(1000)
