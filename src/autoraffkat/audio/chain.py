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
# Hyökkäysaika on hitaampi kuin miltä «huippukompressori» kuulostaa. Kahden
# millisekunnin hyökkäys säätää vahvistusta perusjakson **sisällä**: 110 Hz:n
# miesäänellä jakso on 9 ms, joten kompressori muokkaa aaltomuotoa eikä tasoa,
# ja se on määritelmällisesti harmonista säröä. Mitattuna sinillä 110 Hz /
# -6 dBFS: 2 ms -> THD -30,9 dB, 10 ms -> -32,9 dB, 40 ms -> -36,1 dB.
# Viisitoista millisekuntia on jaksoa pidempi kaikilla puheäänillä.
# Kompressorien kynnykset on viritetty tällä äänekkyydellä. Signaali
# normalisoidaan tavoitteeseen ennen kompressoreita, joten absoluuttinen
# kynnys tarkoittaa eri määrää tiivistystä eri tavoitteilla — ja kun oletus
# vaihtui -20:stä YouTuben -14:ään, sama kynnys söi 4,5 dB enemmän
# dynamiikkaa: crest 20,2 dB -> 15,7 dB, ja se kuuluu säröisenä.
#
# Kynnykset siirtyvät siksi tavoitteen mukana. Tavoite muuttaa tason,
# ei tiivistyksen määrää.
THRESHOLD_REFERENCE_LUFS = -20.0

PEAK_ATTACK_MS = 15.0
PEAK_RELEASE_MS = 80.0
PEAK_RATIO = 3.0
LEVEL_ATTACK_MS = 30.0
LEVEL_RELEASE_MS = 300.0
LEVEL_RATIO = 2.0

# Rinnakkaiskompressio: kuiva ja tiivistetty summataan. Äänekkyys nousee
# hiljaisten kohtien mukana, mutta transientit säilyvät kuivassa haarassa
# koskemattomina — se on se ero, jonka korva kuulee «puristettuna». Osuus on
# tiivistetyn paino; nolla olisi pelkkä kuiva.
PARALLEL_MIX = 0.6

# Sihinänpoisto. Restaurointiliitännäinen lisää ylätaajuuksia — dxRevivellä
# mitattuna +4…+5,7 dB välillä 3–20 kHz — ja se osuu suoraan s-äänteisiin,
# jotka sitten ohjaavat kompressoreita koko puheen yli. Kynnys on absoluuttinen
# ja tulee vasta normalisoinnin jälkeen, jolloin taso on tiedossa.
DEESS_HZ = 4500.0
DEESS_THRESHOLD_DB = -30.0
DEESS_RATIO = 3.0
DEESS_SMOOTH_MS = 3.0
# Huippukatto.
#
# Tämä oli pitkään staattinen koko raidan vaimennus, ja se oli ketjun suurin
# virhe. Kompressorit ovat lempeitä, joten normalisoinnin jälkeen huiput
# olivat +8…+11 dBFS; staattinen vaimennus veti silloin **koko tiedoston**
# alas sen verran. Mitattuna: -14,00 LUFS -> -25,74 (nyman) ja -22,94
# (wancke). Kolme oiretta yhdestä rivistä: kaikki 9–12 dB tavoitteen alle,
# puhujat eri tasoilla sen mukaan mikä oli kunkin kovin yksittäinen napsahdus,
# ja ohjelmatrimmin 1,8 dB merkityksetön sen rinnalla.
#
# Nyt katto hoidetaan ennakoivalla rajoittimella, joka koskee vain huippuihin.
# `peak_guard` jää viimeiseksi varmistukseksi, jonka ei pitäisi koskaan laueta.
# Katto on **true peak**, ei näytehuippu, ja siihen jätetään varaa.
# Näytehuippujen rajaaminen -1 dBFS:ään antoi mitattuna -0,42 dBTP: väliin
# jäävät huiput ylittävät näytteet, ja lossy-koodaus nostaa niitä vielä.
# Puolentoista desibelin varaa kestää AAC-muunnoksen ilman leikkautumista.
CEILING_DB = -1.5
LIMITER_OVERSAMPLE = 4
LIMITER_LOOKAHEAD_MS = 5.0
LIMITER_RELEASE_MS = 120.0


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


