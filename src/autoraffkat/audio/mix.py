"""Äänenkäsittelyn ohjaus: mitkä tiedostot, minne ja milloin.

Itse signaalinkäsittely on ``chain.py``:ssä. Tämä moduuli päättää mitä
käsitellään, tarkistaa tuloksen ja pitää huolen kahdesta säännöstä, joista
kumpikaan ei ole neuvoteltavissa:

**Alkuperäiseen tiedostoon ei kosketa.** Käsitelty ääni menee rinnakkaiseen
``nimi [mix].wav``:iin. Päälle kirjoittaminen rikkoisi kaksi asiaa kerralla:
verhokäyrän välimuisti avainnetaan muokkausajalla, joten se laskettaisiin
uudestaan, ja uusi laskenta osuisi käsiteltyyn ääneen.

**Näytemäärä ei saa muuttua, eikä ääni saa siirtyä.** Vienti viittaa
käsiteltyyn tiedostoon samoilla ajoilla kuin alkuperäiseen. Pituus
tarkistetaan ketjussa ja uudestaan valmiista tiedostosta; siirtymä mitataan
ristikorrelaatiolla, koska ulkoinen liitännäinen voi ilmoittaa viiveensä
väärin ja tuottaa oikean mittaisen mutta väärässä kohdassa olevan raidan.

Analyysi ajetaan aina raa'asta äänestä. Kompressori nostaa pohjakohinaa
sanojen välissä ja tasoittaa mikkien keskinäisen eron — herkkyys on kynnys
pohjan yli ja päällekkäispuheen sääntö vertaa mikkejä toisiinsa, joten
käsitellystä äänestä laskettu päätös olisi huonompi.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..decide import _runs, drop_short, open_windows, trim_end
from ..i18n import t
from ..model import HOP, AudioSettings
from . import chain
from .binaries import get_binary_path
from .chain import ChainError

# Formaatit jotka luetaan suoraan. Muut puretaan ffmpegillä: kameran ääni on
# mp4:n sisällä, eikä pedalboardin lukija avaa sitä.
READABLE = {
    ".wav",
    ".wave",
    ".aif",
    ".aiff",
    ".aifc",
    ".flac",
    ".w64",
    ".caf",
    ".ogg",
    ".mp3",
}

MIX_SUFFIX = " [mix]"
ROOM_SUFFIX = " [room]"
ROOM_ROLE = "effects.Tilaääni"

# Suurin sallittu siirtymä. Yksi millisekunti on jo kuultavissa kammalla,
# jos tilaääni ja mikki soivat päällekkäin.
MAX_LAG_MS = 1.0
TIMEOUT = 3600


class MixError(Exception):
    """Ääntä ei voitu käsitellä."""


def sibling(path: str, suffix: str) -> str:
    """``x.wav`` -> ``x [mix].wav``. Aina WAV, myös mp4-lähteestä."""
    base, _ = os.path.splitext(path)
    return f"{base}{suffix}.wav"


def is_current(source: str, target: str) -> bool:
    """Onko käsitelty tiedosto tuoreempi kuin lähde.

    Käsittely on hidas ja sama lähde tulee vastaan joka viennissä.
    Vanhentunut tunnistetaan muokkausajasta, kuten verhokäyrän välimuistissa.

    Tämä on vasta puolet: sama lähde eri asetuksilla antaa eri tuloksen,
    eikä se näy muokkausajassa mitenkään. Katso ``is_fresh``.
    """
    if not os.path.exists(target) or not os.path.exists(source):
        return False
    return os.path.getmtime(target) >= os.path.getmtime(source)


# Asetukset joista lopputulos riippuu. ``enabled`` ja ``room_track``
# päättävät tehdäänkö työtä lainkaan, eivät miltä tulos kuulostaa, joten ne
# eivät ole mukana. Lista on tahallaan kirjoitettu auki eikä johdettu
# kentistä: uusi säädin ei saa livahtaa mukaan tai pois huomaamatta, ja
# ``test_fingerprint_covers_every_setting`` kaatuu jos niin käy.
FINGERPRINT_FIELDS = (
    "high_pass_hz",
    "target_lufs",
    "peak_threshold_db",
    "leveler_threshold_db",
    "declick",
    "declick_sensitivity",
    "plugin_path",
    "plugin_params",
    "duck",
    "duck_db",
    "duck_lookahead",
    "duck_hold",
    "duck_min_open",
    "duck_dominance_db",
    "duck_fade",
    "duck_release",
    "duck_min_closed",
    "gain_db",
    "room_db",
    "program_target",
    "plugin_workers",
)

# Kasvatetaan kun ketju itse muuttuu niin että vanha tulos ei enää vastaa
# samoilla asetuksilla syntyvää. Sama tarkoitus kuin verhokäyrän
# ``CACHE_VERSION``:illa.
FINGERPRINT_VERSION = 1


def stamp_dir() -> Path:
    """Käsittelyn jälkien hakemisto.

    Erillään lähteen vierestä, koska tämä on välimuistia eikä käyttäjän
    aineistoa: mikkikansioon ei kuulu tiedostoa jota kukaan ei ole pyytänyt.
    Turvallista tyhjentää — tyhjennys maksaa yhden uuden käsittelyn.
    """
    root = Path.home() / "Library" / "Caches" / "autoraffkat" / "mix"
    root.mkdir(parents=True, exist_ok=True)
    return root


def fingerprint(job: dict, settings: AudioSettings) -> str:
    """Mistä asetuksista tämä tiedosto syntyisi juuri nyt.

    Mukana on lähde (polku, koko, muokkausaika), työn omat arvot ja
    ``FINGERPRINT_FIELDS``. Liitännäisen muokkausaika on mukana siksi, että
    päivitetty liitännäinen kuulostaa eri tavalta samoilla säätimillä.

    Yksi asia jää ulkopuolelle tietoisesti: vaimennuksen ajoitus tulee samasta
    puheentunnistuksesta kuin kuvan leikkaus, joten raitojen herkkyys vaikuttaa
    siihen. Sitä ei ole täällä, koska ``adopt`` ajetaan latauksessa ja
    viennissä pelkillä ``stat``-kutsuilla — ruudukon rakentaminen siinä kohtaa
    rikkoisi juuri sen säännön, ettei tiedostojen lukeminen kuulu silmukkaan.
    """
    plugin_path = settings.plugin_path
    try:
        plugin_stamp = os.path.getmtime(plugin_path) if plugin_path else 0.0
    except OSError:
        plugin_stamp = 0.0
    try:
        st = os.stat(job["source"])
        source = [os.path.abspath(job["source"]), st.st_size, st.st_mtime_ns]
    except OSError:
        source = [os.path.abspath(job["source"]), 0, 0]
    raw = {
        "version": FINGERPRINT_VERSION,
        "source": source,
        "plugin_mtime": plugin_stamp,
        "job": {
            key: job.get(key)
            for key in ("target_lufs", "gain_db", "speech", "mono", "bit_depth")
        },
        "settings": {name: getattr(settings, name) for name in FINGERPRINT_FIELDS},
    }
    text = json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _stamp_path(target: str) -> Path:
    key = hashlib.sha1(os.path.abspath(target).encode("utf-8")).hexdigest()
    return stamp_dir() / f"{key}.txt"


def read_stamp(target: str) -> str:
    """Millä asetuksilla levyllä oleva tiedosto tehtiin, tai ``""``."""
    try:
        return _stamp_path(target).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_stamp(job: dict, settings: AudioSettings) -> None:
    """Merkitsee millä asetuksilla juuri valmistunut tiedosto tehtiin."""
    try:
        _stamp_path(job["target"]).write_text(
            fingerprint(job, settings), encoding="utf-8"
        )
    except OSError:
        # Merkinnän puuttuminen maksaa yhden turhan käsittelyn, ei tulosta.
        pass


def is_fresh(job: dict, settings: AudioSettings) -> bool:
    """Kelpaako levyllä oleva tulos sellaisenaan.

    Pelkkä muokkausaika ei riitä, ja ero on juuri se joka sai painikkeen
    näyttämään rikkinäiseltä: liitännäisen vaihto, sen säätimet, tavoitetaso
    tai vaimennuksen syvyys eivät koske lähdetiedostoon mitenkään, joten
    ``is_current`` piti vanhaa tulosta ajan tasalla ja käsittely palasi
    hiljaa tekemättä mitään.

    Tuntematon merkintä on vanhentunut: käsitelty tiedosto jonka
    syntyhistoriaa ei tiedetä voi olla mistä tahansa asetuksista.
    """
    if not is_current(job["source"], job["target"]):
        return False
    return read_stamp(job["target"]) == fingerprint(job, settings)


def weight_of(path: str) -> float:
    """Tiedoston osuus työstä, tiedostokokona.

    Tiedostot ovat eri mittaisia — samassa jaksossa 20 minuuttia ja 64 —
    joten «2/4» ei kerro paljonko on jäljellä eikä yhtä suuriksi oletettu
    arvio osu lähellekään. Koko on saatavissa ilman ffprobea ja on samassa
    muodossa olevilla tiedostoilla suoraan verrannollinen kestoon.
    """
    try:
        return float(max(1, os.path.getsize(path)))
    except OSError:
        return 1.0


def frame_count(path: str) -> int | None:
    """Äänen näytemäärä ffprobella, tai ``None`` jos ei selviä."""
    try:
        ffprobe_bin = get_binary_path("ffprobe")
        done = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=duration_ts,nb_samples,sample_rate,duration",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        streams = json.loads(done.stdout or "{}").get("streams") or []
    except (
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        FileNotFoundError,
    ):
        return None
    if not streams:
        return None
    stream = streams[0]
    for name in ("nb_samples", "duration_ts"):
        raw = stream.get(name)
        if raw not in (None, "N/A"):
            try:
                return int(raw)
            except (TypeError, ValueError):
                continue
    try:
        return int(round(float(stream["duration"]) * int(stream["sample_rate"])))
    except (KeyError, TypeError, ValueError):
        return None


def extract_dir() -> Path:
    """Puretun äänen välimuisti. Turvallista tyhjentää milloin tahansa."""
    root = Path.home() / "Library" / "Caches" / "autoraffkat" / "extracted"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_readable(path: str) -> str:
    """Palauttaa polun, jonka äänilukija osaa avata.

    Kameran ääni on mp4:n sisällä, joten se puretaan WAViksi välimuistiin.
    Purku ei kirjoita median viereen: se on väliaikaista eikä kuulu käyttäjän
    hakemistoon.
    """
    if os.path.splitext(path)[1].lower() in READABLE:
        return path
    stat = os.stat(path)
    target = (
        extract_dir() / f"{Path(path).stem}-{stat.st_size}-{int(stat.st_mtime)}.wav"
    )
    if target.exists():
        return str(target)
    tmp = target.with_suffix(".tmp.wav")
    try:
        ffmpeg_bin = get_binary_path("ffmpeg")
        done = subprocess.run(
            [
                ffmpeg_bin,
                "-y",
                "-v",
                "error",
                "-i",
                path,
                "-vn",
                "-map",
                "a:0",
                "-c:a",
                "pcm_f32le",
                str(tmp),
            ],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        raise MixError(t("audio.extract_failed", name=exc)) from exc
    if done.returncode != 0 or not tmp.exists():
        tail = (done.stderr or "").strip().splitlines()
        raise MixError(
            t("audio.extract_failed", name=os.path.basename(path))
            + (f" — {tail[-1]}" if tail else "")
        )
    tmp.replace(target)
    return str(target)


@dataclass
class MixResult:
    """Käsittelyn tulos vientiä varten."""

    # media key -> käsitelty tiedosto. Vienti viittaa näihin alkuperäisten
    # sijaan; ajat pysyvät samoina, koska näytemäärä on sama.
    replacements: dict[str, str] = field(default_factory=dict)
    # (media key, käsitelty tiedosto) tilaäänelle, omalle lanelleen.
    room: list[tuple[str, str]] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    # Normalisoinnin nosto raidoittain. Näytetään käyttöliittymässä, koska
    # nosto nostaa myös pohjakohinaa eikä sitä saa tehdä huomaamatta.
    gains: dict[str, float] = field(default_factory=dict)
    processed: int = 0
    # Mitattu ohjelmatrimmi, näytetään käyttäjälle: se selittää miksi
    # yksittäinen stemi mittaa tavoitteen alle.
    program_trim: float = 0.0
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def closed_ranges(
    item, closed, program_start: float, rate: int
) -> list[tuple[int, int]]:
    """Missä tiedoston kohdissa mikki on kiinni, näyteväleinä.

    Ruudukko on aikajanan aikaa, tiedosto omaansa. Muunnos tehdään
    esiintymittäin, koska kunkin palan sisällä kuvaus on lineaarinen.
    Ruudukon ulkopuolelle jäävää osaa ei vaimenneta: siitä ei ole tietoa, eikä
    vienti käytä sitä.
    """
    out: list[tuple[int, int]] = []
    for start, end, value in _runs(closed.astype(np.int8)):
        if not value:
            continue
        low = program_start + start * HOP
        high = program_start + end * HOP
        for placement in item.placements:
            first = max(low, float(placement.offset))
            last = min(high, float(placement.end))
            if last <= first:
                continue
            # tiedostoaika = klipin start - assetin start + (aikajana - offset)
            base = float(placement.start - item.asset_start - placement.offset)
            out.append(
                (int(round((base + first) * rate)), int(round((base + last) * rate)))
            )
    return out


def _jobs(timeline, roles, settings: AudioSettings) -> list[dict]:
    """Käsiteltävät tiedostot: mikit, ja tilaääni jos sellainen on valittu."""
    jobs: list[dict] = []
    for speaker, keys in roles.mics.items():
        for track_key in keys:
            for item in timeline.track_media(track_key):
                if item.path:
                    jobs.append(
                        {
                            "key": item.key,
                            "name": item.name,
                            "speaker": speaker,
                            "item": item,
                            "source": item.path,
                            "target": sibling(item.path, MIX_SUFFIX),
                            "target_lufs": settings.target_lufs,
                            "gain_db": settings.gain_db,
                            "speech": True,
                            "weight": weight_of(item.path),
                        }
                    )
    if settings.room_track:
        for item in timeline.track_media(settings.room_track):
            if item.path and item.has_audio:
                # Tilaääni normalisoidaan samaan tavoitteeseen mutta asetetun
                # verran hiljemmalle, jotta taso on ennustettava eikä riipu
                # siitä miten kuuma kameran mikki sattui olemaan.
                jobs.append(
                    {
                        "key": item.key,
                        "name": item.name,
                        "source": item.path,
                        "target": sibling(item.path, ROOM_SUFFIX),
                        "target_lufs": settings.target_lufs + settings.room_db,
                        "gain_db": 0.0,
                        "speech": False,
                        # Tunnelmaraita ei tarvitse stereokuvaa eikä 24
                        # bittiä: monona ja 16 bitissä se on kuudesosa.
                        "mono": True,
                        "bit_depth": 16,
                        "weight": weight_of(item.path),
                    }
                )
    return jobs


# Ohjelmatrimmin mittausikkuna. Trimmi on tilastollinen suure — kuinka paljon
# puhujat menevät päällekkäin ja kuinka paljon mikit kuulevat toisiaan — eikä
# se muutu jakson aikana niin paljon että koko jakson lukeminen kannattaisi.
# Kaksitoista minuuttia keskeltä maksaa muutaman sekunnin.
PROGRAM_WINDOW = 720.0

# Trimmiä ei sallita rajattomasti kumpaankaan suuntaan: se on korjaus
# päällekkäisyyteen, ei toinen normalisointi. Kuudesta desibelistä ylöspäin
# olisi kyse mittausvirheestä, ei summasta.
MAX_PROGRAM_TRIM = 6.0


def _item_span(item) -> float:
    """Median kokonaiskesto aikajanalla, ikkunan ankkurin valintaan."""
    return sum(float(p.duration) for p in item.placements)


def program_trim(jobs: list[dict], settings: AudioSettings) -> float:
    """Kuinka paljon mikkien summa on tavoitteen yli, desibeleinä (≤ 0).

    Tavoitetaso on **ohjelman** taso, ei yhden stemin. Kaksi -14 LUFS:n
    mikkiä ei summaudu -14:ään: tällä aineistolla mitattu summa oli -12,3.
    Ero ei ole 3 dB (silloin molemmat puhuisivat koko ajan) eikä 0 dB
    (silloin toinen mikki olisi täysin hiljaa toisen puhuessa), joten se
    mitataan eikä arvata.

    Mitataan raa'asta äänestä ennen käsittelyä ja rajatusta ikkunasta.
    Tarkka vastaus vaatisi koko ohjelman käsittelyn ensin ja jokaisen
    tiedoston kirjoittamisen toiseen kertaan — noin viidenneksen lisää
    aikaa — ja ero on murto-osa desibeliä. Lopullinen taso asetetaan
    Final Cutissa joka tapauksessa.

    Ikkuna on aikajanan aikaa, koska summa on aikajanalla. Tiedostoaika
    lasketaan esiintymistä samalla kaavalla kuin ``closed_ranges``:issa.
    """
    from pedalboard.io import AudioFile

    mics = [
        job
        for job in jobs
        if job.get("speech")
        and job.get("item") is not None
        and os.path.exists(job["source"])
        and os.path.splitext(job["source"])[1].lower() in READABLE
    ]
    if len(mics) < 2:
        # Yksi mikki *on* ohjelma: sen oma taso on jo oikea.
        return 0.0

    # Ikkuna ankkuroidaan pisimpään mikkitiedostoon eikä koko aikajanan
    # keskelle: monikamerassa osat ovat peräkkäin, ja aikajanan keskikohta
    # osuu yhteen osaan — toisen osan tiedostot mittautuisivat hiljaisiksi
    # ja koko mittaus kaatuisi siihen. Saman osan mikit ovat aina päällekkäin.
    anchor = max((job["item"] for job in mics), key=_item_span)
    low, high = float(anchor.timeline_start), float(anchor.timeline_end)
    span = min(PROGRAM_WINDOW, high - low)
    if span <= 1.0:
        return 0.0
    middle = (low + high) / 2
    window = (middle - span / 2, middle + span / 2)

    rate = 0
    voices = 0
    total: np.ndarray | None = None
    for job in mics:
        item = job["item"]
        with AudioFile(job["source"]) as handle:
            if rate and handle.samplerate != rate:
                # Eri näytetaajuudet vaatisivat uudelleennäytteistyksen.
                # Trimmi on valinnainen tarkennus, ei syy hidastaa ajoa.
                _log("ohjelmatrimmi ohitettu: mikeillä eri näytetaajuus")
                return 0.0
            rate = handle.samplerate
            if total is None:
                total = np.zeros(int(span * rate), dtype=np.float32)
            here = np.zeros_like(total)
            for placement in item.placements:
                first = max(window[0], float(placement.offset))
                last = min(window[1], float(placement.end))
                if last <= first:
                    continue
                base = float(placement.start - item.asset_start - placement.offset)
                start = int(round((base + first) * rate))
                frames = int(round((last - first) * rate))
                if start < 0 or frames <= 0 or start >= handle.frames:
                    continue
                frames = min(frames, handle.frames - start)
                handle.seek(start)
                block = handle.read(frames).mean(axis=0)
                at = int(round((first - window[0]) * rate))
                end = min(len(here), at + len(block))
                if end > at:
                    here[at:end] = block[: end - at]
        measured = chain.loudness(here, rate)
        if measured is None:
            # Tämä mikki ei ole äänessä tässä ikkunassa — toisen osan
            # tiedosto tai hiljainen kohta. Se ei ole virhe eikä se lisää
            # summaan mitään.
            continue
        # Sama nosto jonka käsittely tekee: summa mitataan siitä mitä
        # aikajanalle on tulossa, ei siitä mitä levyllä on nyt.
        total += here * float(10 ** ((settings.target_lufs - measured) / 20))
        voices += 1

    if voices < 2 or total is None:
        return 0.0
    summed = chain.loudness(total, rate)
    if summed is None:
        return 0.0
    trim = float(settings.target_lufs - summed)
    trim = round(max(-MAX_PROGRAM_TRIM, min(0.0, trim)), 2)
    _log(f"ohjelmatrimmi {trim:+.2f} dB (summa {summed:.2f} LUFS)")
    return trim


# Tiedoston työ vaiheittain: luku ja kirjoitus ovat gigatavun tiedostolla
# oikeaa aikaa, ketju on loput. Ketjun sisäinen jako on ``chain.STAGES_*``.
READ_SHARE = 0.08
CHAIN_SHARE = 0.84
WRITE_SHARE = 0.08


def _run_one(
    job: dict,
    settings: AudioSettings,
    plugin,
    masks: dict | None = None,
    program_start: float = 0.0,
    stage=None,
    trim_db: float = 0.0,
) -> float:
    """Käsittelee yhden tiedoston. Palauttaa normalisoinnin noston.

    ``stage(nimi, osuus)`` kertoo missä kohtaa tätä tiedostoa mennään.
    Liitännäinen on kallein vaihe eikä kerro itsestään mitään kesken ajon,
    joten vaiheen tarkkuus on se mitä edistymisestä on saatavissa — ja se
    riittää siihen, ettei palkki seiso tunnin tiedoston ajan paikallaan.
    """
    from pedalboard.io import AudioFile

    def report(name: str, share: float) -> None:
        if stage is not None:
            stage(name, share)

    source = ensure_readable(job["source"])
    with AudioFile(source) as handle:
        audio = handle.read(handle.frames)
        rate = handle.samplerate
    report("read", READ_SHARE)
    if audio.shape[1] == 0:
        raise MixError(t("audio.empty_file", name=os.path.basename(job["source"])))
    if job.get("mono") and audio.shape[0] > 1:
        audio = audio.mean(axis=0, keepdims=True)

    # Ohjelmatrimmi kuuluu **tavoitteeseen**, ei vahvistukseen. Ketju
    # normalisoi lopuksi tavoitteeseen, joten vahvistukseen lisätty trimmi
    # kumoutuu siinä kokonaan — mitattuna stemit osuivat -14,1:een kun niiden
    # piti osua -15,8:aan. Tavoitteessa se säilyy, koska normalisointi ajaa
    # juuri siihen lukemaan.
    target = job.get("target_lufs")
    if target is not None and trim_db:
        target = float(target) + trim_db

    audio, info = chain.process(
        audio,
        rate,
        settings,
        job.get("gain_db", 0.0),
        job.get("speech", True),
        target,
        plugin,
        stage=lambda name, frac: report(name, READ_SHARE + CHAIN_SHARE * frac),
    )

    # Vaimennus viimeisenä: sitä ennen mitattu taso koskee puhetta, ei
    # puheen ja hiljaisuuden keskiarvoa.
    mask = (masks or {}).get(job.get("speaker"))
    if mask is not None and settings.duck and settings.duck_db < 0:
        audio = chain.apply_duck(
            audio,
            rate,
            closed_ranges(job["item"], mask, program_start, rate),
            settings.duck_db,
            settings.duck_fade,
            settings.duck_release,
        )
    report("duck", READ_SHARE + CHAIN_SHARE)

    limit = int(rate * MAX_LAG_MS / 1000)
    if abs(info.lag) > limit:
        raise MixError(
            t(
                "audio.plugin_shifted",
                samples=info.lag,
                ms=info.lag / rate * 1000,
                name=os.path.basename(job["source"]),
            )
        )

    # Alkuperäiseen ei kosketa. ``sibling`` takaa tämän jo, mutta tarkistus
    # on kirjoituskohdassa, koska kohteen laskeminen on muualla ja yksi
    # virhe siellä olisi peruuttamaton.
    if os.path.abspath(job["target"]) == os.path.abspath(job["source"]):
        raise MixError(t("audio.would_overwrite", name=os.path.basename(job["source"])))

    tmp = job["target"] + ".tmp.wav"
    with AudioFile(
        tmp, "w", rate, audio.shape[0], bit_depth=job.get("bit_depth", 24)
    ) as out:
        out.write(np.ascontiguousarray(audio))
    written = frame_count(tmp)
    if written is not None and written != info.frames:
        os.remove(tmp)
        raise MixError(
            t(
                "audio.written_length",
                before=info.frames,
                after=written,
                name=os.path.basename(job["source"]),
            )
        )
    os.replace(tmp, job["target"])
    write_stamp(job, settings)
    report("write", READ_SHARE + CHAIN_SHARE + WRITE_SHARE)
    return info.gain_db


def freshness(timeline, roles, settings: AudioSettings) -> tuple[int, int]:
    """(ajan tasalla, kaikkiaan) — mitä painike kertoo käyttäjälle.

    Käyttöliittymän on erotettava kolme tilaa, jotka näyttivät ennen samalta:
    ei käsitelty, käsitelty, ja käsitelty mutta asetukset ovat sen jälkeen
    muuttuneet. Ilman tätä painike palasi joka kerta tekstiin «Käsittele
    ääni», eikä valmiiseen työhön voinut luottaa katsomalla.

    Pelkkiä ``stat``-kutsuja ja pieniä merkintätiedostoja, kuten ``adopt``:
    tämä saa olla kyselyn tiellä, äänen lukeminen ei.
    """
    if timeline is None or not settings.enabled:
        return 0, 0
    jobs = _jobs(timeline, roles, settings)
    return sum(1 for job in jobs if is_fresh(job, settings)), len(jobs)


def adopt(timeline, roles, settings: AudioSettings) -> MixResult:
    """Ottaa käyttöön ne käsitellyt tiedostot jotka ovat jo levyllä.

    Käsittely tehdään kerran, mutta ``MixResult`` on istunnon tila. Ilman
    tätä jakson uusi avaus veisi raakaa ääntä pelkästään siksi että nappia
    ei painettu tällä kertaa — vaikka ajan tasalla oleva ``[mix]`` on
    lähteen vieressä. Eron kuulee vasta Final Cutissa, jolloin leikkaus on
    jo tehty eikä sille ole enää muuta lähdettä.

    Pelkkiä ``stat``-kutsuja: ei lue ääntä eikä lataa liitännäistä. Vanhaa
    ei oteta: ``is_fresh`` vertaa muokkausajan lisäksi asetuksia, samoin
    kuin ``process`` — muuten vienti käyttäisi tiedostoa jonka käsittely
    juuri totesi vanhentuneeksi.
    """
    result = MixResult()
    if not settings.enabled:
        return result
    for job in _jobs(timeline, roles, settings):
        if os.path.exists(job["source"]) and is_fresh(job, settings):
            result.skipped += 1
            _record(result, job)
    return result


def duck_masks(grid, settings: AudioSettings) -> dict:
    """Puhujakohtaiset «mikki kiinni» -maskit ruudukossa.

    Ohjaus on sama puheentunnistus kuin kuvan leikkauksessa — se on jo säädetty
    herkkyyssäätimillä ja näkyy esikatselupalkissa — mutta omilla ajoillaan.

    Kolme sääntöä, joista jokainen korjaa yhden tavan kuulostaa pahalta:

    **Vaimennus tapahtuu vain toisen puheen alla.** Jos kukaan ei puhu, kaikki
    mikit jäävät auki. Hiljaisuuteen laskeva portti kuuluu aina, koska mikään
    ei peitä sitä; toisen puhujan aloituksen alla lasku katoaa kuulumattomiin.
    Tämä on syy siihen että maskeri lasketaan **ilman ennakkoa**: lasku ei saa
    alkaa ennen kuin peittävä ääni on jo tullut.

    **Kovin voittaa.** Kaksi mikkiä samassa huoneessa kuulevat molemmat
    puhujat, joten kumpikin ylittää kynnyksen — mitattuna 41 % ajasta yhtä
    aikaa. Vuoto on kuitenkin mediaanissa 12,8 dB hiljempaa, joten auki jää
    kovin ja ne jotka ovat ``duck_dominance_db``:n sisällä siitä.

    **Lyhyitä vaimennuksia ei tehdä.** Ilman tätä syntyi 20 millisekunnin
    kuoppia: naksahdus, ei vaimennus.
    """
    if grid is None or not settings.duck or len(grid.speakers) < 2:
        return {}
    active = np.stack([lane.on for lane in grid.speakers])
    levels = np.stack([lane.level for lane in grid.speakers])
    # Vain äänessä olevat kilpailevat; hiljainen ei voi olla kovin.
    loudest = np.where(active, levels, -300.0).max(axis=0)
    keep = active & (levels >= loudest - settings.duck_dominance_db)

    # Auki: ennakko mukana, jotta sanan alku ei katoa.
    opened = [
        open_windows(
            keep[i], settings.duck_lookahead, settings.duck_hold, settings.duck_min_open
        )
        for i in range(len(grid.speakers))
    ]
    # Peittävä puhe. Ilman ennakkoa, koska tämä ajoittaa laskun: lasku ei saa
    # alkaa ennen kuin peittävä ääni on tullut. Lopusta leikataan pito ja
    # paluun mitta pois, jotta myös nousu ehtii tapahtua peittävän äänen alla
    # eikä sen jälkeisessä hiljaisuudessa.
    masking = [
        trim_end(
            open_windows(keep[i], 0.0, settings.duck_hold, settings.duck_min_open),
            settings.duck_hold + settings.duck_release,
        )
        for i in range(len(grid.speakers))
    ]

    out = {}
    for i, lane in enumerate(grid.speakers):
        others = np.zeros_like(opened[i])
        for j in range(len(grid.speakers)):
            if j != i:
                others |= masking[j]
        closed = others & ~opened[i]
        out[lane.name] = drop_short(closed, settings.duck_min_closed)
    return out


def process(
    timeline,
    roles,
    settings: AudioSettings,
    grid=None,
    program_start: float = 0.0,
    progress=None,
    force: bool = False,
) -> MixResult:
    """Käsittelee mikit ja tilaäänen. Hidas — ei kuulu säätösilmukkaan.

    Liitännäinen ladataan kerran ja sen tila nollataan tiedostojen välissä:
    lataus maksaa, mutta edellisen tiedoston häntä ei saa vuotaa seuraavaan.

    ``force`` ohittaa tuoreuden ja käsittelee kaiken uudestaan. Se on
    käyttäjän tahallinen valinta eikä oletus: ajo maksaa minuutteja, joten
    käyttöliittymä kysyy sen erikseen.
    """
    result = MixResult()
    if not settings.enabled:
        return result

    jobs = _jobs(timeline, roles, settings)
    if not jobs:
        _log("ei käsiteltäviä raitoja")
        return result

    todo = []
    for job in jobs:
        if not os.path.exists(job["source"]):
            result.errors[job["key"]] = t("audio.source_missing", path=job["source"])
        elif not force and is_fresh(job, settings):
            _log(f"ohitetaan {job['name']}: ajan tasalla")
            result.skipped += 1
            _record(result, job)
        else:
            todo.append(job)
    if not todo:
        # Ilman tätä riviä painike näyttää rikkinäiseltä: ei lokia, ei
        # palkkia, ei uusia tiedostoja — eikä mitään mikä kertoisi että ajo
        # todella tapahtui ja oli valmis ennen kuin se alkoi.
        _log(f"ei mitään tehtävää: {len(jobs)} tiedostoa on jo ajan tasalla")
        return result

    try:
        workers = chain.worker_count(settings.plugin_workers)
        plugin = chain.load_pool(
            settings.plugin_path, settings.plugin_params, workers
        )
        if plugin is not None and workers > 1:
            _log(f"liitännäinen {workers} rinnakkaisena palana")
    except ChainError as exc:
        result.errors["plugin"] = str(exc)
        return result

    masks = duck_masks(grid, settings)
    if settings.duck:
        # Maskit avaimetaan puhujan nimellä ja työt hakevat samalla nimellä.
        # Hiljainen avainten eroaminen olisi juuri se vika joka on jo kerran
        # jäänyt huomaamatta: asetus päällä, tuloksessa ei mitään.
        wanted = {job.get("speaker") for job in jobs if job.get("speech")}
        matched = wanted & set(masks)
        if not matched:
            result.errors["duck"] = t(
                "audio.duck_none", speakers=", ".join(sorted(w for w in wanted if w))
            )
            _log(result.errors["duck"])
        else:
            covered = sum(int(masks[name].sum()) for name in matched)
            _log(
                f"vaimennus: {len(matched)}/{len(wanted)} mikkiä, "
                f"{covered * HOP / 60:.1f} min vaimennettavaa"
            )
    # Mitataan kaikista mikeistä eikä vain käsiteltävistä: summa on koko
    # ohjelma riippumatta siitä mikä tiedosto sattuu olemaan jo valmis.
    trim = program_trim(jobs, settings) if settings.program_target else 0.0
    result.program_trim = trim
    started = time.perf_counter()
    total_weight = sum(job["weight"] for job in todo) or 1.0
    behind = 0.0  # jo valmiiden tiedostojen paino

    try:
        return _run_todo(
            result, todo, jobs, settings, plugin, masks, program_start,
            progress, trim, started, total_weight,
        )
    finally:
        if hasattr(plugin, "close"):
            plugin.close()


def _run_todo(
    result, todo, jobs, settings, plugin, masks, program_start,
    progress, trim, started, total_weight,
):
    """Tiedostot yksi kerrallaan. Erillään, jotta liitännäisvaranto suljetaan
    myös silloin kun jokin kaatuu kesken."""
    behind = 0.0
    for index, job in enumerate(todo):
        _log(f"{index + 1}/{len(todo)} {job['name']}")
        # Kello nollataan tiedostoittain: vaiheen kesto on tämän tiedoston
        # vaiheen kesto, ei kulunut aika koko ajon alusta.
        stage_at = time.perf_counter()

        def stage(name: str, share: float, job=job, behind=behind) -> None:
            """Yhden vaiheen valmistuminen: lokiin ja edistymiseen."""
            nonlocal stage_at
            now = time.perf_counter()
            _log(f"    {name} {now - stage_at:.1f}s")
            stage_at = now
            fraction = (behind + job["weight"] * share) / total_weight
            if progress is not None:
                progress(
                    {
                        "done": index,
                        "total": len(todo),
                        "current": job["name"],
                        "stage": name,
                        "fraction": round(fraction, 4),
                        "eta": _eta(started, fraction),
                    }
                )

        if progress is not None:
            progress(
                {
                    "done": index,
                    "total": len(todo),
                    "current": job["name"],
                    "stage": "read",
                    "fraction": round(behind / total_weight, 4),
                    "eta": _eta(started, behind / total_weight),
                }
            )
        try:
            result.gains[job["key"]] = _run_one(
                job, settings, plugin, masks, program_start, stage, trim
            )
        except (MixError, ChainError, OSError, RuntimeError, ValueError) as exc:
            result.errors[job["key"]] = str(exc)
            _log(f"    VIRHE: {exc}")
            behind += job["weight"]
            continue
        _log(f"    valmis {result.gains[job['key']]:+.1f} dB")
        behind += job["weight"]
        result.processed += 1
        _record(result, job)
    _log(f"valmis {time.perf_counter() - started:.0f}s")
    if progress is not None:
        progress(
            {
                "done": len(todo),
                "total": len(todo),
                "current": "",
                "stage": "",
                "fraction": 1.0,
                "eta": 0.0,
            }
        )
    return result


def _log(message: str) -> None:
    """Käsittelyn kulku terminaaliin.

    Käsittely on minuutteja pitkä ja tapahtuu taustasäikeessä, jossa mikään
    ei näy. Kun se on hidas tai kaatuu, kysymys on aina sama: minkä tiedoston
    kohdalla ja missä vaiheessa. Suomeksi kuten muukin koodi — tämä on
    ylläpitäjän loki, ei käyttäjälle näkyvä teksti.
    """
    print(f"[ääni] {message}", flush=True)


def _eta(started: float, fraction: float) -> float:
    """Arvio jäljellä olevasta ajasta sekunteina.

    Osuus painotetaan tiedostokoolla ja vaiheella, joten arvio on olemassa
    jo ensimmäisen vaiheen jälkeen eikä vasta ensimmäisen tiedoston jälkeen —
    ja 20 minuutin tiedosto ei enää lupaa samaa kuin 64 minuutin.
    """
    if fraction <= 0.001:
        return 0.0
    return (time.perf_counter() - started) / fraction * (1.0 - fraction)


def _record(result: MixResult, job: dict) -> None:
    """Merkitsee valmiin tuloksen oikeaan koriin."""
    if job.get("speech", True):
        result.replacements[job["key"]] = job["target"]
    else:
        result.room.append((job["key"], job["target"]))
