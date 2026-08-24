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
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..i18n import t

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


# Mistä liitännäisiä etsitään. Vakiopaikat käyttöjärjestelmän mukaan.
def _standard_plugin_dirs() -> tuple[str, ...]:
    if sys.platform == "darwin":
        return (
            "/Library/Audio/Plug-Ins/VST3",
            "~/Library/Audio/Plug-Ins/VST3",
            "/Library/Audio/Plug-Ins/Components",
            "~/Library/Audio/Plug-Ins/Components",
        )
    elif sys.platform.startswith("linux"):
        return (
            "/usr/lib/vst3",
            "/usr/local/lib/vst3",
            "~/.vst3",
            "~/.local/lib/vst3",
        )
    elif sys.platform == "win32":
        common_files = os.environ.get(
            "CommonProgramFiles", r"C:\Program Files\Common Files"
        )
        common_files_x86 = os.environ.get(
            "CommonProgramFiles(x86)", r"C:\Program Files (x86)\Common Files"
        )
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        dirs = [
            os.path.join(common_files, "VST3"),
            os.path.join(common_files_x86, "VST3"),
        ]
        if local_app_data:
            dirs.append(os.path.join(local_app_data, "Programs", "Common", "VST3"))
        return tuple(dirs)
    return (
        "/Library/Audio/Plug-Ins/VST3",
        "~/Library/Audio/Plug-Ins/VST3",
    )


PLUGIN_DIRS = _standard_plugin_dirs()


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


def load_plugin(path: str, params: dict | None = None):
    """Lataa liitännäisen ja asettaa sen säätimet. Virhe on luettava, ei
    pedalboardin oma."""
    import pedalboard

    if not path:
        return None
    if not os.path.exists(path):
        raise ChainError(t("audio.plugin_missing", path=path))
    try:
        plugin = pedalboard.load_plugin(path)
    except Exception as exc:
        raise ChainError(
            t("audio.plugin_failed", name=os.path.basename(path), error=exc)
        ) from exc
    apply_parameters(plugin, params)
    return plugin


# Liitännäinen on 97 % käsittelyn ajasta ja käyttää **yhtä** ydintä: mitattu
# dxRevivella M2:lla 0,98 ydintä ja 7,25x reaaliaika. Koneen muut ytimet saa
# töihin vain ajamalla useaa kohtaa yhtä aikaa.
#
# Skaalaus ei ole lineaarinen — liitännäisen päättely on muistikaistarajoitettu
# ja tehokkuusytimet ovat hitaampia. Mitattu läpimeno M2:lla (4P+4E):
# 1 → 7,5x, 2 → 9,5x, 4 → 14,8x, 6 → 20,1x reaaliaikaa. Oikealla 20 minuutin
# tiedostolla koko ketju 168,4 s → 68,3 s, eli 2,46-kertainen.
#
# Osuus eikä vakioluku: kahdeksan ytimen kannettava ja kahdenkymmenen ytimen
# työasema ovat eri koneita, eikä kummankaan lukua voi kirjoittaa tähän.
WORKER_SHARE = 0.75


def worker_count(wanted: int = 0) -> int:
    """Montako liitännäisinstanssia ajetaan rinnakkain.

    ``0`` on automaattinen: ``WORKER_SHARE`` koneen ytimistä. Loput jäävät
    käyttöliittymälle ja muulle koneelle — käsittely on taustatyö, jonka
    aikana konetta käytetään muuhun.

    Muu luku on käyttäjän oma valinta, rajattuna ytimien määrään: kolmesta-
    kymmenestä palasta kahdeksalla ytimellä ei tule nopeampaa, vain enemmän
    muistia ja lyhyempiä paloja.
    """
    cores = os.cpu_count() or 2
    if wanted > 0:
        return max(1, min(int(wanted), cores))
    return max(1, round(cores * WORKER_SHARE))


# Palan reunoille jätetään marginaali, joka käsitellään ja heitetään pois:
# liitännäinen tarvitsee kontekstia ennen kuin sen tulos vakiintuu.
#
# Mitattu ero kokonaisena käsiteltyyn, 60 s puhetta neljänä palana:
# marginaali 0,5 s → -32,8 dBFS, 2 s → -34,8 dBFS, 5 s → -42,5 dBFS, kun
# signaali itse on -15,6 dBFS. Sauma on puhdas (-50…-70 dBFS) — jäljelle
# jäävä ero on liitännäisen oma hidas sopeutuminen, ei napsahdus.
#
# Oikealla 20 minuutin tiedostolla kuutena palana ero on puhelohkoissa
# 25,7 dB signaalin alle ja hiljaisissa kohdissa -84 dBFS absoluuttisesti.
# Se ei ole nolla, ja siksi tämä on säädettävissä:
# ``AudioSettings.plugin_workers``, jossa 1 tarkoittaa yhtenä palana.
PIECE_MARGIN = 5.0
# Tätä lyhyempää ei pilkota: marginaalit söisivät hyödyn.
PIECE_MIN = 120.0


