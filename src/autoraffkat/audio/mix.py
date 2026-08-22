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
    """
    if not os.path.exists(target) or not os.path.exists(source):
        return False
    return os.path.getmtime(target) >= os.path.getmtime(source)


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
                    }
                )
    return jobs


def _run_one(
    job: dict,
    settings: AudioSettings,
    plugin,
    masks: dict | None = None,
    program_start: float = 0.0,
) -> float:
    """Käsittelee yhden tiedoston. Palauttaa normalisoinnin noston."""
    from pedalboard.io import AudioFile

    source = ensure_readable(job["source"])
    with AudioFile(source) as handle:
        audio = handle.read(handle.frames)
        rate = handle.samplerate
    if audio.shape[1] == 0:
        raise MixError(t("audio.empty_file", name=os.path.basename(job["source"])))
    if job.get("mono") and audio.shape[0] > 1:
        audio = audio.mean(axis=0, keepdims=True)

    audio, info = chain.process(
        audio,
        rate,
        settings,
        job.get("gain_db", 0.0),
        job.get("speech", True),
        job.get("target_lufs"),
        plugin,
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
    return info.gain_db


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
) -> MixResult:
    """Käsittelee mikit ja tilaäänen. Hidas — ei kuulu säätösilmukkaan.

    Liitännäinen ladataan kerran ja sen tila nollataan tiedostojen välissä:
    lataus maksaa, mutta edellisen tiedoston häntä ei saa vuotaa seuraavaan.
    """
    result = MixResult()
    if not settings.enabled:
        return result

    jobs = _jobs(timeline, roles, settings)
    if not jobs:
        return result

    todo = []
    for job in jobs:
        if not os.path.exists(job["source"]):
            result.errors[job["key"]] = t("audio.source_missing", path=job["source"])
        elif is_current(job["source"], job["target"]):
            result.skipped += 1
            _record(result, job)
        else:
            todo.append(job)
    if not todo:
        return result

    try:
        plugin = chain.load_plugin(settings.plugin_path)
    except ChainError as exc:
        result.errors["plugin"] = str(exc)
        return result

    masks = duck_masks(grid, settings)
    started = time.perf_counter()
    for index, job in enumerate(todo):
        if progress is not None:
            progress(index, len(todo), job["name"], _eta(started, index, len(todo)))
        try:
            result.gains[job["key"]] = _run_one(
                job, settings, plugin, masks, program_start
            )
        except (MixError, ChainError, OSError, RuntimeError, ValueError) as exc:
            result.errors[job["key"]] = str(exc)
            continue
        result.processed += 1
        _record(result, job)
    if progress is not None:
        progress(len(todo), len(todo), "", 0.0)
    return result


def _eta(started: float, done: int, total: int) -> float:
    """Arvio jäljellä olevasta ajasta sekunteina.

    Liitännäinen voi olla hidas — dxRevive kulkee noin seitsemän kertaa
    reaaliaikaa — joten pelkkä «2/4» ei kerro riittävästi.
    """
    if done <= 0:
        return 0.0
    return (time.perf_counter() - started) / done * (total - done)


def _record(result: MixResult, job: dict) -> None:
    """Merkitsee valmiin tuloksen oikeaan koriin."""
    if job.get("speech", True):
        result.replacements[job["key"]] = job["target"]
    else:
        result.room.append((job["key"], job["target"]))
