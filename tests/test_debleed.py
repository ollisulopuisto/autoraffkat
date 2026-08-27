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
    _out, info = debleed.remove(target, source, RATE, solo_source, solo_target)

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


def test_the_lag_sums_match_a_full_correlation():
    """Paloittain laskettu korrelaatio on sama luku luvulta.

    Koko ``2n-1`` mittaisen korrelaation laskeminen kahdentuhannen viiveen
    poimimiseksi kaatui pitkiin tiedostoihin: tunnin mikki on 184 miljoonaa
    näytettä, ja siitä täysi korrelaatio on 368 miljoonaa liukulukua. Oire
    oli «vuotopolkua ei saatu ratkaistua» **vain pitkissä osissa** — 20
    minuutin tiedostot menivät läpi, 64 minuutin eivät.

    Optimointi ei saa muuttaa tulosta, joten sitä verrataan siihen mitä se
    korvasi.
    """
    from scipy import signal as sig

    rng = np.random.default_rng(1)
    taps = 2048
    for n in (5000, 300000):
        a = rng.standard_normal(n)
        b = rng.standard_normal(n)
        full = sig.correlate(a, b, "full", method="fft")[n - 1:n - 1 + taps]
        blocked = debleed._lags(a, b, taps)
        scale = max(float(np.abs(full).max()), 1e-12)
        assert np.abs(full - blocked).max() / scale < 1e-12, n


def test_the_lags_cover_the_tail_when_the_signal_runs_out():
    """Lopussa signaali loppuu kesken, ja silloin nollataan eikä lyhennetä.

    Lyhentäminen jätti pitkät viiveet laskematta ja täytti vain viiveen
    nolla — suodin oli silloin yksi luku, ei polku. Tämä osui jokaiseen
    palaan jonka jälkeen aineisto loppui, eli aina viimeiseen.
    """
    rng = np.random.default_rng(5)
    taps = 64
    n = 300
    a = rng.standard_normal(n)
    b = rng.standard_normal(n)
    out = debleed._lags(a, b, taps)
    # Jokainen viive on laskettu, ei vain ensimmäinen.
    assert np.count_nonzero(out) == taps, np.count_nonzero(out)
    suora = np.array([
        float(np.dot(a[k:k + n - k], b[: n - k])) for k in range(taps)
    ])
    assert np.abs(out - suora).max() / max(np.abs(suora).max(), 1e-12) < 1e-12