def load_pool(path: str, params: dict | None = None, count: int = 1):
    """Liitännäinen ``count`` kappaleena, tai ``None`` jos polkua ei ole.

    Jokainen rinnakkainen pala tarvitsee oman instanssin: VST3-olio on
    tilallinen eikä sitä voi ajaa kahdesta säikeestä yhtä aikaa.
    """
    if not path:
        return None
    return [load_plugin(path, params) for _ in range(max(1, count))]


def apply_plugin(plugin, audio: np.ndarray, rate: int) -> np.ndarray:
    """Liitännäinen koko tiedostoon. ``plugin`` on yksi olio tai lista.

    Listana tiedosto pilkotaan yhtä moneen palaan ja palat ajetaan
    rinnakkain omilla instansseillaan. Jokainen pala on oma täysi
    ``reset=True``-ajonsa marginaaleineen — ei siis sama asia kuin
    tiedoston syöttäminen liitännäiselle paloissa, joka lyhentäisi tuloksen
    liitännäisen viiveen verran.

    Pituus säilyy rakenteeltaan: tulos kirjoitetaan valmiiksi oikean
    kokoiseen taulukkoon, ja jokaisen palan pituus tarkistetaan erikseen.
    """
    if plugin is None:
        return audio
    if not isinstance(plugin, (list, tuple)):
        return plugin.process(audio, rate, reset=True)
    pool = list(plugin)
    frames = audio.shape[1]
    pieces = min(len(pool), max(1, int(frames / rate / PIECE_MIN)))
    if pieces < 2:
        return pool[0].process(audio, rate, reset=True)

    margin = int(PIECE_MARGIN * rate)
    edges = [int(round(i * frames / pieces)) for i in range(pieces + 1)]
    out = np.zeros_like(audio)
    failures: list[Exception] = []

    def one(index: int) -> None:
        first, last = edges[index], edges[index + 1]
        low, high = max(0, first - margin), min(frames, last + margin)
        try:
            done = pool[index].process(audio[:, low:high], rate, reset=True)
            if done.shape[1] != high - low:
                raise ChainError(
                    t("audio.plugin_length", before=high - low, after=done.shape[1])
                )
            out[:, first:last] = done[:, first - low : first - low + (last - first)]
        except Exception as exc:  # säie ei saa kaatua hiljaa
            failures.append(exc)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(pieces)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    if failures:
        raise failures[0]
    return out


def apply_parameters(plugin, params: dict | None) -> list[str]:
    """Asettaa liitännäisen säätimet. Palauttaa nimet jotka ohitettiin.

    Arvo on liitännäisen omissa yksiköissä (``plugin.input_gain = 3.0``);
    pedalboard muuntaa sen liitännäisen raaka-arvoksi itse, eikä muunnos ole
    aina lineaarinen — siksi asetuksissakin on yksikköarvo eikä 0–1.

    Nimi tarkistetaan ``parameters``-sanakirjasta ennen kirjoitusta. Ilman
    tarkistusta tuntematon nimi menisi läpi hiljaa: pedalboardin
    liitännäisolio ottaa vastaan minkä tahansa attribuutin, jolloin asetus
    näyttäisi menneen perille eikä vaikuttaisi mihinkään.

    Ohitus ei ole virhe. Asetukset periytyvät jaksosta toiseen, ja edellisen
    jakson liitännäinen on voinut olla toinen — silloin oikea käytös on ajaa
    liitännäinen omilla oletuksillaan eikä kaataa koko käsittelyä.
    """
    skipped: list[str] = []
    known = getattr(plugin, "parameters", None) or {}
    for name, value in (params or {}).items():
        if name not in known:
            skipped.append(str(name))
            continue
        try:
            setattr(plugin, name, value)
        except (ValueError, TypeError):
            skipped.append(str(name))
    return skipped


# Kuinka monta säädintä käyttöliittymälle kerrotaan. Puheliitännäisessä niitä
# on muutama, syntikassa tuhansia. Katkaisu kerrotaan käyttäjälle: hiljainen
# katkaisu näyttäisi siltä ettei liitännäisessä ole enempää.
MAX_PARAMS = 64
# Valikollisen säätimen vaihtoehdot. Sama syy.
MAX_CHOICES = 64

