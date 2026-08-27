"""Ristivuodon vähennys: sama ääni toisessa mikissä, viiveellä.

Kaksi mikkiä samassa huoneessa kuulevat molemmat puhujat. Kun raidat
soivat yhdessä — ja viennissä ne soivat, molemmat ovat monikameran
kulmia — toisen puhujan ääni tulee kahdesti: omalta raidaltaan ja toisen
mikin vuotona muutama millisekunti myöhemmin. Se on kampasuodin, ja se
kuulostaa metalliselta kaiulta.

**Portti ei riitä, eikä syvempi portti auta.** Mitattuna oikealla
jaksolla: vaimennus toimii ja osuu oikeaan kohtaan, mutta ääretönkin
syvyys siirsi summan aaltoilua 6,22 dB:stä 6,01 dB:hen. Syy on siinä
missä maskin aukot ovat — puheenvuorojen vaihdoissa, joissa vuoto on
kovimmillaan. Portti ei myöskään voi tehdä mitään päällekkäiselle
puheelle, jossa molempien mikkien on oltava auki.

**Vuoto on lineaarinen, joten se voidaan vähentää.** Sama lähde, sama
huone, kiinteä viive ja varhaiset heijastukset: se on FIR-suodin
lähdemikistä kohdemikkiin. Suodin estimoidaan pienimmän neliösumman
mielessä niistä jaksoista joissa **vain lähde** puhuu — muuten ratkaisu
vähentäisi kohteen omaa puhetta — ja vähennetään kaikkialta, myös
päällekkäisen puheen alta.

Mitattuna 300 sekunnin jaksolla, koherenssi 200–6000 Hz kohde- ja
lähderaidan välillä silloin kun vain lähde puhuu:

    raaka                       0,1734
    raaka + vähennys            0,0095
    ketjun jälkeen, nykyinen    0,1069
    ketjun jälkeen, vähennyksen 0,0098

ja kohteen oma puhe säilyi korrelaatiolla 0,9993.

**Tämä ajetaan raa'alla äänellä ennen liitännäistä.** Liitännäinen on
generatiivinen: se ei säilytä raitojen välistä lineaarista suhdetta, ja
sen jälkeen vuotoa ei enää voi vähentää millään suotimella.

**Tulos tarkistetaan, ei uskota.** Estimaatti voi mennä pieleen — liian
vähän aineistoa, väärin valitut jaksot, mikki joka on liikkunut kesken
jakson — ja pieleen mennyt vähennys syö kohteen omaa puhetta. Siksi
``remove`` mittaa lopputuloksen ja hylkää suotimen joka ei kelpaa. Hiljaa
väärässä oleva vähennys kuuluu vasta viennin jälkeen.
"""

from __future__ import annotations

import numpy as np

# Suotimen pituus. 2048 näytettä on 43 ms 48 kHz:llä: suora ääni (5–7 ms
# mitattuna) ja varhaiset heijastukset mahtuvat, myöhäinen jälkikaiku ei —
# eikä sen tarvitse, se on jo hajonnut eikä muodosta kampaa.
TAPS = 2048

# Autokorrelaation diagonaalin korotus. Ilman tätä Toeplitz-ratkaisu on
# huonosti ehdollistettu kaistoilla joilla lähteessä ei ole energiaa.
REGULARISATION = 1e-4

# Vähempää aineistoa ei kannata estimoida: suodin sovittuu kohinaan.
MIN_SOLO_SECONDS = 20.0

# Kohteen oman puheen on säilyttävä. Alle tämän korrelaation vähennys on
# osunut puheeseen eikä vuotoon, ja suodin hylätään.
MIN_SPEECH_KEPT = 0.99

# Vähennyksen on myös tehtävä jotain. Tätä pienempi muutos vuotojaksoissa
# ei ole vähennys vaan mittausvirhe, eikä siitä kannata maksaa.
MIN_REDUCTION_DB = 0.5


# Kuinka pitkissä paloissa korrelaatiot lasketaan.
#
# Sadan tuhannen näytteen palat olisivat turhan pieniä ja kymmenen miljoonan
# turhan isoja; miljoona on FFT:lle nopea ja muistille olematon.
_LAG_BLOCK = 1 << 20