def load_plugin(path: str, params: dict | None = None, state: str | None = None):
    """Lataa liitännäisen ja asettaa sen tilan ja säätimet.

    ``state`` on liitännäisen oma tila base64:nä, talletettuna sen omasta
    ikkunasta (``audio/editor.py``). Se tarvitaan siksi, että kaikki mikä
    vaikuttaa lopputulokseen ei ole parametri: dxRevivella mallin valinta
    — Studio 2 ja muut — ei ole yksikään sen neljästä parametrista, vaan
    elää tilassa. Ilman tilaa ajetaan aina liitännäisen oletusmallia.

    Tila asetetaan **ennen** parametreja, jotta talletettu Mix ei jyrää
    asetuksissa olevaa: parametri on se, jota käyttöliittymän liukusäädin
    liikuttaa, ja sen on voitettava.

    Kelvoton tila ei kaada mitään. Se on läpinäkymätön tavujono jonka vain
    liitännäinen osaa lukea, ja liitännäisen vaihtuessa vanha tila on
    roskaa — mutta parametrit toimivat silti, joten väärä tila sivuutetaan
    eikä siitä tehdä virhettä.
    """
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
    apply_state(plugin, state)
    apply_parameters(plugin, params)
    return plugin


def apply_state(plugin, state: str | None) -> bool:
    """Asettaa talletetun tilan. Palauttaa onnistuiko."""
    import base64

    if not state:
        return False
    try:
        plugin.raw_state = base64.b64decode(state)
        return True
    except Exception:
        # Tila on toisesta liitännäisestä tai eri versiosta. Parametrit
        # riittävät, joten jatketaan ilman.
        return False


def read_parameters(plugin) -> dict:
    """Liitännäisen nykyiset säädettävät arvot nimen mukaan.

    Käyttäjä on voinut kääntää säätimiä liitännäisen omassa ikkunassa, ja
    käyttöliittymän liukusäätimien on seurattava — muuten sama arvo lukee
    kahdessa paikassa eri lukemaa.
    """
    out: dict = {}
    for name, param in getattr(plugin, "parameters", {}).items():
        try:
            value = getattr(plugin, name)
        except Exception:
            continue
        if isinstance(value, bool):
            out[name] = value
        elif isinstance(value, (int, float)):
            out[name] = float(value)
        elif isinstance(value, str):
            out[name] = value
    return out


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


class PluginPool:
    """Liitännäisiä säikeittäin, luotuina siinä säikeessä joka niitä käyttää.

    Jokainen rinnakkainen pala tarvitsee oman instanssin: VST3-olio on
    tilallinen eikä sitä voi ajaa kahdesta säikeestä yhtä aikaa. Se ei
    kuitenkaan riitä, että instansseja on monta — pedalboard vaatii, että
    instanssia käytetään **samassa säikeessä jossa se ladattiin**, ja
    muuten kaatuu viestiin «must be reloaded on the main thread».

    Siksi lataus tapahtuu laiskasti säiekohtaisesti ja säikeet pidetään
    hengissä koko ajon yli: lataus maksaa noin 0,2 s instanssilta, eikä sitä
    kannata maksaa jokaisesta palasta uudestaan.
    """

    def __init__(self, path: str, params: dict | None, workers: int, state=None):
        from concurrent.futures import ThreadPoolExecutor

        self.path = path
        self.params = params
        self.workers = max(1, workers)
        # **Kaikki instanssit ladataan tässä**, eli siinä säikeessä joka
        # varannon rakentaa. Laiska säiekohtainen lataus oli ensimmäinen
        # yritys ja se on juuri se mitä pedalboard kieltää: lataus onnistuu
        # vain pääsäikeessä, ja työsäikeessä se kaatuu viestiin «must be
        # reloaded on the main thread». Käsittely työsäikeestä on sallittua,
        # lataus ei — ja ero on helppo sekoittaa, koska virhe puhuu
        # `reset`istä.
        self.instances = [
            load_plugin(path, params, state) for _ in range(self.workers)
        ]
        self._pool = ThreadPoolExecutor(
            max_workers=self.workers, thread_name_prefix="plugin"
        )

    def plugin(self, index: int = 0):
        """Instanssi numero ``index``. Yksi pala, yksi instanssi."""
        return self.instances[index % len(self.instances)]

    def run(self, function, items) -> list:
        """Ajaa työt säikeissä ja palauttaa tulokset järjestyksessä."""
        return list(self._pool.map(function, items))

    def close(self) -> None:
        self._pool.shutdown(wait=True)