# Säätimien kuvaukset polun mukaan. Lataus kestää sekunteja, eikä liitännäinen
# muutu ohjelman ajon aikana.
_SPECS: dict[str, tuple[list[dict], int]] = {}


def _spec(name: str, param) -> dict | None:
    """Yksi säädin käyttöliittymän ymmärtämässä muodossa, tai ``None`` jos
    sitä ei voi piirtää.

    Tyyppi ratkaisee elementin: totuusarvo on valintaruutu, merkkijono on
    valikko ja luku on liukusäädin. Rajat tulevat liitännäiseltä
    (``range``), koska ne ovat sen omissa yksiköissä — desibeleissä,
    prosenteissa tai hertseissä sen mukaan mistä säätimestä on kyse.
    """
    kind = getattr(param, "type", float)
    label = str(getattr(param, "name", None) or name)
    if kind is bool:
        return {"name": name, "label": label, "type": "bool"}
    if kind is str:
        choices = [str(v) for v in (getattr(param, "valid_values", None) or [])]
        if not choices:
            return None
        return {
            "name": name,
            "label": label,
            "type": "choice",
            "choices": choices[:MAX_CHOICES],
        }
    span = tuple(getattr(param, "range", None) or ())
    low, high, step = (span + (None, None, None))[:3]
    if low is None or high is None or float(high) <= float(low):
        return None
    low, high = float(low), float(high)
    # Askel puuttuu portaattomalta säätimeltä. Sadasosa alueesta on se mitä
    # liitännäisen oma yleiskäyttöliittymä näyttäisi.
    step = float(step) if step else (high - low) / 100.0
    out = {
        "name": name,
        "label": label,
        "type": "float",
        "min": low,
        "max": high,
        "step": step,
    }
    units = getattr(param, "units", None)
    if units:
        out["units"] = str(units)
    return out


def _default_value(plugin, name: str, kind: str):
    """Säätimen nykyarvo omana tyyppinään, tai ``None`` jos sitä ei saa.

    Muunnos on pakollinen: pedalboard palauttaa kääritun arvon, joka ei
    mene sellaisenaan JSONiin.
    """
    cast = {"bool": bool, "choice": str}.get(kind, float)
    try:
        return cast(getattr(plugin, name))
    except (AttributeError, TypeError, ValueError):
        return None


def parameter_specs(path: str) -> tuple[list[dict], int]:
    """Liitännäisen säätimet käyttöliittymälle: ``(kuvaukset, kokonaismäärä)``.

    Kuvaukseen tulee myös liitännäisen oma oletusarvo (``value``), jotta
    säädin näyttää oikeaa lukua ennen kuin siihen on koskettu: asetuksiin
    tallennetaan vain ne säätimet joita käyttäjä on liikuttanut.
    """
    if not path:
        return [], 0
    if path in _SPECS:
        return _SPECS[path]
    plugin = load_plugin(path)
    known = getattr(plugin, "parameters", None) or {}
    specs: list[dict] = []
    for name in known:
        spec = _spec(name, known[name])
        if spec is None:
            continue
        value = _default_value(plugin, name, spec["type"])
        if value is None:
            continue
        spec["value"] = value
        specs.append(spec)
        if len(specs) >= MAX_PARAMS:
            break
    _SPECS[path] = (specs, len(known))
    return _SPECS[path]


def loudness(mono: np.ndarray, rate: int) -> float | None:
    """Integroitu äänekkyys, tai ``None`` jos ei mitattavissa."""
    import pyloudnorm as pyln

    if mono.size < rate:  # alle sekunti: ei mitattavaa
        return None
    try:
        value = float(
            pyln.Meter(rate).integrated_loudness(np.asarray(mono, dtype=np.float64))
        )
    except Exception:
        return None
    if not np.isfinite(value) or value < -70.0:
        return None
    return value


