"""Ruutujen mittaus: hidas kerros, välimuistitettuna levylle.

Purkaa avainruudut ja ajaa tunnistimen jokaiselle. Tulos on taulukko, jossa
on aikaleima ja tunnistimen kentät — **mittaukset, ei pisteitä**. Se on koko
kerroksen idea: painojen säätäminen ei saa maksaa uutta purkua, ja
pisteytystä odotetaan muutettavan usein.

**Vain avainruudut.** ``-skip_frame nokey`` jättää väliruudut purkamatta
kokonaan: mitattuna 70x reaaliaika, eli noin yksi ruutu sekunnissa kameran
tavallisella avainruutuvälillä. Täysi purku on 16x. Koko tiedosto luetaan
yhtenä vetona ja rajataan vasta jälkikäteen — hyppiminen ikkunasta toiseen
olisi hitaampaa, koska haku purkaa joka tapauksessa edellisestä
avainruudusta eteenpäin.

**Aikaleima ja ruutu pariutetaan järjestyksellä**, joten eri pituudet
tarkoittaisivat että jokainen mittaus on väärässä kohdassa aikajanaa. Se ei
näkyisi mistään, joten se on virhe eikä varoitus.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ..audio.binaries import get_binary_path
from . import detect

# Purkuleveys. Kasvot ovat lähikuvassa noin viidesosa kuvan korkeudesta, eli
# tällä noin 110 px — Vision haluaa vähintään nelisenkymmentä.
WIDTH = 960
CACHE_VERSION = 1
TIMEOUT = 3600.0


class MeasureError(Exception):
    """Ruutuja ei saatu mitattua."""


def cache_dir() -> Path:
    """Mittausten välimuisti. Turvallista tyhjentää: maksaa yhden purun."""
    root = Path.home() / "Library" / "Caches" / "autoraffkat" / "video"
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_key(path: str, detector: detect.Detector) -> str:
    """Polku, koko, muokkausaika, purkuleveys — ja tunnistimen nimi ja versio.

    Tunnistin on avaimessa siksi, että sen vaihtaminen tuottaa eri sarakkeet
    eri merkityksillä. Ilman sitä uusi tunnistin lukisi vanhan jäljet ja
    tulos olisi kelvollinen, hyväksytty ja väärä.
    """
    stat = os.stat(path)
    raw = (f"{os.path.abspath(path)}|{stat.st_size}|{stat.st_mtime_ns}"
           f"|{WIDTH}|{CACHE_VERSION}|{detector.name}|{detector.version}")
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def duration(path: str) -> float:
    """Tiedoston kesto sekunteina, tai 0. Otsikosta, ei pakettivirrasta.

    Erotus on koko juttu: ``keyframe_times`` lukee jokaisen paketin läpi,
    tämä lukee yhden kentän. Kevyt otos tarvitsee vain tietää mistä välistä
    poimia.
    """
    probe = get_binary_path("ffprobe")
    done = subprocess.run(
        [probe, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=TIMEOUT)
    try:
        return float(done.stdout.strip().rstrip(","))
    except ValueError:
        return 0.0


def keyframe_times(path: str) -> list[float]:
    """Avainruutujen aikaleimat sekunteina."""
    probe = get_binary_path("ffprobe")
    done = subprocess.run(
        [probe, "-v", "error", "-skip_frame", "nokey", "-select_streams", "v",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=TIMEOUT)
    times = []
    for token in done.stdout.split():
        # csv=p=0 jättää silti pilkun perään, ja aikaleimattomasta ruudusta
        # tulee «N/A». Kumpikin siivotaan tässä, koska yksi väärin luettu
        # aikaleima siirtäisi kaikki mittaukset väärään kohtaan.
        try:
            times.append(float(token.strip().rstrip(",")))
        except ValueError:
            continue
    return times


def _extract(path: str, into: Path) -> list[Path]:
    """Purkaa avainruudut JPEGeiksi järjestyksessä."""
    ffmpeg = get_binary_path("ffmpeg")
    done = subprocess.run(
        [ffmpeg, "-v", "error", "-skip_frame", "nokey", "-i", path,
         # `-fps_mode passthrough`, ei `-vsync 0`: uusi ffmpeg ei tunne
         # jälkimmäistä lainkaan, ja ilman kumpaakaan avainruudut
         # venytettäisiin takaisin täyteen ruutunopeuteen — sama kuva
         # kymmeninä kopioina, ja aikaleimat ristiin ruutujen kanssa.
         "-fps_mode", "passthrough", "-vf", f"scale={WIDTH}:-2", "-q:v", "4",
         str(into / "%06d.jpg")],
        capture_output=True, text=True, timeout=TIMEOUT)
    frames = sorted(into.glob("*.jpg"))
    if done.returncode != 0 or not frames:
        tail = (done.stderr or "").strip().splitlines()
        raise MeasureError(
            f"{os.path.basename(path)}: purku epäonnistui"
            + (f" — {tail[-1]}" if tail else ""))
    return frames


# Kuinka monta ruutua istumajärjestykseen riittää.
#
# Kysymys on eri kuin reaktiokuvilla: siellä etsitään **hetkiä** ja jokainen
# avainruutu on ehdokas, tässä päätetään yksi merkki puhujaa kohti. Mitattuna
# oikealla jaksolla viisi satunnaista ruutua riitti 400/400 kertaa — luokat
# ovat kaukana toisistaan (+0,46 ja -0,28), joten mediaani asettuu heti.
# Kaksikymmentäneljä on siis reilusti yli tarpeen, ja se on tarkoitus: osasta
# ruuduista ei löydy kasvoja lainkaan, eikä otos saa kutistua sattumalta
# olemattomiin.
SEATING_FRAMES = 24


def sample_file(path: str, detector: detect.Detector,
                count: int = SEATING_FRAMES) -> dict:
    """Mittaa muutaman tasavälein poimitun ruudun. Ei välimuistia.

    Kevyt sukulainen ``measure_file``ille: sen työ on purkaa **kaikki**
    avainruudut, mikä on minuutteja ja koko reaktiokerroksen hinta. Paikan
    päättämiseen riittää kourallinen, ja silloin ominaisuus ei enää riipu
    siitä että joku on jaksanut painaa mittausnappia.

    Ruudut haetaan syötteen haulla (``-ss`` ennen ``-i``), joka hyppää
    lähimpään avainruutuun purkamatta väliin jäävää — se on koko idea.
    """
    # **Ei** ``keyframe_times``: se lukee koko tiedoston läpi ffprobella, ja
    # se on tässä koko hinta — 20 minuutin tiedostolla enemmän kuin ruutujen
    # poiminta. Aikaleimoja ei tarvita: paikkaa ei sidota hetkeen, ja
    # ``-ss`` hakee lähimmän avainruudun itse. Kestoon riittää formaatin
    # oma luku, joka tulee otsikosta eikä pakettivirrasta.
    span = duration(path)
    if not span:
        raise MeasureError(f"{os.path.basename(path)}: kestoa ei saatu")
    # Reunat pois: ensimmäisessä sekunnissa kamera voi vielä tarkentaa ja
    # viimeisessä istua jo väärin, eikä kumpikaan kerro istumajärjestyksestä.
    times = list(np.linspace(span * 0.05, span * 0.95, max(1, count)))
    picks = range(len(times))
    ffmpeg = get_binary_path("ffmpeg")
    work = Path(tempfile.mkdtemp(prefix="autoraffkat-seat-"))
    try:
        columns = {name: np.zeros(len(times), dtype=np.float32)
                   for name in detector.fields}
        found = np.zeros(len(times), dtype=bool)
        for index, pick in enumerate(picks):
            frame = work / f"{index:03d}.jpg"
            done = subprocess.run(
                [ffmpeg, "-v", "error", "-ss", f"{times[pick]:.3f}",
                 "-i", path, "-frames:v", "1",
                 "-vf", f"scale={WIDTH}:-2", "-q:v", "4", str(frame)],
                capture_output=True, text=True, timeout=TIMEOUT)
            if done.returncode != 0 or not frame.exists():
                continue          # yksi ruutu vähemmän ei kaada otosta
            row = detector.measure(str(frame))
            if row is None:
                continue
            found[index] = True
            for name in detector.fields:
                columns[name][index] = float(row.get(name, 0.0))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"times": np.asarray(times, dtype=np.float32),
            "found": found, **columns}


def measure_file(path: str, detector: detect.Detector, progress=None) -> dict:
    """Mittaa yhden tiedoston avainruudut. Palauttaa taulukon sanakirjana.

    Avaimet: ``times`` ja yksi taulukko per tunnistimen kenttä, sekä
    ``found`` joka kertoo mistä ruuduista kasvot löytyivät. Ruudut joista ei
    löytynyt ovat mukana nollina — poistaminen siirtäisi indeksit eikä
    aikaleimoja voisi enää pariuttaa.
    """
    times = keyframe_times(path)
    if not times:
        raise MeasureError(f"{os.path.basename(path)}: ei avainruutuja")

    work = Path(tempfile.mkdtemp(prefix="autoraffkat-video-"))
    try:
        frames = _extract(path, work)
        if len(frames) != len(times):
            raise MeasureError(
                f"{os.path.basename(path)}: {len(frames)} ruutua mutta "
                f"{len(times)} aikaleimaa")
        columns = {name: np.zeros(len(frames), dtype=np.float32)
                   for name in detector.fields}
        found = np.zeros(len(frames), dtype=bool)
        for index, frame in enumerate(frames):
            row = detector.measure(str(frame))
            if row is None:
                continue          # ei kasvoja on tulos, ei virhe
            found[index] = True
            for name in detector.fields:
                columns[name][index] = float(row.get(name, 0.0))
            if progress is not None and index % 200 == 0:
                progress(index / len(frames))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return {"times": np.asarray(times, dtype=np.float32), "found": found,
            **columns}


def table(path: str, detector: detect.Detector, progress=None) -> dict:
    """Mittaukset välimuistista, tai lasketaan ja talletetaan.

    Kirjoitus tehdään auki olevaan tiedostokahvaan ja nimetään vasta sitten:
    ``np.savez`` lisää ``.npz``:n *polkuun* joka on sitä vailla, joten
    väliaikaisnimeen tallennus loisi väärän tiedoston ja uudelleennimeäminen
    epäonnistuisi hiljaa. Sama ansa kuin verhokäyrällä, ks. audio/envelope.py.
    """
    target = cache_dir() / f"{cache_key(path, detector)}.npz"
    if target.exists():
        try:
            with np.load(target) as data:
                return {name: data[name] for name in data.files}
        except (OSError, ValueError):
            target.unlink(missing_ok=True)   # rikkinäinen välimuisti lasketaan uusiksi

    result = measure_file(path, detector, progress)
    tmp = target.with_suffix(".tmp")
    try:
        with open(tmp, "wb") as handle:
            np.savez(handle, **result)
        tmp.replace(target)
    except OSError:
        tmp.unlink(missing_ok=True)
    return result


def is_cached(path: str, detector: detect.Detector) -> bool:
    """Onko tiedosto jo mitattu — halpa tarkistus käyttöliittymälle."""
    try:
        return (cache_dir() / f"{cache_key(path, detector)}.npz").exists()
    except OSError:
        return False
