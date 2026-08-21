"""Verhokäyrä: hidas kerros.

ffmpeg purkaa raidan monoksi, RMS lasketaan 20 ms välein desibeleinä. Tämä
ajetaan kerran tiedostoa kohden ja välimuistitetaan levylle, koska se maksaa
sekunteja minuuttia kohden. Päätöskerros lukee vain valmiin taulukon.

Verhokäyrä indeksoidaan tiedoston alusta, ei aikajanasta, jotta sama välimuisti
kelpaa vaikka klippi siirtyisi aikajanalla.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

from ..model import HOP
from .binaries import get_binary_path, require_ffmpeg as _check_binaries

SAMPLE_RATE = 8000          # riittää puheen energialle, neljäsosa purkuajasta
CACHE_VERSION = 2
FLOOR_DB = -120.0


class EnvelopeError(Exception):
    """Verhokäyrää ei saatu."""


def cache_dir() -> Path:
    """Verhokäyrien välimuisti. Turvallista tyhjentää milloin tahansa."""
    root = Path.home() / "Library" / "Caches" / "autoraffkat" / "envelopes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def require_ffmpeg() -> None:
    """Varmistaa työkalut ennen purkua, jotta virhe on luettava eikä OSError."""
    try:
        _check_binaries()
    except FileNotFoundError as err:
        raise EnvelopeError(str(err)) from err


def _cache_key(path: str) -> str:
    """Välimuistiavain: polku, koko, muokkausaika ja laskennan parametrit.

    Koko ja muokkausaika mukana, jotta korvattu tiedosto ei osu vanhaan
    käyrään; ``CACHE_VERSION``, jotta laskennan muutos mitätöi vanhat.
    """
    st = os.stat(path)
    raw = f"{os.path.abspath(path)}|{st.st_size}|{st.st_mtime_ns}|{SAMPLE_RATE}|{HOP}|{CACHE_VERSION}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def probe_audio(path: str) -> bool:
    """Onko tiedostossa ääniraitaa."""
    try:
        ffprobe_bin = get_binary_path("ffprobe")
        out = subprocess.run(
            [ffprobe_bin, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


def _decode_rms(path: str, progress=None) -> np.ndarray:
    """Purkaa äänen virtana ja palauttaa RMS-desibelit HOP-välein."""
    win = max(1, int(round(SAMPLE_RATE * HOP)))
    chunk_frames = 4096                      # 4096 * 20 ms ≈ 82 s kerrallaan
    chunk_bytes = win * chunk_frames * 4

    ffmpeg_bin = get_binary_path("ffmpeg")
    cmd = [ffmpeg_bin, "-v", "error", "-nostdin", "-i", path,
           "-map", "0:a:0", "-ac", "1", "-ar", str(SAMPLE_RATE),
           "-f", "f32le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    blocks: list[np.ndarray] = []
    leftover = b""
    frames_done = 0
    try:
        while True:
            data = proc.stdout.read(chunk_bytes)
            if not data:
                break
            data = leftover + data
            usable = (len(data) // (win * 4)) * (win * 4)
            leftover = data[usable:]
            if usable == 0:
                continue
            samples = np.frombuffer(data[:usable], dtype="<f4")
            frames = samples.reshape(-1, win)
            mean_sq = np.mean(np.square(frames, dtype=np.float64), axis=1)
            blocks.append(mean_sq.astype(np.float32))
            frames_done += frames.shape[0]
            if progress is not None:
                progress(frames_done * HOP)
    finally:
        if proc.stdout:
            proc.stdout.close()
        stderr = proc.stderr.read() if proc.stderr else b""
        if proc.stderr:
            proc.stderr.close()
        proc.wait()

    if proc.returncode not in (0, None):
        raise EnvelopeError(
            f"Äänen purku epäonnistui: {os.path.basename(path)}\n"
            + stderr.decode(errors="replace").strip()
        )
    if not blocks:
        return np.zeros(0, dtype=np.float32)

    mean_sq = np.concatenate(blocks)
    db = 10.0 * np.log10(np.maximum(mean_sq, 1e-12))
    return np.maximum(db, FLOOR_DB).astype(np.float32)


def envelope_for(path: str, progress=None, use_cache: bool = True) -> np.ndarray:
    """RMS-verhokäyrä desibeleinä, yksi arvo per HOP tiedoston alusta."""
    if not path or not os.path.exists(path):
        raise EnvelopeError(f"Tiedostoa ei löydy: {path or '(polku puuttuu)'}")
    require_ffmpeg()

    cache_path = cache_dir() / f"{_cache_key(path)}.npy"
    if use_cache and cache_path.exists():
        try:
            return np.load(cache_path)
        except (OSError, ValueError):
            cache_path.unlink(missing_ok=True)

    db = _decode_rms(path, progress)
    if use_cache:
        tmp = cache_path.with_suffix(".npy.tmp")
        try:
            np.save(tmp, db)
            tmp.replace(cache_path)
        except OSError:
            tmp.unlink(missing_ok=True)
    return db
