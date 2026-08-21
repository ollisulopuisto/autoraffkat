"""Puheen kanavanauha.

Sama ketju kuin automixerin PIPELINE.md:ssä, mutta pedalboardilla ja omassa
prosessissa. Aiemmin tämä ajettiin automixerin ympäristössä `uv run`illa;
riippuvuus poistettiin, koska tarvittu osa oli pieni ja pedalboard tekee sen
suoraan — samalla lähti vaatimus Python 3.13:sta ja MLX:stä.

Järjestys on tarkoituksellinen:

1. **Ulkoinen liitännäinen** (dxRevive tms.) ensin. Kohina ja särö siivotaan
   ennen kuin mikään vahvistaa niitä.
2. **Ylipäästö** vie jyrinän.
3. **Maiskausten poisto** siivoaa huulinaksut.
4. **Normalisointi** mitataan vasta tässä, siivotusta signaalista.
5. **Kompressointi** kahdessa vaiheessa, nopea ja hidas.
6. **Trimmi ja huippukatto.**

Normalisointi on nimenomaan tässä kohtaa eikä aiemmin: kompressorin kynnykset
ovat absoluuttisia desibelejä, ja käsittelemätön podcast-mikki on helposti
-40 LUFS, jolloin -12 dB:n kynnys ei ylity kertaakaan.

Tiedostoa ei käsitellä paloissa. Liitännäisen tila jatkuisi palojen yli
(``reset=False``), mutta tulos jää liitännäisen viiveen verran lyhyemmäksi —
mitattuna 4641 näytettä — ja pituuden muuttuminen on tässä työkalussa se yksi
asia jota ei sallita.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# Ylipäästön jyrkkyys ja kompressorien ajat. automixer ilmaisi kompressorin
# RMS-ikkunana; pedalboard puhuu hyökkäys- ja palautusajoista, joten nopea ja
# hidas vaihe on kirjoitettu tähän auki.
PEAK_ATTACK_MS = 2.0
PEAK_RELEASE_MS = 60.0
PEAK_RATIO = 2.5
LEVEL_ATTACK_MS = 30.0
LEVEL_RELEASE_MS = 300.0
LEVEL_RATIO = 1.5
# Huippukatto. Tämä on staattinen vaimennus eikä rajoitin: pedalboardin
# Limiter tekee makeup-vahvistuksen, joka nosti mitattuna -20 LUFS:n
# -15,8:aan ja huiput nollaan. Tässä ei haluta lisää tasoa vaan varmuus
# siitä ettei summa säröydy.
CEILING_DB = -1.0

# Mistä liitännäisiä etsitään. Vain vakiopaikat: väärä polku olisi pahempi
# kuin ei polkua.
PLUGIN_DIRS = (
    "/Library/Audio/Plug-Ins/VST3",
    "~/Library/Audio/Plug-Ins/VST3",
    "/Library/Audio/Plug-Ins/Components",
    "~/Library/Audio/Plug-Ins/Components",
)


class ChainError(Exception):
    """Ääntä ei voitu käsitellä."""


def plugins() -> list[dict]:
    """Asennetut VST3- ja AU-liitännäiset nimineen ja polkuineen."""
    found: dict[str, str] = {}
    for folder in PLUGIN_DIRS:
        root = Path(os.path.expanduser(folder))
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if entry.suffix in (".vst3", ".component"):
                # Sama liitännäinen on usein molemmissa muodoissa; VST3 voittaa,
                # koska se on ensin listassa.
                found.setdefault(entry.stem, str(entry))
    return [{"name": name, "path": path} for name, path in sorted(found.items())]


def load_plugin(path: str):
    """Lataa liitännäisen. Virhe on luettava, ei pedalboardin oma."""
    import pedalboard

    if not path:
        return None
    if not os.path.exists(path):
        raise ChainError(f"Liitännäistä ei löydy: {path}")
    try:
        return pedalboard.load_plugin(path)
    except Exception as exc:
        raise ChainError(
            f"Liitännäistä ei voitu ladata: {os.path.basename(path)} — {exc}") from exc


def loudness(mono: np.ndarray, rate: int) -> float | None:
    """Integroitu äänekkyys, tai ``None`` jos ei mitattavissa."""
    import pyloudnorm as pyln

    if mono.size < rate:                      # alle sekunti: ei mitattavaa
        return None
    try:
        value = float(pyln.Meter(rate).integrated_loudness(
            np.asarray(mono, dtype=np.float64)))
    except Exception:
        return None
    if not np.isfinite(value) or value < -70.0:
        return None
    return value


def lag_samples(before: np.ndarray, after: np.ndarray, rate: int,
                bin_ms: float = 1.0) -> int:
    """Signaalien välinen viive näytteinä.

    Ristikorrelaatio lasketaan verhokäyristä eikä aallonmuodosta, koska
    liitännäinen muuttaa sisältöä mutta ei puheen rytmiä. Tämä on ainoa tapa
    huomata liitännäinen joka ilmoittaa viiveensä väärin: pituus säilyy, mutta
    ääni on siirtynyt — eikä sitä huomaa ennen kuin leikkaus on koossa.
    """
    step = max(1, int(rate * bin_ms / 1000))
    count = min(before.size, after.size) // step * step
    if count < step * 8:
        return 0
    a = np.abs(before[:count]).reshape(-1, step).max(axis=1)
    b = np.abs(after[:count]).reshape(-1, step).max(axis=1)
    a = a - a.mean()
    b = b - b.mean()
    if not a.any() or not b.any():
        return 0
    correlation = np.correlate(b, a, mode="full")
    return (int(np.argmax(correlation)) - (a.size - 1)) * step


def declick(audio: np.ndarray, rate: int, sensitivity: float = 0.5) -> np.ndarray:
    """Poistaa huulinaksut ja maiskaukset.

    Portattu automixerin ``DeSmackProcessor``ista. Yli 4 kHz:n transientit,
    jotka piikkaavat paikallisen **keskiarvon** yli, tulkitaan naksuiksi —
    paitsi jos matalilla on samaan aikaan energiaa, jolloin kyse on
    plosiivista eikä naksusta. Löydetyt kohdat interpoloidaan yli.

    Alkuperäinen käytti vertailukohtana paikallista maksimia, vaikka koodin
    oma kommentti puhui keskiarvosta. Naksu on määritelmän mukaan oman
    ympäristönsä maksimi, joten ehto ``|x| > max * 3,5`` ei voi täyttyä
    koskaan: käsittely oli aina nolla-operaatio. Keskiarvo on se mitä
    tarkoitettiin, ja sillä ehto myös laukeaa.
    """
    from scipy import signal as sp
    from scipy.ndimage import uniform_filter1d

    out = audio.copy()
    for channel in range(audio.shape[0]):
        data = audio[channel]
        high = sp.sosfiltfilt(sp.butter(4, 4000, "hp", fs=rate, output="sos"), data)
        low = sp.sosfiltfilt(sp.butter(4, 1000, "lp", fs=rate, output="sos"), data)
        window = max(1, int(0.05 * rate))
        local = uniform_filter1d(np.abs(high), size=window)
        factor = 5.0 - 3.0 * sensitivity
        clicks = np.abs(high) > local * factor
        clicks &= ~(np.abs(low) > np.mean(np.abs(low)) * 3.0)
        if not clicks.any():
            continue
        index = np.flatnonzero(clicks)
        for cluster in np.split(index, np.flatnonzero(np.diff(index) > 1) + 1):
            start = max(0, int(cluster[0]) - 10)
            end = min(data.size, int(cluster[-1]) + 10)
            if end - start >= int(0.01 * rate):   # yli 10 ms ei ole naksu
                continue
            before = np.arange(max(0, start - 20), start)
            after = np.arange(end, min(data.size, end + 20))
            if before.size <= 5 or after.size <= 5:
                continue
            reference = np.concatenate([before, after])
            out[channel, start:end] = np.interp(
                np.arange(start, end), reference, data[reference])
    return out


def peak_guard(audio: np.ndarray, ceiling_db: float = CEILING_DB) -> tuple:
    """Vaimentaa koko raidan, jos huippu ylittää katon.

    Staattinen vaimennus eikä rajoitin: dynamiikka on jo hoidettu
    kompressoreilla, ja tässä halutaan vain varmuus ettei särö. Palauttaa
    ``(ääni, vaimennus_dB)``, jotta tavoitetason ohitus näkyy kutsujalle.
    """
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak <= 0.0:
        return audio, 0.0
    ceiling = 10.0 ** (ceiling_db / 20.0)
    if peak <= ceiling:
        return audio, 0.0
    return audio * (ceiling / peak), 20.0 * np.log10(ceiling / peak)


def _board(*steps):
    """Pedalboard annetuista vaiheista, tyhjät pois."""
    import pedalboard

    return pedalboard.Pedalboard([s for s in steps if s is not None])


@dataclass
class ChainResult:
    """Yhden tiedoston käsittely."""

    frames: int
    channels: int
    gain_db: float          # normalisoinnin nosto
    measured_lufs: float | None
    lag: int                # liitännäisen aiheuttama siirtymä näytteinä


def process(audio: np.ndarray, rate: int, settings, gain_db: float,
            speech: bool, target_lufs: float | None, plugin=None) -> tuple:
    """Ajaa ketjun. ``audio`` on muotoa ``(kanavat, näytteet)``.

    Palauttaa ``(käsitelty, ChainResult)``. Pituus ei muutu; jos jokin vaihe
    muuttaa sitä, se on virhe eikä tulosta käytetä.
    """
    import pedalboard

    frames = audio.shape[1]
    original = audio[0].copy() if speech and plugin is not None else None

    # 1. Ulkoinen liitännäinen ensin: siivoa ennen kuin vahvistat.
    #
    # ``reset=True`` on pakollinen. ``reset=False`` jättää liitännäisen viiveen
    # verran häntää pois — mitattuna 4641 näytettä dxRevivella — eli tulos on
    # oikean kuuloinen mutta liian lyhyt. Samasta syystä tiedostoa ei käsitellä
    # paloissa.
    if plugin is not None:
        audio = plugin.process(audio, rate, reset=True)
        if audio.shape[1] != frames:
            raise ChainError(
                f"Liitännäinen muutti pituutta ({frames} → {audio.shape[1]}).")

    # 2.–3. Siivous ennen mittausta.
    cleanup = _board(
        pedalboard.HighpassFilter(cutoff_frequency_hz=settings.high_pass_hz)
        if settings.high_pass_hz > 0 else None)
    if len(cleanup):
        audio = cleanup(audio, rate, reset=True)
    if speech and getattr(settings, "declick", False):
        audio = declick(audio, rate)

    # 4. Normalisointi siivotusta signaalista.
    measured = loudness(audio.mean(axis=0), rate) if target_lufs is not None else None
    lift = 0.0 if measured is None else float(target_lufs - measured)

    # 5. Dynamiikka.
    if speech:
        board = _board(
            pedalboard.Gain(gain_db=lift) if lift else None,
            pedalboard.Compressor(threshold_db=settings.peak_threshold_db,
                                  ratio=PEAK_RATIO, attack_ms=PEAK_ATTACK_MS,
                                  release_ms=PEAK_RELEASE_MS),
            pedalboard.Compressor(threshold_db=settings.leveler_threshold_db,
                                  ratio=LEVEL_RATIO, attack_ms=LEVEL_ATTACK_MS,
                                  release_ms=LEVEL_RELEASE_MS))
        if len(board):
            audio = board(audio, rate, reset=True)

        # 6. Taso mitataan uudestaan, koska kompressointi siirtää sitä.
        #
        # LUFS portittaa hiljaiset kohdat pois suhteessa kokonaisuuteen. Kun
        # kompressori nostaa hiljaisia kohtia, portin läpi pääsee eri joukko
        # lohkoja ja lukema nousee — mitattuna 2,2 dB tavoitteen yli. Siksi
        # korjaus tehdään vasta tässä, ja rajoitin sen jälkeen.
        after = loudness(audio.mean(axis=0), rate) if target_lufs is not None else None
        correction = 0.0 if after is None else float(target_lufs - after)
        lift += correction
        tail = _board(
            pedalboard.Gain(gain_db=correction) if correction else None,
            pedalboard.Gain(gain_db=gain_db) if gain_db else None)
        if len(tail):
            audio = tail(audio, rate, reset=True)
        audio, trimmed = peak_guard(audio)
        lift += trimmed
    else:
        # Tilaääni jätetään koskematta muuten: kompressoitu tilaääni pumppaa,
        # eikä taso siirry, joten yksi mittaus riittää.
        board = _board(pedalboard.Gain(gain_db=lift) if lift else None,
                       pedalboard.Gain(gain_db=gain_db) if gain_db else None)
        if len(board):
            audio = board(audio, rate, reset=True)
        audio, trimmed = peak_guard(audio)
        lift += trimmed

    if audio.shape[1] != frames:
        raise ChainError(
            f"Käsittely muutti pituutta ({frames} → {audio.shape[1]}).")

    lag = lag_samples(original, audio[0], rate) if original is not None else 0
    return audio, ChainResult(frames=frames, channels=audio.shape[0],
                              gain_db=round(lift, 2), measured_lufs=measured,
                              lag=lag)


def apply_duck(audio: np.ndarray, rate: int, closed: list[tuple[int, int]],
               depth_db: float, fade: float) -> np.ndarray:
    """Vaimentaa annetut jaksot ja liu'uttaa reunat.

    Vaimennus tehdään jaksoittain paikan päällä eikä koko tiedoston mittaisella
    vahvistuskäyrällä: tunnin mittainen mikki on 184 miljoonaa näytettä, ja
    erillinen float-taulukko sen päälle olisi kolme neljäsosaa gigatavusta.
    Jaksoja on tuhansia.

    Reunoilla on liuku, koska askel vaimennuksesta täyteen tasoon naksahtaa.
    """
    if not closed or depth_db >= 0:
        return audio
    level = 10.0 ** (depth_db / 20.0)
    ramp = max(1, int(fade * rate))
    frames = audio.shape[1]

    for start, end in closed:
        start = max(0, start)
        end = min(frames, end)
        if end <= start:
            continue
        # Liu'ut eivät saa syödä toisiaan lyhyessä jaksossa.
        span = min(ramp, (end - start) // 2)
        body_start, body_end = start + span, end - span
        if body_end > body_start:
            audio[:, body_start:body_end] *= level
        if span > 0:
            down = np.linspace(1.0, level, span, dtype=np.float32)
            audio[:, start:start + span] *= down
            audio[:, end - span:end] *= down[::-1]
    return audio