def load_pool(path: str, params: dict | None = None, count: int = 1, state=None):
    """Säiekohtainen liitännäisvaranto, tai ``None`` jos polkua ei ole.

    Tarkistaa polun heti pääsäikeessä, jotta virheellinen polku kerrotaan
    ennen kuin minuuttien ajo alkaa.
    """
    if not path:
        return None
    return PluginPool(path, params, count, state)


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
    if isinstance(plugin, PluginPool):
        return _apply_pool(plugin, audio, rate)
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


def _apply_pool(pool: "PluginPool", audio: np.ndarray, rate: int) -> np.ndarray:
    """Palat rinnakkain, jokainen sen säikeen omalla instanssilla."""
    frames = audio.shape[1]
    pieces = min(pool.workers, max(1, int(frames / rate / PIECE_MIN)))
    if pieces < 2:
        return pool.plugin(0).process(audio, rate, reset=True)

    margin = int(PIECE_MARGIN * rate)
    edges = [int(round(i * frames / pieces)) for i in range(pieces + 1)]
    out = np.zeros_like(audio)

    def one(index: int) -> None:
        first, last = edges[index], edges[index + 1]
        low, high = max(0, first - margin), min(frames, last + margin)
        done = pool.plugin(index).process(audio[:, low:high], rate, reset=True)
        if done.shape[1] != high - low:
            raise ChainError(
                t("audio.plugin_length", before=high - low, after=done.shape[1])
            )
        out[:, first:last] = done[:, first - low : first - low + (last - first)]

    pool.run(one, range(pieces))
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


# Naksunpoiston kynnys, kerroin paikalliseen keskiarvoon. Kalibroitu
# oikeasta materiaalista: kertoimella 3,5 löydöksiä oli 316–666 sekunnissa,
# kertoimella 25 noin yksi. Huulinaksuja on muutama minuutissa.
DECLICK_FACTOR_MAX = 40.0  # herkkyys 0.0
DECLICK_FACTOR_MIN = 10.0  # herkkyys 1.0
# Tätä tiheämpi löydös on signaalia, ei naksuja.
DECLICK_MAX_PER_SECOND = 5.0
# Montako kertaa kynnys kaksinkertaistetaan ennen kuin luovutetaan.
DECLICK_ESCALATIONS = 6
# Tätä lähempänä toisiaan olevat ylitykset ovat samaa naksua. Ilman tätä
# yksi 2 ms:n naksu on kolmisenkymmentä erillistä löydöstä — sen puolijaksot
# — jolloin katto laukeaa yhdestä naksusta ja interpolointi korjaa vain
# aallon huiput ja jättää loput paikalleen.
DECLICK_MERGE_MS = 2.0


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
    tarkoitettiin — mutta **kerroin 3,5 oli maksimin kerroin**, ja
    keskiarvoon sovellettuna se laukeaa kaikesta. Mitattuna oikealla
    puheella: 1,8–2,2 % kaikista näytteistä, 550–640 korjausta sekunnissa,
    ja signaali muuttui −10…−15 dB itseensä nähden. Se ei ole naksunpoisto
    vaan säröngeneraattori, ja juuri siltä se kuulostaa.

    Huulinaksuja on muutama minuutissa. Kerroin on siksi kalibroitu siitä,
    montako löydöstä sekunnissa syntyy oikeasta materiaalista
    (``DECLICK_FACTOR_*``), ja sen päällä on **katto**: jos löydöksiä tulee
    silti enemmän kuin ``DECLICK_MAX_PER_SECOND``, kynnystä nostetaan kunnes
    ne loppuvat, ja jos ne eivät lopu, mitään ei korjata. Detektori joka
    löytää naksun joka toisesta millisekunnista ei ole löytänyt naksuja vaan
    signaalin, ja hiljaa väärässä oleva korjaus on tässä projektissa
    kalliimpi kuin tekemättä jätetty.

    Plosiivisuoja vertaa myös **paikalliseen** keskiarvoon. Koko tiedoston
    keskiarvo teki suojasta tiedoston pituuden funktion: tunnin nauhassa,
    jossa on paljon taukoja, keskiarvo painuu alas ja suoja lakkaa
    suojaamasta juuri hiljaisissa kohdissa, joissa detektori laukeaa
    herkimmin.
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
        local_low = uniform_filter1d(np.abs(low), size=window)
        factor = DECLICK_FACTOR_MAX - (
            DECLICK_FACTOR_MAX - DECLICK_FACTOR_MIN
        ) * float(np.clip(sensitivity, 0.0, 1.0))
        seconds = max(data.size / rate, 1e-9)
        allowed = DECLICK_MAX_PER_SECOND * seconds
        gap = max(1, int(DECLICK_MERGE_MS * rate / 1000.0))
        index = np.empty(0, dtype=np.intp)
        for _ in range(DECLICK_ESCALATIONS):
            clicks = np.abs(high) > local * factor
            clicks &= ~(np.abs(low) > local_low * 3.0)
            index = np.flatnonzero(clicks)
            if index.size == 0:
                break
            found = 1 + int((np.diff(index) > gap).sum())
            if found <= allowed:
                break
            factor *= 2.0
        else:
            # Kynnys ei riittänyt millään: tämä ei ole naksuinen tiedosto
            # vaan detektori väärässä. Ei kosketa.
            continue
        if index.size == 0:
            continue
        for cluster in np.split(index, np.flatnonzero(np.diff(index) > gap) + 1):
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