def lag_samples(
    before: np.ndarray, after: np.ndarray, rate: int, bin_ms: float = 1.0
) -> int:
    """Signaalien välinen viive näytteinä.

    Ristikorrelaatio lasketaan verhokäyristä eikä aallonmuodosta, koska
    liitännäinen muuttaa sisältöä mutta ei puheen rytmiä. Tämä on ainoa tapa
    huomata liitännäinen joka ilmoittaa viiveensä väärin: pituus säilyy, mutta
    ääni on siirtynyt — eikä sitä huomaa ennen kuin leikkaus on koossa.

    Korrelaatio tehdään FFT:llä. ``np.correlate(..., "full")`` laskee sen
    suoraan, mikä on O(n²): millisekunnin ruudulla 20 minuutin tiedostosta
    tulee 1,2 miljoonaa ruutua ja mittaus kesti **132 sekuntia** — enemmän
    kuin dxRevive samasta tiedostosta. FFT antaa saman tuloksen 0,05
    sekunnissa. Ero kasvaa neliössä, joten tunnin tiedostolla suora tapa oli
    varttitunti pelkkää tarkistusta.
    """
    from scipy import signal as sp

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
    correlation = sp.fftconvolve(b, a[::-1], mode="full")
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
            if end - start >= int(0.01 * rate):  # yli 10 ms ei ole naksu
                continue
            before = np.arange(max(0, start - 20), start)
            after = np.arange(end, min(data.size, end + 20))
            if before.size <= 5 or after.size <= 5:
                continue
            reference = np.concatenate([before, after])
            out[channel, start:end] = np.interp(
                np.arange(start, end), reference, data[reference]
            )
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
    gain_db: float  # normalisoinnin nosto
    measured_lufs: float | None
    lag: int  # liitännäisen aiheuttama siirtymä näytteinä


# Vaiheiden kumulatiiviset osuudet ketjun työstä.
#
# Mitattu 20 minuutin mikkitiedostolla dxRevivellä: liitännäinen 163 s, mittaus
# 2,0 s, dynamiikka 2,2 s, siirtymän mittaus 0,05 s, muut alle sekunnin. Ilman
# liitännäistä painot ovat aivan toiset, joten taulukoita on kaksi.
#
# Luvut eivät ole tarkkoja eivätkä voi olla: liitännäisen nopeus riippuu
# liitännäisestä. Ne ovat siksi, että palkki liikkuisi tunnin tiedoston aikana
# eikä seisoisi kymmentä minuuttia paikallaan.
STAGES_PLUGIN = {
    "plugin": 0.95,
    "cleanup": 0.96,
    "measure": 0.975,
    "dynamics": 0.995,
    "lag": 1.0,
}
STAGES_PLAIN = {
    "cleanup": 0.10,
    "measure": 0.45,
    "dynamics": 0.90,
    "lag": 1.0,
}


def process(
    audio: np.ndarray,
    rate: int,
    settings,
    gain_db: float,
    speech: bool,
    target_lufs: float | None,
    plugin=None,
    stage=None,
) -> tuple:
    """Ajaa ketjun. ``audio`` on muotoa ``(kanavat, näytteet)``.

    Palauttaa ``(käsitelty, ChainResult)``. Pituus ei muutu; jos jokin vaihe
    muuttaa sitä, se on virhe eikä tulosta käytetä.

    ``stage(nimi, osuus)`` kutsutaan vaiheen **valmistuttua**. Liitännäistä ei
    voi kysyä kesken ajon — se käsittelee tiedoston yhtenä palana, koska
    paloittain se lyhentäisi tuloksen — joten vaiheen tarkkuus on se mitä
    edistymisestä on saatavissa.
    """
    import pedalboard

    weights = STAGES_PLUGIN if plugin is not None else STAGES_PLAIN

    def done(name: str) -> None:
        if stage is not None:
            stage(name, weights[name])

    frames = audio.shape[1]
    original = audio[0].copy() if speech and plugin is not None else None

    # 1. Ulkoinen liitännäinen ensin: siivoa ennen kuin vahvistat.
    #
    # ``reset=True`` on pakollinen. ``reset=False`` jättää liitännäisen viiveen
    # verran häntää pois — mitattuna 4641 näytettä dxRevivella — eli tulos on
    # oikean kuuloinen mutta liian lyhyt. Tiedostoa ei siksi koskaan syötetä
    # liitännäiselle paloissa; ``apply_plugin``in rinnakkaiset palat ovat eri
    # asia, jokainen niistä on oma täysi ajonsa.
    if plugin is not None:
        audio = apply_plugin(plugin, audio, rate)
        if audio.shape[1] != frames:
            raise ChainError(
                t("audio.plugin_length", before=frames, after=audio.shape[1])
            )
        done("plugin")

    # 2.–3. Siivous ennen mittausta.
    cleanup = _board(
        pedalboard.HighpassFilter(cutoff_frequency_hz=settings.high_pass_hz)
        if settings.high_pass_hz > 0
        else None
    )
    if len(cleanup):
        audio = cleanup(audio, rate, reset=True)
    if speech and getattr(settings, "declick", False):
        audio = declick(audio, rate, getattr(settings, "declick_sensitivity", 0.5))
    done("cleanup")

    # 4. Normalisointi siivotusta signaalista.
    measured = loudness(audio.mean(axis=0), rate) if target_lufs is not None else None
    lift = 0.0 if measured is None else float(target_lufs - measured)
    done("measure")

    # 5. Dynamiikka.
    if speech:
        board = _board(
            pedalboard.Gain(gain_db=lift) if lift else None,
            pedalboard.Compressor(
                threshold_db=settings.peak_threshold_db,
                ratio=PEAK_RATIO,
                attack_ms=PEAK_ATTACK_MS,
                release_ms=PEAK_RELEASE_MS,
            ),
            pedalboard.Compressor(
                threshold_db=settings.leveler_threshold_db,
                ratio=LEVEL_RATIO,
                attack_ms=LEVEL_ATTACK_MS,
                release_ms=LEVEL_RELEASE_MS,
            ),
        )
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
            pedalboard.Gain(gain_db=gain_db) if gain_db else None,
        )
        if len(tail):
            audio = tail(audio, rate, reset=True)
        audio, trimmed = peak_guard(audio)
        lift += trimmed
    else:
        # Tilaääni jätetään koskematta muuten: kompressoitu tilaääni pumppaa,
        # eikä taso siirry, joten yksi mittaus riittää.
        board = _board(
            pedalboard.Gain(gain_db=lift) if lift else None,
            pedalboard.Gain(gain_db=gain_db) if gain_db else None,
        )
        if len(board):
            audio = board(audio, rate, reset=True)
        audio, trimmed = peak_guard(audio)
        lift += trimmed

    if audio.shape[1] != frames:
        raise ChainError(t("audio.chain_length", before=frames, after=audio.shape[1]))
    done("dynamics")

    lag = lag_samples(original, audio[0], rate) if original is not None else 0
    done("lag")
    return audio, ChainResult(
        frames=frames,
        channels=audio.shape[0],
        gain_db=round(lift, 2),
        measured_lufs=measured,
        lag=lag,
    )


