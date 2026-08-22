"""Kanavanauha. Ei tiedostoja eikä liitännäisiä — pelkkää signaalia.

Painopiste on niissä kolmessa asiassa jotka voivat rikkoa leikkauksen:
pituus, siirtymä ja taso.
"""

import numpy as np
import pytest

from autoraffkat.audio import chain
from autoraffkat.model import AudioSettings

RATE = 48000


def speech_like(seconds=6.0, rate=RATE, level=0.02):
    """Puheenkaltainen signaali: purskeita hiljaisuuden välissä."""
    rng = np.random.default_rng(7)
    n = int(seconds * rate)
    out = rng.standard_normal(n).astype(np.float32) * 0.0005  # pohjakohina
    for start in np.arange(0.5, seconds - 0.5, 1.2):
        i0 = int(start * rate)
        i1 = i0 + int(0.6 * rate)
        t = np.arange(i1 - i0) / rate
        out[i0:i1] += (level * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    return out[None, :]


def test_chain_never_changes_length():
    """Pituus on synkan koko lupaus."""
    audio = speech_like()
    out, info = chain.process(
        audio, RATE, AudioSettings(declick=True), 0.0, True, -20.0, None
    )
    assert out.shape[1] == audio.shape[1] == info.frames


def test_chain_hits_the_loudness_target():
    """Kompressointi siirtää tasoa, joten korjaus mitataan sen jälkeen."""
    pyln = pytest.importorskip("pyloudnorm")
    out, _ = chain.process(
        speech_like(20.0), RATE, AudioSettings(), 0.0, True, -20.0, None
    )
    measured = pyln.Meter(RATE).integrated_loudness(
        np.asarray(out[0], dtype=np.float64)
    )
    assert measured == pytest.approx(-20.0, abs=0.5)


def test_peak_guard_only_attenuates():
    """Huippukatto ei saa koskaan nostaa tasoa."""
    quiet = np.full((1, 100), 0.1, dtype=np.float32)
    out, trim = chain.peak_guard(quiet, -1.0)
    assert trim == 0.0 and np.array_equal(out, quiet)

    loud = np.full((1, 100), 1.5, dtype=np.float32)
    out, trim = chain.peak_guard(loud, -1.0)
    assert trim < 0
    assert float(np.abs(out).max()) == pytest.approx(10 ** (-1.0 / 20), rel=1e-6)


def test_output_stays_under_the_ceiling():
    """Kova lähde ei saa säröytyä normalisoinnin jälkeen."""
    out, _ = chain.process(
        speech_like(level=0.5), RATE, AudioSettings(), 0.0, True, -14.0, None
    )
    ceiling = 10 ** (chain.CEILING_DB / 20)
    assert float(np.abs(out).max()) <= ceiling + 1e-6


def test_lag_finds_a_known_shift():
    """Siirtymän mittaus on ainoa tapa huomata väärin ilmoitettu viive."""
    a = speech_like()[0]
    assert chain.lag_samples(a, a, RATE) == 0
    shifted = np.concatenate([np.zeros(960, dtype=np.float32), a])[: a.size]
    assert chain.lag_samples(a, shifted, RATE) == pytest.approx(960, abs=48)


def _with_transient(freq, amp=0.4, at_s=3.0, seconds=6.0):
    """Tasainen kantoaalto ja yksi 2 ms:n transientti.

    Kantoaalto on tahallaan tasainen: puheenkaltaisen signaalin omat
    iskut ovat itsekin HF-transientteja, eikä testi silloin mittaisi
    tunnistinta vaan testiaineistoa.
    """
    t = np.arange(int(seconds * RATE)) / RATE
    audio = (0.05 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)[None, :]
    at = int(at_s * RATE)
    burst = np.arange(int(0.002 * RATE)) / RATE
    audio[0, at : at + burst.size] += (amp * np.sin(2 * np.pi * freq * burst)).astype(
        np.float32
    )
    return audio, slice(at - 100, at + burst.size + 100)


def test_declick_removes_a_click_but_not_a_plosive():
    """Naksu on korkeilla, plosiivi matalilla — ja vain naksu poistetaan.

    Tämä ero on koko tunnistimen idea: leveäkaistainen tai matala isku on «p»
    tai «t» eikä huulinaksu, eikä sitä saa interpoloida pois puheesta.
    """
    click, window = _with_transient(9000)
    cleaned = chain.declick(click, RATE)
    assert cleaned.shape == click.shape
    assert np.abs(cleaned[0, window]).max() < np.abs(click[0, window]).max()

    plosive, window = _with_transient(120)
    kept = chain.declick(plosive, RATE)
    assert np.abs(kept[0, window]).max() == pytest.approx(
        float(np.abs(plosive[0, window]).max()), rel=1e-6
    )


def test_declick_would_be_dead_with_a_maximum_reference():
    """Vartio automixerista peritylle virheelle.

    Alkuperäinen vertasi paikalliseen maksimiin, vaikka kommentti puhui
    keskiarvosta. Naksu on oman ympäristönsä maksimi, joten ehto ei voinut
    täyttyä koskaan. Jos vertailukohta joskus palautuu maksimiksi, tämä
    kaatuu.
    """
    click, window = _with_transient(9000)
    assert not np.allclose(
        chain.declick(click, RATE)[0, window], click[0, window], atol=1e-6
    )


def test_a_plugin_that_changes_length_is_refused():
    """Väärin käyttäytyvä liitännäinen ei saa päätyä vientiin."""

    class Truncating:
        def reset(self):
            pass

        def process(self, audio, rate, reset=True):
            return audio[:, :-4641]  # dxReviven mitattu viive

    with pytest.raises(chain.ChainError, match="pituutta"):
        chain.process(
            speech_like(), RATE, AudioSettings(), 0.0, True, -20.0, Truncating()
        )


def test_room_tone_is_not_compressed():
    """Tilaääni saa vain tason: kompressoitu tilaääni pumppaa."""
    audio = speech_like(level=0.3)
    out, _ = chain.process(
        audio, RATE, AudioSettings(high_pass_hz=0), 0.0, False, None, None
    )
    # Ilman tavoitetta ja ilman kompressointia signaali on muuttumaton.
    assert np.allclose(out, audio, atol=1e-6)


def test_missing_plugin_is_a_readable_error():
    with pytest.raises(chain.ChainError, match="ei löydy"):
        chain.load_plugin("/ei/ole/mitaan.vst3")
    assert chain.load_plugin("") is None
