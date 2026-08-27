"""Istumajärjestys kuvasta ja panorointi siitä."""

import numpy as np

from autoraffkat import staging

FIELDS = ("yaw", "roll", "size", "x", "y", "w", "h", "eyes", "smile",
          "cx", "cy", "turn", "tilt")


def table(turn, n=None, found=None):
    """Mittaustaulukko, jossa vain ``turn`` on kiinnostava."""
    values = np.asarray(turn, dtype=np.float32)
    n = len(values) if n is None else n
    out = {"times": np.arange(n, dtype=np.float32),
           "found": np.ones(n, dtype=bool) if found is None
           else np.asarray(found, dtype=bool)}
    for name in FIELDS:
        out[name] = np.zeros(n, dtype=np.float32)
    out["turn"] = values
    return out


def test_the_side_comes_from_the_head_not_the_framing():
    """Vasemmalla istuva katsoo oikealle, ja se on positiivinen ``turn``.

    Merkki on päinvastoin kuin arvaus «nenä osoittaa sinne missä istutaan».
    Oikealla jaksolla mitattuna vasemmalla istuvan mediaani oli +0,46 ja
    oikealla istuvan -0,28, molemmissa osissa sama — tarkistettu ruuduista
    eikä pääteltynä.
    """
    vasen = staging.side(table(np.full(400, 0.46)))
    oikea = staging.side(table(np.full(400, -0.28)))
    assert vasen > 0 and oikea < 0
    assert staging.order({"V": vasen, "O": oikea}) == ["V", "O"]


def test_a_speaker_without_enough_frames_has_no_side():
    """Yksittäinen ruutu voi olla mitä tahansa.

    Mittaamaton puhuja ei saa arvattua paikkaa: keskus on ainoa arvo joka
    ei ole koskaan väärin.
    """
    assert np.isnan(staging.side(table(np.full(3, 0.5))))
    # Kasvot löytyivät liian harvasta ruudusta: sama tilanne.
    found = np.zeros(400, dtype=bool)
    found[:2] = True
    assert np.isnan(staging.side(table(np.full(400, 0.5), found=found)))
    # Kevyt otos riittää: viisi ruutua on jo vastaus, ks. SIDE_MIN_FRAMES.
    assert staging.side(table(np.full(5, 0.46))) > 0
    assert np.isnan(staging.side({"found": np.zeros(0, dtype=bool), "turn": []}))


def test_the_spread_is_even_and_stays_narrow():
    """Paikat jaetaan tasan, ei mitatun kulman suhteessa.

    Kulma kertoo järjestyksen luotettavasti mutta etäisyyden ei lainkaan:
    se riippuu tuolien asennosta ja objektiivista. Kolme puhujaa on siis
    vasen, keskus, oikea.
    """
    kaksi = staging.pans({"V": 0.5, "O": -0.5})
    assert kaksi["V"] == -3.0 and kaksi["O"] == 3.0

    kolme = staging.pans({"V": 0.5, "K": 0.02, "O": -0.5})
    assert kolme["K"] == 0.0
    assert kolme["V"] == -4.0 and kolme["O"] == 4.0

    # Epätasaisesti mitatut kulmat eivät tee epätasaisia paikkoja.
    vino = staging.pans({"V": 0.9, "K": 0.85, "O": -0.1})
    assert sorted(vino.values()) == [-4.0, 0.0, 4.0]

    # Levein sallittu on silti kapea: kuulokkeilla tätä ei juuri huomaa.
    for count, width in staging.PAN_WIDTH.items():
        sides = {f"p{i}": 1.0 - i * 0.1 for i in range(count)}
        values = staging.pans(sides)
        assert max(values.values()) <= 6.0
        assert min(values.values()) >= -6.0


def test_more_than_five_speakers_are_not_panned():
    """Kuudella paikat ovat niin lähellä ettei ero ole enää paikka.

    Silloin keskeltä on parempi kuin melkein keskeltä.
    """
    sides = {f"p{i}": 1.0 - i * 0.1 for i in range(6)}
    assert set(staging.pans(sides).values()) == {0.0}
    # Yksi puhuja on jo keskellä: ei ole mihin nähden olla sivussa.
    assert staging.pans({"yksin": 0.4}) == {"yksin": 0.0}


def test_an_unmeasured_speaker_stays_in_the_centre():
    """Paikkaa jota ei tiedetä ei arvata."""
    values = staging.pans({"V": 0.5, "O": -0.5, "X": float("nan")})
    assert values["X"] == 0.0
    assert values["V"] == -3.0 and values["O"] == 3.0
    assert staging.order({"V": 0.5, "O": -0.5, "X": float("nan")})[-1] == "X"