def apply_duck(
    audio: np.ndarray,
    rate: int,
    closed: list[tuple[int, int]],
    depth_db: float,
    fade: float,
    release: float = 0.0,
) -> np.ndarray:
    """Vaimentaa annetut jaksot ja liu'uttaa reunat.

    Liu'ut ovat epäsymmetriset ja desibeliasteikolla. Lasku on nopea, koska se
    ajoittuu toisen puhujan aloitukseen ja jää sen alle kuulumattomiin. Paluu
    on hidas, koska se osuu hiljaisuuteen eikä siinä ole mitään mikä
    peittäisi sen — nopea paluu kuuluu pohjakohinan nykäisynä.

    Lineaarinen liuku amplitudissa kuulostaa äkkinäiseltä, koska kuulo on
    logaritminen: puolivälissä ollaan jo lähes perillä. Siksi liuku tehdään
    desibeleissä.

    Vaimennus tehdään jaksoittain paikan päällä eikä koko tiedoston mittaisella
    vahvistuskäyrällä: tunnin mittainen mikki on 184 miljoonaa näytettä, ja
    erillinen float-taulukko sen päälle olisi kolme neljäsosaa gigatavusta.
    """
    if not closed or depth_db >= 0:
        return audio
    level = 10.0 ** (depth_db / 20.0)
    down_n = max(1, int(fade * rate))
    up_n = max(1, int((release or fade) * rate))
    frames = audio.shape[1]

    for start, end in closed:
        start = max(0, start)
        end = min(frames, end)
        if end <= start:
            continue
        # Liu'ut eivät saa syödä toisiaan lyhyessä jaksossa.
        span = end - start
        head = min(down_n, span // 2)
        tail = min(up_n, span - head)
        body_start, body_end = start + head, end - tail
        if body_end > body_start:
            audio[:, body_start:body_end] *= level
        if head > 0:
            audio[:, start : start + head] *= _ramp_db(0.0, depth_db, head)
        if tail > 0:
            audio[:, end - tail : end] *= _ramp_db(depth_db, 0.0, tail)
    return audio


def _ramp_db(from_db: float, to_db: float, count: int) -> np.ndarray:
    """Liuku desibeleissä, ei amplitudissa. Kuulo on logaritminen."""
    return (
        10.0 ** (np.linspace(from_db, to_db, count, dtype=np.float32) / 20.0)
    ).astype(np.float32)