def _one_pole(x: np.ndarray, rate: int, ms: float) -> np.ndarray:
    """Yksinapainen tasoitus. Vektorisoitu, koska tiedostot ovat pitkiä.

    Näytteittäinen hyökkäys/palautus-seuraaja on sarjallinen eikä sellaista
    voi ajaa Pythonissa sadalle miljoonalle näytteelle. ``lfilter`` tekee
    saman C:ssä, symmetrisillä ajoilla — riittää sekä sihinänpoiston
    verhokäyrälle että rajoittimen pehmennykselle.
    """
    from scipy import signal as _sig

    coeff = float(np.exp(-1.0 / max(1.0, ms * rate / 1000.0)))
    b, a = [1.0 - coeff], [1.0, -coeff]
    # Alkutila ensimmäisestä näytteestä: nollasta lähtevä suodin häivyttäisi
    # tiedoston alun sisään, ja rajoittimen vahvistuskäyrällä se tarkoittaisi
    # että jokainen tiedosto alkaa vaimennettuna.
    zi = _sig.lfilter_zi(b, a) * float(np.asarray(x).reshape(-1)[0])
    out, _ = _sig.lfilter(b, a, x, zi=zi)
    return out


def deess(
    audio: np.ndarray,
    rate: int,
    threshold_db: float = DEESS_THRESHOLD_DB,
    ratio: float = DEESS_RATIO,
    freq: float = DEESS_HZ,
) -> np.ndarray:
    """Vaimentaa s-äänteet ennen kompressoreita.

    Jako tehdään vähentämällä alipäästö kokonaisuudesta, jolloin osat
    summautuvat takaisin täsmälleen alkuperäiseksi eikä jakoon jää
    vaihevirhettä. Vaimennus kohdistuu vain yläkaistaan, joten puheen runko
    ei liiku mukana.

    Ennen kompressoreita siksi, että ongelma ei ole s-äänteen kovuus vaan se,
    että s ohjaa kompressoria: ilman tätä yksi sihahdus vetää koko lauseen
    alas. Restauroitu ääni on tässä erityisen altis, koska liitännäinen
    lisää juuri sille alueelle useita desibelejä.
    """
    from scipy import signal as _sig

    if audio.size == 0:
        return audio
    sos = _sig.butter(4, min(freq, rate / 2 * 0.95) / (rate / 2), output="sos")
    low = _sig.sosfilt(sos, audio, axis=-1)
    high = audio - low

    level = _one_pole(np.abs(high).max(axis=0), rate, DEESS_SMOOTH_MS)
    level_db = 20.0 * np.log10(level + 1e-9)
    over = np.maximum(0.0, level_db - threshold_db)
    reduction_db = -over * (1.0 - 1.0 / ratio)
    gain = _one_pole(10.0 ** (reduction_db / 20.0), rate, DEESS_SMOOTH_MS)
    return low + high * gain


