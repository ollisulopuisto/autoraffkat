"""Äänenkäsittely automixerin kanavanauhalla.

Käsittely on **valinnainen**. Ilman automixeria kaikki muu toimii kuten
ennenkin, ja vienti viittaa alkuperäisiin tiedostoihin.

Yhteys automixeriin on prosessiraja, ei import (``_mix_worker.py`` ajetaan
``uv run --project``illa sen omassa ympäristössä). Syy on käytännöllinen:
automixer vaatii Python 3.13:n ja MLX:n ja asentuu nimellä ``src.automixer``,
eikä leikkaustyökalu saa periä mitään noista kolmesta. Rajapinta on kapea —
"anna näistä tiedostoista käsitellyt, saman pituiset kopiot" — joten
prosessiraja ei maksa mitään.

Kaksi sääntöä, joista kumpikaan ei ole neuvoteltavissa:

**Alkuperäiseen tiedostoon ei kosketa.** Käsitelty ääni menee rinnakkaiseen
``nimi [mix].wav``:iin. Päälle kirjoittaminen rikkoisi kaksi asiaa kerralla:
verhokäyrän välimuisti avainnetaan muokkausajalla, joten se laskettaisiin
uudestaan, ja uusi laskenta osuisi käsiteltyyn ääneen.

**Näytemäärä ei saa muuttua.** Vienti viittaa käsiteltyyn tiedostoon samoilla
ajoilla kuin alkuperäiseen, joten yksikin lisätty tai pudotettu näyte siirtää
kuvan ja äänen erilleen. Tämä tarkistetaan kahdesti: työprosessissa
näytetaulukoista, ja täällä uudestaan ffprobella — käsittely tapahtuu vieraassa
ympäristössä, eikä sen lupauksiin nojata.

Analyysi ajetaan aina raa'asta äänestä. Kompressori nostaa pohjakohinaa
sanojen välissä ja tasoittaa mikkien keskinäisen eron — herkkyys on kynnys
pohjan yli ja päällekkäispuheen sääntö vertaa mikkejä toisiinsa, joten
käsitellystä äänestä laskettu päätös olisi huonompi.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..model import AudioSettings

# Formaatit joita soundfile lukee suoraan. Muut puretaan ensin ffmpegillä:
# kameran ääni on mp4:n sisällä eikä libsndfile avaa sitä.
SOUNDFILE_FORMATS = {".wav", ".wave", ".aif", ".aiff", ".aifc", ".flac",
                     ".w64", ".caf", ".ogg", ".opus"}

MIX_SUFFIX = " [mix]"
ROOM_SUFFIX = " [room]"
ROOM_ROLE = "effects.Tilaääni"

WORKER = Path(__file__).parent / "_mix_worker.py"
ENV_VAR = "AUTORAFFKAT_AUTOMIXER"
TIMEOUT = 3600


class MixError(Exception):
    """Ääntä ei voitu käsitellä."""


def automixer_path() -> str:
    """automixerin hakemisto, tai ``""``.

    Ympäristömuuttuja voittaa, muuten katsotaan repon naapurista. Kauempaa ei
    etsitä: väärä automixer olisi pahempi kuin ei automixeria.
    """
    named = os.environ.get(ENV_VAR, "").strip()
    if named:
        return named if _is_automixer(named) else ""
    here = Path(__file__).resolve().parents[3]          # repon juuri
    sibling = here.parent / "automixer"
    return str(sibling) if _is_automixer(str(sibling)) else ""


def _is_automixer(path: str) -> bool:
    """Onko hakemisto automixer-projekti."""
    config = Path(path) / "pyproject.toml"
    if not config.exists():
        return False
    try:
        return 'name = "automixer"' in config.read_text(encoding="utf-8")
    except OSError:
        return False


def available() -> bool:
    """Voidaanko ääntä käsitellä: automixer löytyy ja uv on polulla."""
    return bool(automixer_path()) and shutil.which("uv") is not None


def sibling(path: str, suffix: str) -> str:
    """``x.wav`` -> ``x [mix].wav``. Aina WAV, myös mp3-lähteestä."""
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
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=duration_ts,nb_samples,sample_rate,duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=60)
        streams = json.loads(done.stdout or "{}").get("streams") or []
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
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


@dataclass
class MixResult:
    """Käsittelyn tulos vientiä varten."""

    # media key -> käsitelty tiedosto. Vienti viittaa näihin alkuperäisten
    # sijaan; ajat pysyvät samoina, koska näytemäärä on sama.
    replacements: dict[str, str] = field(default_factory=dict)
    # (media key, käsitelty tiedosto) tilaäänelle, omalle lanelleen.
    room: list[tuple[str, str]] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    processed: int = 0
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def extract_dir() -> Path:
    """Puretun äänen välimuisti. Turvallista tyhjentää milloin tahansa."""
    root = Path.home() / "Library" / "Caches" / "autoraffkat" / "extracted"
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_readable(path: str) -> str:
    """Palauttaa polun, jonka soundfile osaa lukea.

    Kameran ääni on mp4:n sisällä, joten se puretaan WAViksi välimuistiin.
    Purku ei kirjoita median viereen: se on väliaikaista eikä kuulu käyttäjän
    hakemistoon.

    Purettu ääni ei ole näytteelleen taattu: AAC:n purku voi poiketa säiliön
    ilmoittamasta pituudesta. Ero tarkistetaan kutsujassa, ja poikkeava
    hylätään ennemmin kuin käytetään väärässä kohdassa.
    """
    suffix = os.path.splitext(path)[1].lower()
    if suffix in SOUNDFILE_FORMATS:
        return path
    stat = os.stat(path)
    name = f"{Path(path).stem}-{stat.st_size}-{int(stat.st_mtime)}.wav"
    target = extract_dir() / name
    if target.exists():
        return str(target)
    tmp = target.with_suffix(".tmp.wav")
    try:
        done = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", path, "-vn", "-map", "a:0",
             "-c:a", "pcm_f32le", str(tmp)],
            capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MixError(f"Äänen purku epäonnistui: {exc}") from exc
    if done.returncode != 0 or not tmp.exists():
        raise MixError(
            f"Äänen purku epäonnistui: {os.path.basename(path)} — "
            + (done.stderr or "").strip().splitlines()[-1:][0] if done.stderr
            else f"Äänen purku epäonnistui: {os.path.basename(path)}")
    tmp.replace(target)
    return str(target)


def _jobs(timeline, roles, settings: AudioSettings) -> list[dict]:
    """Käsiteltävät tiedostot: mikit, ja tilaääni jos sellainen on valittu."""
    jobs: list[dict] = []
    for keys in roles.mics.values():
        for track_key in keys:
            for item in timeline.track_media(track_key):
                if item.path:
                    jobs.append({"key": item.key, "source": item.path,
                                 "target": sibling(item.path, MIX_SUFFIX),
                                 "target_lufs": settings.target_lufs,
                                 "gain_db": settings.gain_db, "speech": True})
    if settings.room_track:
        for item in timeline.track_media(settings.room_track):
            if item.path and item.has_audio:
                # Tilaääni normalisoidaan samaan tavoitteeseen mutta
                # asetetun verran hiljemmalle, jotta taso on ennustettava
                # eikä riipu siitä miten kuuma kameran mikki sattui olemaan.
                jobs.append({"key": item.key, "source": item.path,
                             "target": sibling(item.path, ROOM_SUFFIX),
                             "target_lufs": settings.target_lufs + settings.room_db,
                             "gain_db": 0.0, "speech": False})
    return jobs


def _run_worker(jobs: list[dict], settings: AudioSettings) -> dict:
    """Ajaa työprosessin automixerin ympäristössä."""
    project = automixer_path()
    if not project:
        raise MixError(
            "automixeria ei löydy. Aseta polku ympäristömuuttujaan "
            f"{ENV_VAR} tai sijoita se autoraffkatin naapuriksi.")
    if shutil.which("uv") is None:
        raise MixError("uv puuttuu polulta, eikä automixeria voi ajaa.")
    spec = json.dumps({"project": project, "jobs": jobs,
                       "settings": settings.to_json()})
    # Ajetaan automixerin juuresta: se asentuu nimellä ``src.automixer``, joten
    # tuonti onnistuu vain sieltä. Tiedostopolut ovat absoluuttisia, joten
    # työhakemistolla ei ole muuta merkitystä.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    try:
        done = subprocess.run(
            ["uv", "run", "--project", project, "python", str(WORKER)],
            input=spec, capture_output=True, text=True, timeout=TIMEOUT,
            cwd=project, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MixError(f"automixerin ajo epäonnistui: {exc}") from exc
    if done.returncode != 0:
        tail = (done.stderr or "").strip().splitlines()
        raise MixError("automixer palautti virheen: "
                       + (tail[-1] if tail else f"paluuarvo {done.returncode}"))
    try:
        return json.loads(done.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise MixError("automixerin vastausta ei voitu lukea.") from exc


def process(timeline, roles, settings: AudioSettings, progress=None) -> MixResult:
    """Käsittelee mikit ja tilaäänen. Hidas — ei kuulu säätösilmukkaan.

    ``roles`` kertoo mitkä raidat ovat mikkejä. Kamerat jätetään rauhaan,
    paitsi se joka on valittu tilaääneksi.
    """
    result = MixResult()
    if not settings.enabled:
        return result

    jobs = _jobs(timeline, roles, settings)
    if not jobs:
        return result

    by_key = {job["key"]: job for job in jobs}
    todo = []
    for job in jobs:
        if not os.path.exists(job["source"]):
            result.errors[job["key"]] = f"Lähdetiedostoa ei löydy: {job['source']}"
            continue
        if is_current(job["source"], job["target"]):
            result.skipped += 1
            _record(result, job)
            continue
        try:
            # Työprosessi lukee soundfilella, joka ei avaa mp4:ää.
            job["source"] = ensure_readable(job["source"])
        except (MixError, OSError) as exc:
            result.errors[job["key"]] = str(exc)
            continue
        todo.append(job)

    if todo:
        if progress is not None:
            progress(0, len(todo), os.path.basename(todo[0]["source"]))
        try:
            answer = _run_worker(todo, settings)
        except MixError as exc:
            result.errors["automixer"] = str(exc)
            return result
        result.errors.update(answer.get("errors") or {})
        for entry in answer.get("done") or []:
            job = by_key.get(entry.get("key", ""))
            if job is None:
                continue
            # Toinen tarkistus omin silmin: työprosessi on vieraassa
            # ympäristössä, eikä sen lupaus pituudesta riitä.
            source_frames = frame_count(job["source"])
            target_frames = frame_count(job["target"])
            if (source_frames is not None and target_frames is not None
                    and source_frames != target_frames):
                result.errors[job["key"]] = (
                    f"Käsitelty ääni on eri pituinen ({source_frames} → "
                    f"{target_frames} näytettä): {os.path.basename(job['source'])}. "
                    "Kuva ja ääni erkanisivat, joten sitä ei käytetä.")
                continue
            result.processed += 1
            _record(result, job)
        if progress is not None:
            progress(len(todo), len(todo), "")
    return result


def _record(result: MixResult, job: dict) -> None:
    """Merkitsee valmiin tuloksen oikeaan koriin."""
    if job.get("speech", True):
        result.replacements[job["key"]] = job["target"]
    else:
        result.room.append((job["key"], job["target"]))
