"""Mediatiedostojen tekniset tiedot ffprobella.

Raidan nimi ei kerro kaikkea mitä roolituksessa tarvitsee tietää: onko tämä
se 4K-kamera vai puhelimella otettu varakuva, onko mikki 24- vai 16-bittinen,
mahtuuko koko juttu levylle. Nämä luetaan tiedostosta, koska XML ei niitä
kerro — se tuntee vain keston ja formaatin id:n.

Tulos välimuistitetaan prosessin muistiin tiedoston koon ja muokkausajan
mukaan. Tilarakenne haetaan käyttöliittymästä sekunnin välein, eikä joka
haulla saa käynnistää kymmentä ffprobea.
"""

from __future__ import annotations

import json
import os
import subprocess

TIMEOUT = 30

_cache: dict[tuple, dict] = {}


def _run(path: str) -> dict:
    """ffproben raakatulos, tai tyhjä."""
    try:
        done = subprocess.run(
            ["ffprobe", "-v", "error", "-show_format", "-show_streams",
             "-of", "json", path],
            capture_output=True, text=True, timeout=TIMEOUT)
        return json.loads(done.stdout or "{}") if done.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fps(raw: str | None) -> float | None:
    """``"25/1"`` -> 25.0."""
    if not raw or "/" not in raw:
        return _number(raw)
    top, _, bottom = raw.partition("/")
    try:
        return float(top) / float(bottom) if float(bottom) else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def info(path: str) -> dict:
    """Tiedoston tekniset tiedot. Puuttuva tiedosto antaa tyhjän."""
    if not path or not os.path.exists(path):
        return {}
    try:
        stat = os.stat(path)
    except OSError:
        return {}
    key = (path, stat.st_size, int(stat.st_mtime))
    hit = _cache.get(key)
    if hit is not None:
        return hit

    raw = _run(path)
    streams = raw.get("streams") or []
    fmt = raw.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})

    out = {
        "size": stat.st_size,
        "duration": _number(fmt.get("duration")),
        "container": (fmt.get("format_name") or "").split(",")[0],
        "bitrate": _number(fmt.get("bit_rate")),
    }
    if video:
        out["video"] = {
            "codec": video.get("codec_name", ""),
            "width": video.get("width", 0),
            "height": video.get("height", 0),
            "fps": _fps(video.get("avg_frame_rate") or video.get("r_frame_rate")),
            "bitrate": _number(video.get("bit_rate")),
        }
    if audio:
        # Bittisyvyys on PCM:llä ``bits_per_raw_sample``, pakatulla ei mitään.
        depth = audio.get("bits_per_raw_sample") or audio.get("bits_per_sample")
        out["audio"] = {
            "codec": audio.get("codec_name", ""),
            "channels": audio.get("channels", 0),
            "rate": _number(audio.get("sample_rate")),
            "depth": _number(depth) or None,
            "bitrate": _number(audio.get("bit_rate")),
        }
    _cache[key] = out
    return out