def limiter(
    audio: np.ndarray,
    rate: int,
    ceiling_db: float = CEILING_DB,
    lookahead_ms: float = LIMITER_LOOKAHEAD_MS,
    release_ms: float = LIMITER_RELEASE_MS,
) -> tuple[np.ndarray, float]:
    """Ennakoiva rajoitin. Palauttaa ``(ääni, suurin vaimennus dB)``.

    Vaadittu vahvistus lasketaan näytteittäin, siitä otetaan liukuva minimi
    ennakkoikkunan yli ja tulos pehmennetään. Liukuva minimi on **keskitetty**,
    joten rajoitin ehtii laskea ennen huippua eikä signaali siirry: pituus ja
    kohdistus säilyvät, mikä on koko viennin ehto.

    Tämä korvaa staattisen vaimennuksen. Ero ei ole hienosäätöä: staattinen
    veti koko tiedoston alas kovimman yksittäisen näytteen mukaan, mitattuna
    9–12 dB, ja teki puhujien tasapainosta sattumanvaraisen.
    """
    from scipy import signal as _sig
    from scipy.ndimage import minimum_filter1d

    if audio.size == 0:
        return audio, 0.0
    ceiling = 10.0 ** (ceiling_db / 20.0)

    # Havainnointi ylinäytteistettynä: näytteiden **väliin** jäävä huippu on
    # se joka leikkaa D/A-muuntimessa ja lossy-koodauksessa, eikä se näy
    # näytteitä katsomalla. Vahvistus lasketaan ylinäytteistetystä ja
    # tiivistetään takaisin ottamalla kunkin ryhmän pienin.
    up = LIMITER_OVERSAMPLE
    dense = _sig.resample_poly(audio, up, 1, axis=-1)
    dense_peak = np.abs(dense).max(axis=0)
    dense_gain = np.minimum(1.0, ceiling / np.maximum(dense_peak, 1e-9))
    usable = (dense_gain.shape[0] // up) * up
    needed = dense_gain[:usable].reshape(-1, up).min(axis=1)
    if needed.shape[0] < audio.shape[1]:
        needed = np.pad(needed, (0, audio.shape[1] - needed.shape[0]), mode="edge")
    needed = needed[: audio.shape[1]]
    if needed.min() >= 1.0:
        return audio, 0.0

    window = max(1, int(lookahead_ms * rate / 1000.0))
    ahead = minimum_filter1d(needed, size=2 * window + 1, mode="nearest")
    smooth = _one_pole(ahead, rate, release_ms)
    # Pehmennys saa nostaa vahvistusta hitaasti mutta ei koskaan yli sen mitä
    # huippu sallii, muuten katto ylittyy juuri siellä missä sitä tarvitaan.
    gain = np.minimum(smooth, ahead)
    return audio * gain, float(20.0 * np.log10(max(gain.min(), 1e-9)))


def compress(
    audio: np.ndarray,
    rate: int,
    threshold_db: float,
    ratio: float,
    max_gr_db: float,
    attack_ms: float,
    release_ms: float,
) -> np.ndarray:
    """Yksi kompressorivaihe, jonka vaimennuksella on **katto**.

    ``max_gr_db`` on koko idea. Yksi kompressori joka vetää kaksitoista
    desibeliä kuulostaa kompressorilta; kolme jotka vetävät neljä kuulostaa
    tasaiselta. Rajaton vaihe myös reagoi yksittäiseen napsahdukseen koko
    lauseen voimalla, ja juuri se kuullaan pumppauksena.

    Hyökkäys tulee tason tasoituksesta ja palautus vahvistuksen
    tasoituksesta: ``minimum`` niiden välillä antaa nopean laskun ja hitaan
    paluun ilman näytteittäistä silmukkaa, jota ei sadalle miljoonalle
    näytteelle voi Pythonissa ajaa.
    """
    if audio.size == 0:
        return audio
    level = _one_pole(np.abs(audio).max(axis=0), rate, attack_ms)
    over = np.maximum(0.0, 20.0 * np.log10(level + 1e-9) - threshold_db)
    wanted = -np.minimum(over * (1.0 - 1.0 / max(ratio, 1.0001)), max_gr_db)
    instant = 10.0 ** (wanted / 20.0)
    gain = np.minimum(_one_pole(instant, rate, release_ms), instant)
    return audio * gain


# Kaistat. Alaraja pitää puhalluksen ja jyrinän erillään rungosta, yläraja
# sihinän erillään siitä: ilman jakoa yksi plosiivi vetää koko puheen alas ja
# yksi s-äänne tekee saman. Rajat ovat puheen omat, eivät musiikin.
BANDS_HZ = (250.0, 4000.0)

# Kuinka paljon kukin vaihe saa enintään vaimentaa.
#
# Owsinski: yhdessä laatikossa «less (usually way less) than 3dB of
# compression», ja kuuden desibelin vaimennus on jo «extreme processing»,
# joka kannattaa jakaa useaan vaiheeseen. Viisi on siis yläraja eikä
# tavoite: tyypillinen vaimennus jää selvästi sen alle, ja kolme vaihetta
# yhteensä pysyy sielläkin missä yksi rajaton olisi ollut kaukana yli.
MAX_GR_DB = 5.0


def split_bands(audio: np.ndarray, rate: int, edges=BANDS_HZ) -> list:
    """Jako kaistoihin, jotka summautuvat takaisin täsmälleen alkuperäiseksi.

    Ylempi kaista lasketaan vähentämällä alempi kokonaisuudesta, jolloin
    rekonstruktio on tarkka eikä jakoon jää vaihe-eroa — tavallinen
    kaistanpäästösuodinpankki vuotaa juuri risteyskohdissa.
    """
    from scipy import signal as _sig

    bands, rest = [], audio
    for edge in edges:
        sos = _sig.butter(4, min(edge, rate / 2 * 0.95) / (rate / 2), output="sos")
        low = _sig.sosfilt(sos, rest, axis=-1)
        bands.append(low)
        rest = rest - low
    bands.append(rest)
    return bands


def multiband(
    audio: np.ndarray,
    rate: int,
    threshold_db: float,
    ratio: float,
    max_gr_db: float = MAX_GR_DB,
    attack_ms: float = PEAK_ATTACK_MS,
    release_ms: float = PEAK_RELEASE_MS,
) -> np.ndarray:
    """Kompressio kaistoittain, jokainen omalla vaimennuskatollaan.

    Leveäkaistainen kompressori antaa matalien taajuuksien ohjata kaikkea:
    yksi p-äänne 100 hertsissä vetää sihinän ja rungon mukanaan, ja se
    kuullaan säröisenä vaikka mikään ei leikkaannu. Kaistoittain jokainen
    hoitaa oman ongelmansa eikä kuule toisten.
    """
    parts = split_bands(audio, rate, BANDS_HZ)
    # Sama suhde ja sama vaimennuskatto joka kaistalle. Ensin ne olivat
    # eri suuruisia — matalalle enemmän, ylös vähemmän — mikä on juuri se
    # mitä Owsinski varoittaa tekemästä: «Use the same compression ratio
    # across all bands, as differing ratios can create an unnatural sound.
    # Apply roughly the same amount of gain reduction to each band to avoid
    # altering the overall mix balance too much.» Eri määrä kaistoittain
    # muuttaa äänen sävyä ohjelman mukana, ja sen kuulee epäluonnollisena.
    out = np.zeros_like(audio)
    for part in parts:
        out = out + compress(
            part, rate, threshold_db, ratio, max_gr_db, attack_ms, release_ms
        )
    return out


# Ylipakkauksen mittari. Owsinski: «If the maximum short-term loudness is
# less than about 6LU below the true peak level, that could be an indication
# that you're compressing more than you need to.» Se on kirjan ainoa
# numeerinen raja tälle, ja se on mitattavissa — joten se mitataan.
PSR_FLOOR_LU = 6.0


def peak_to_short_term(audio: np.ndarray, rate: int) -> float:
    """True peak miinus suurin lyhyen aikavälin äänekkyys, LU.

    Alle kuuden tarkoittaa että tiivistys on mennyt pidemmälle kuin oli
    tarpeen. Palauttaa ``nan`` jos ei ole mitattavaa.
    """
    from scipy import signal as _sig

    mono = np.asarray(audio).mean(axis=0) if audio.ndim > 1 else np.asarray(audio)
    if mono.size < rate * 3:
        return float("nan")
    peak = 20.0 * np.log10(np.abs(_sig.resample_poly(mono, 4, 1)).max() + 1e-12)
    window = int(3 * rate)
    step = max(1, int(0.5 * rate))
    best = -np.inf
    for start in range(0, len(mono) - window + 1, step):
        block = mono[start : start + window]
        level = -0.691 + 10.0 * np.log10(float(np.mean(block**2)) + 1e-20)
        best = max(best, level)
    return float(peak - best) if np.isfinite(best) else float("nan")


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
        if lift:
            audio = _board(pedalboard.Gain(gain_db=lift))(audio, rate, reset=True)
        # Sihinä pois ennen kompressoreita: muuten yksi s ohjaa koko lauseen.
        audio = deess(audio, rate)

        # Rinnakkaiskompressio. Tiivistetty haara nostaa hiljaiset kohdat,
        # kuiva haara pitää transientit — sarjassa ajettuna sama tiivistys
        # veisi molemmat.
        # Kynnykset seuraavat tavoitetta, ks. THRESHOLD_REFERENCE_LUFS.
        offset = (
            0.0
            if target_lufs is None
            else float(target_lufs) - THRESHOLD_REFERENCE_LUFS
        )
        # Kolme rajattua vaihetta yhden rajattoman sijaan. Ensin kaistoittain,
        # jottei plosiivi ohjaa sihinää eikä toisin päin; sitten kaksi lempeää
        # leveäkaistaista, jotka tasaavat kokonaisuuden. Jokainen enintään
        # MAX_GR_DB, joten yhteensäkin vaimennus on maltillinen ja tasainen.
        compressed = multiband(
            audio,
            rate,
            settings.peak_threshold_db + offset,
            PEAK_RATIO,
            MAX_GR_DB,
            PEAK_ATTACK_MS,
            PEAK_RELEASE_MS,
        )
        compressed = compress(
            compressed,
            rate,
            settings.leveler_threshold_db + offset,
            LEVEL_RATIO,
            MAX_GR_DB,
            LEVEL_ATTACK_MS,
            LEVEL_RELEASE_MS,
        )
        compressed = compress(
            compressed,
            rate,
            settings.leveler_threshold_db + offset + 4.0,
            LEVEL_RATIO,
            MAX_GR_DB,
            LEVEL_ATTACK_MS * 4,
            LEVEL_RELEASE_MS * 2,
        )
        audio = audio * (1.0 - PARALLEL_MIX) + compressed * PARALLEL_MIX

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

        # Katto rajoittimella, ja sen jälkeen taso uudestaan: rajoitin syö
        # äänekkyyttä sen verran kuin se leikkaa, ja puhujien on osuttava
        # samaan lukemaan. Yksi kierros riittää, koska korjaus on pieni ja
        # rajoitin ajetaan sen perään uudestaan.
        audio, _ = limiter(audio, rate)
        # Rajoitin syö äänekkyyttä sen verran kuin se leikkaa, ja korjaus
        # nostaa huiput takaisin rajoittimen kynsiin — yksi kierros jää siis
        # vajaaksi. Kolme riittää: mitattuna ensimmäinen kierros jäi 1–2 dB
        # tavoitteesta, kolmannen jälkeen ero on alle 0,3 dB. Tavoite on nyt
        # jakelualustan lukema eikä makuasia, joten se on osuttava.
        for _ in range(3):
            if target_lufs is None:
                break
            settled = loudness(audio.mean(axis=0), rate)
            if settled is None or abs(target_lufs - settled) <= 0.3:
                break
            step = float(target_lufs - settled)
            audio = _board(pedalboard.Gain(gain_db=step))(audio, rate, reset=True)
            audio, _ = limiter(audio, rate)
            lift += step
        # Viimeinen varmistus. Rajoittimen jälkeen tämän ei pitäisi laueta,
        # ja jos laukeaa, se on rajoittimessa oleva vika eikä turvaverkon työ.
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
