"""Ristivuodon vähennys.

Testit mittaavat kahta asiaa, ja molemmat ovat pakollisia: vuoto lähtee, ja
kohteen oma puhe **ei** lähde. Pelkkä ensimmäinen menisi läpi myös
vähennyksellä joka syö puheen, ja se kuuluisi vasta viennin jälkeen.
"""

import numpy as np
import pytest

from autoraffkat.audio import debleed

RATE = 48000


def _room(seconds=240.0, seed=3):
    """Kaksi vuorottelevaa puhujaa ja vuotopolku toisesta toiseen."""
    from scipy import signal as sig

    rng = np.random.default_rng(seed)
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    source = rng.normal(size=n) * (np.sin(2 * np.pi * 0.11 * t) > 0.0)
    own = rng.normal(size=n) * (np.sin(2 * np.pi * 0.11 * t) < -0.3)
    # Suora ääni 5 ms:n päässä ja kaksi varhaista heijastusta.
    leak = np.zeros(300)
    leak[240], leak[262], leak[290] = 0.18, -0.07, 0.04
    target = own + sig.fftconvolve(source, leak)[:n]
    solo_source = (source != 0) & (own == 0)
    solo_target = (own != 0) & (source == 0)
    return target, source, own, solo_source, solo_target


def test_bleed_goes_and_own_speech_stays():
    """Istutettu vuotopolku lähtee, eikä kohteen omaan puheeseen kosketa."""
    target, source, _, solo_source, solo_target = _room()
    out, info = debleed.remove(target, source, RATE, solo_source, solo_target)

    assert info["reason"] == ""
    assert info["reduction_db"] > 20.0, f"vuotoa lähti vain {info['reduction_db']:.1f} dB"
    assert info["kept"] > 0.9999, "kohteen oma puhe muuttui"


def test_a_subtraction_that_would_eat_speech_is_refused():
    """Väärä lähde ei saa johtaa puheen vähentämiseen.

    Tässä lähde on kohde itse, jolloin pienimmän neliösumman ratkaisu
    vähentäisi kohteen omaa puhetta lähes kokonaan. Tarkistus on olemassa
    juuri siksi, että estimaatti voi mennä pieleen hiljaa — liian vähän
    aineistoa, mikki joka on liikkunut, väärin valitut jaksot — eikä
    lopputulosta kuule kukaan ennen vientiä.
    """
    target, _, _, solo_source, solo_target = _room()
    out, info = debleed.remove(target, target, RATE, solo_source, solo_target)

    assert info["reason"] == "ate_speech"
    assert np.array_equal(out, target), "hylätty suodin päätyi silti tulokseen"


def test_too_little_solo_material_is_refused_and_says_so():
    """Muutamasta sekunnista estimoitu suodin sovittuu kohinaan."""
    target, source, _, solo_source, solo_target = _room(seconds=30.0)
    few = np.zeros_like(solo_source)
    few[: int(RATE * 5)] = solo_source[: int(RATE * 5)]
    out, info = debleed.remove(target, source, RATE, few, solo_target)

    assert info["reason"] == "too_little"
    assert np.array_equal(out, target)


def test_clean_tracks_are_left_alone():
    """Kun vuotoa ei ole, vähennystä ei tehdä eikä signaaliin kosketa."""
    _, source, own, solo_source, solo_target = _room()
    out, info = debleed.remove(own, source, RATE, solo_source, solo_target)

    # Kumpi tahansa syy kelpaa — polkua ei ole tai vähennettävää ei ole —
    # kunhan signaaliin ei kosketa.
    assert info["reason"] in ("no_path", "no_gain")
    assert np.array_equal(out, own)


def test_the_solo_mask_is_only_where_one_speaker_is_active():
    """Estimointijaksot: minä äänessä, kaikki muut vaiti."""
    from autoraffkat.audio import mix

    class Lane:
        def __init__(self, name, on):
            self.name, self.on = name, np.asarray(on, dtype=bool)

    class Grid:
        speakers = [
            Lane("A", [1, 1, 0, 0, 1]),
            Lane("B", [0, 1, 1, 0, 1]),
        ]

    solos = mix.solo_masks(Grid())
    assert list(solos["A"]) == [True, False, False, False, False]
    assert list(solos["B"]) == [False, False, True, False, False]
    # Yksi puhuja: ei ketään keneltä vuotaisi.
    class Alone:
        speakers = [Lane("A", [1, 1])]

    assert mix.solo_masks(Alone()) == {}
    assert mix.solo_masks(None) == {}


def test_the_partner_lands_on_the_same_timeline_moment():
    """Eri tiedostot, eri alut — vuotoa ei voi vähentää ennen kohdistusta."""
    from fractions import Fraction

    from autoraffkat.audio import mix

    class Placement:
        def __init__(self, offset, start, duration):
            self.offset = Fraction(offset)
            self.start = Fraction(start)
            self.duration = Fraction(duration)

        @property
        def end(self):
            return self.offset + self.duration

    class Item:
        def __init__(self, placements, asset_start=0):
            self.placements = placements
            self.asset_start = Fraction(asset_start)

    # Kohde alkaa aikajanan hetkellä 0 tiedoston hetkestä 0.
    target = Item([Placement(0, 0, 4)])
    # Lähde on samassa aikajanan kohdassa mutta alkaa tiedostossaan
    # sekunnin myöhemmin.
    source = Item([Placement(0, 1, 4)])

    audio = np.arange(4 * RATE, dtype=np.float64)
    out = mix._aligned(target, source, audio, RATE, 4 * RATE)
    # Aikajanan hetki 0 on lähdetiedoston hetki 1 s.
    assert out[0] == pytest.approx(float(RATE))
    assert out[RATE] == pytest.approx(float(2 * RATE))