def _lags(a: np.ndarray, b: np.ndarray, taps: int) -> np.ndarray:
    """``out[k] = sum_n a[n+k] * b[n]`` viiveille ``0 … taps-1``.

    Paloittain, eikä koko ``2n-1`` mittaista korrelaatiota josta leikataan
    kaksituhatta lukua. Ero ei ole hienosäätöä: tunnin mikki on 184
    miljoonaa näytettä, jolloin täysi korrelaatio on 368 miljoonaa
    liukulukua ja sen FFT pyöristyy seuraavaan nopeaan pituuteen — useita
    gigatavuja, ja siinä koossa ratkaisu ei enää tullut ulos. Oireena
    «vuotopolkua ei saatu ratkaistua» **vain pitkissä osissa**: 20 minuutin
    tiedostot menivät läpi, 64 minuutin eivät.

    Sama virhe kuin ``np.correlate(..., "full")``ssa siirtymän mittauksessa
    ja ``keyframe_times``issa videopuolella: lasketaan kaikki ja otetaan
    siitä murto-osa. Summat ovat tässä samat luku luvulta, vain kertyminen
    on eri järjestyksessä.
    """
    from scipy import signal as sig

    n = min(len(a), len(b))
    out = np.zeros(taps, dtype=np.float64)
    step = max(taps * 4, _LAG_BLOCK)
    for start in range(0, n, step):
        stop = min(start + step, n)
        piece = np.asarray(b[start:stop], dtype=np.float64)
        if not piece.size:
            break
        # Laajennettu pala kantaa viiveet palan reunan yli, joten raja ei
        # katkaise yhtään summan termiä. Lopussa signaali loppuu kesken, ja
        # silloin **nollataan**, ei lyhennetä: täysi korrelaatio tekee
        # saman implisiittisesti, ja lyhentäminen jättäisi pitkät viiveet
        # laskematta — jolloin ``out`` täyttyisi vain viiveelle nolla.
        wide = np.asarray(a[start:start + piece.size + taps - 1],
                          dtype=np.float64)
        if wide.size < piece.size + taps - 1:
            wide = np.pad(wide, (0, piece.size + taps - 1 - wide.size))
        found = sig.correlate(wide, piece, "valid", method="fft")
        out[: found.size] += found[:taps]
    return out


def path(
    target: np.ndarray,
    source: np.ndarray,
    keep: np.ndarray,
    taps: int = TAPS,
) -> np.ndarray:
    """Pienimmän neliösumman FIR ``source`` -> ``target``, vain ``keep``-näytteillä.

    ``keep`` on totuusarvotaulukko: ne näytteet joissa vain lähde on
    äänessä. Molemmat signaalit nollataan sen ulkopuolelta ennen
    korrelaatioita, jolloin summat kertyvät vain valituista kohdista.

    Normaaliyhtälöt ratkaistaan Toeplitz-rakenteesta: matriisi olisi
    2048×2048 ja sen muodostaminen turhaa, kun autokorrelaatio määrää sen
    kokonaan.
    """
    from scipy import linalg

    n = min(len(target), len(source), len(keep))
    mask = np.asarray(keep[:n], dtype=np.float64)
    t = np.asarray(target[:n], dtype=np.float64) * mask
    s = np.asarray(source[:n], dtype=np.float64) * mask
    if not np.any(s):
        return np.zeros(taps)

    auto = _lags(s, s, taps)
    cross = _lags(t, s, taps)
    if auto[0] <= 0:
        return np.zeros(taps)
    auto[0] *= 1.0 + REGULARISATION
    try:
        return linalg.solve_toeplitz((auto, auto), cross)
    except (linalg.LinAlgError, ValueError):
        return np.zeros(taps)


def _level(x: np.ndarray, keep: np.ndarray) -> float:
    picked = np.asarray(x)[: len(keep)][np.asarray(keep, dtype=bool)]
    if picked.size == 0:
        return -np.inf
    return 10.0 * np.log10(float(np.mean(np.asarray(picked, np.float64) ** 2)) + 1e-30)


def remove(
    target: np.ndarray,
    source: np.ndarray,
    rate: int,
    solo_source: np.ndarray,
    solo_target: np.ndarray,
    taps: int = TAPS,
) -> tuple[np.ndarray, dict]:
    """Vähentää ``source``:n vuodon ``target``:sta.

    ``solo_source`` ja ``solo_target`` ovat näytekohtaisia totuusarvoja:
    kohdat joissa vain lähde puhuu (estimointiin) ja joissa vain kohde
    puhuu (tarkistukseen).

    Palauttaa ``(tulos, tiedot)``. Jos suodin ei kelpaa, tulos on
    alkuperäinen ja ``tiedot["reason"]`` kertoo miksi — vähennystä ei
    tehdä puolittain eikä hiljaa.
    """
    from scipy import signal as sig

    info: dict = {"reduction_db": 0.0, "kept": 1.0, "reason": ""}
    solo_source = np.asarray(solo_source, dtype=bool)
    seconds = float(solo_source.sum()) / max(rate, 1)
    info["solo_seconds"] = seconds
    if seconds < MIN_SOLO_SECONDS:
        info["reason"] = "too_little"
        return target, info

    filt = path(target, source, solo_source, taps)
    if not np.any(filt):
        info["reason"] = "no_path"
        return target, info

    leak = sig.fftconvolve(np.asarray(source, dtype=np.float64), filt)[: len(target)]
    if len(leak) < len(target):
        leak = np.pad(leak, (0, len(target) - len(leak)))
    cleaned = np.asarray(target, dtype=np.float64) - leak

    before = _level(target, solo_source)
    after = _level(cleaned, solo_source)
    info["reduction_db"] = float(before - after)

    solo_target = np.asarray(solo_target, dtype=bool)
    if solo_target.any():
        a = np.asarray(target, np.float64)[: len(solo_target)][solo_target]
        b = cleaned[: len(solo_target)][solo_target]
        if a.size > 1 and np.std(a) > 0 and np.std(b) > 0:
            info["kept"] = float(np.corrcoef(a, b)[0, 1])

    if info["kept"] < MIN_SPEECH_KEPT:
        info["reason"] = "ate_speech"
        return target, info
    if info["reduction_db"] < MIN_REDUCTION_DB:
        info["reason"] = "no_gain"
        return target, info
    return cleaned.astype(target.dtype, copy=False), info
