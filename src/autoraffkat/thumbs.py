"""Pikkukuvat kameratiedostoista.

Kulmien nimet ovat monikamerassa ``1``, ``2`` ja ``3``, eivätkä
tiedostonimetkään kerro kumpaa puhujaa mikäkin kamera kuvaa. Yksi ruutu
kertoo sen heti, ja se on ainoa tapa jolla roolituksen voi tehdä katsomatta
erikseen Final Cutista.

Ruutu otetaan **puolivälistä tiedostoa**. Alku on usein asettelua ja loppu
pakkaamista; puoliväli on käytännössä aina se mitä kamera oikeasti kuvaa.

Purku on hidas, joten se tehdään vasta pyydettäessä ja tulos jää levylle.
Välimuisti on käyttäjän Caches-hakemistossa, ei median vieressä: pikkukuva on
johdettua eikä kuulu kuvaushakemistoon.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .audio.binaries import get_binary_path

WIDTH = 320
TIMEOUT = 60


def cache_dir() -> Path:
    """Pikkukuvien välimuisti. Turvallista tyhjentää milloin tahansa."""
    root = Path.home() / "Library" / "Caches" / "autoraffkat" / "thumbs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_path(path: str, seconds: float) -> Path:
    """Välimuistipolku: tiedosto, koko, muokkausaika ja kohta."""
    stat = os.stat(path)
    stem = Path(path).stem
    return cache_dir() / f"{stem}-{stat.st_size}-{int(stat.st_mtime)}-{int(seconds)}.jpg"


def thumbnail(path: str, duration: float) -> str | None:
    """Ruutu puolivälistä. Palauttaa polun tai ``None``.

    Epäonnistuminen ei ole virhe: pikkukuva on mukavuus, eikä sen puuttuminen
    saa estää roolitusta.
    """
    if not path or not os.path.exists(path) or duration <= 0:
        return None
    at = duration / 2.0
    target = cache_path(path, at)
    if target.exists():
        return str(target)
    tmp = target.with_suffix(".tmp.jpg")
    try:
        ffmpeg_bin = get_binary_path("ffmpeg")
        done = subprocess.run(
            # -ss ennen -i:tä hakee avainkuvaan asti purkamatta, joten tunnin
            # tiedostosta ruudun saa sekunnissa eikä minuutissa.
            [ffmpeg_bin, "-y", "-v", "error", "-ss", f"{at:.3f}", "-i", path,
             "-frames:v", "1", "-vf", f"scale={WIDTH}:-2", "-q:v", "5",
             str(tmp)],
            capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if done.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return None
    tmp.replace(target)
    return str(target)


def for_item(item) -> str | None:
    """Pikkukuva medialle, jos siinä on kuvaa."""
    if not item.has_video:
        return None
    return thumbnail(item.path, float(item.asset_duration))
