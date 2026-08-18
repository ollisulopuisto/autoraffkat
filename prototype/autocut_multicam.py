#!/usr/bin/env python3
"""
autocut_multicam.py

Kaksi mikkiraitaa ja kolme kameraklippiä sisaan, FCPXML ulos.
Leikkaa laajasta lahikuvaan sen mukaan, kumpi puhuu. Ei renderoi mitaan.

Riippuvuudet: python3, numpy, ffmpeg + ffprobe polussa.

Esimerkki:
  python3 autocut_multicam.py \
      --wide WIDE.mov --cam-a CAM_A.mov --cam-b CAM_B.mov \
      --mic-a mic_a.wav --mic-b mic_b.wav \
      -o raakaleikkaus.fcpxml --report
"""

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

import numpy as np

HOP = 0.02  # analyysin aika-askel sekunteina


# ---------------------------------------------------------------- media

@dataclass
class Media:
    path: str
    role: str                 # wide / cam-a / cam-b / mic-a / mic-b
    offset: float = 0.0       # aikajanan hetki, jossa taman tiedoston t=0 on
    duration: float = 0.0
    has_video: bool = False
    has_audio: bool = False
    width: int = 0
    height: int = 0
    fps: Fraction = Fraction(25, 1)
    audio_rate: int = 48000
    audio_channels: int = 2
    res_id: str = ""

    @property
    def start(self) -> float:
        return self.offset

    @property
    def end(self) -> float:
        return self.offset + self.duration


def ffprobe(path: str) -> dict:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit("ffprobe epaonnistui: %s\n%s" % (path, out.stderr.strip()))
    return json.loads(out.stdout)


def probe(path: str, role: str, offset: float) -> Media:
    info = ffprobe(path)
    m = Media(path=os.path.abspath(path), role=role, offset=offset)
    m.duration = float(info.get("format", {}).get("duration", 0.0))
    for s in info.get("streams", []):
        if s["codec_type"] == "video" and not m.has_video:
            m.has_video = True
            m.width = int(s.get("width", 0))
            m.height = int(s.get("height", 0))
            rate = s.get("r_frame_rate") or s.get("avg_frame_rate") or "25/1"
            try:
                m.fps = Fraction(rate)
            except (ValueError, ZeroDivisionError):
                m.fps = Fraction(25, 1)
            if s.get("duration"):
                m.duration = max(m.duration, float(s["duration"]))
        elif s["codec_type"] == "audio" and not m.has_audio:
            m.has_audio = True
            m.audio_rate = int(s.get("sample_rate", 48000))
            m.audio_channels = int(s.get("channels", 2))
    if m.duration <= 0:
        sys.exit("Kestoa ei saatu selville: %s" % path)
    return m


def decode_mono(path: str, sr: int = 8000) -> np.ndarray:
    cmd = ["ffmpeg", "-v", "error", "-i", path,
           "-map", "0:a:0", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True)
    if out.returncode != 0:
        sys.exit("aanen purku epaonnistui: %s\n%s"
                 % (path, out.stderr.decode(errors="replace").strip()))
    return np.frombuffer(out.stdout, dtype="<f4").astype(np.float32)


# ---------------------------------------------------------------- analyysi

def envelope_db(samples: np.ndarray, sr: int) -> np.ndarray:
    """RMS-verhokayra desibeleina, yksi arvo per HOP."""
    win = max(1, int(round(sr * HOP)))
    n = len(samples) // win
    if n == 0:
        return np.full(1, -120.0, dtype=np.float32)
    trimmed = samples[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(np.square(trimmed), axis=1) + 1e-12)
    return (20.0 * np.log10(rms + 1e-9)).astype(np.float32)


def smooth(db: np.ndarray, seconds: float) -> np.ndarray:
    k = max(1, int(round(seconds / HOP)))
    if k <= 1:
        return db
    kernel = np.ones(k, dtype=np.float32) / k
    return np.convolve(db, kernel, mode="same")


def noise_floor(db: np.ndarray) -> float:
    return float(np.percentile(db, 20))


@dataclass
class Segment:
    angle: str
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def decide_angles(db_a: np.ndarray, db_b: np.ndarray, args) -> list:
    """Palauttaa listan Segmentteja ohjelma-ajassa (0 = ohjelman alku)."""
    n = min(len(db_a), len(db_b))
    db_a, db_b = db_a[:n], db_b[:n]

    thr_a = (noise_floor(db_a) + args.margin) if args.threshold is None else args.threshold
    thr_b = (noise_floor(db_b) + args.margin) if args.threshold is None else args.threshold

    a_on = db_a > thr_a
    b_on = db_b > thr_b
    lead_a = (db_a - db_b) > args.dominance
    lead_b = (db_b - db_a) > args.dominance

    want = np.empty(n, dtype=object)
    for i in range(n):
        if a_on[i] and b_on[i]:
            if lead_a[i]:
                want[i] = "cam-a"
            elif lead_b[i]:
                want[i] = "cam-b"
            else:
                want[i] = "wide"          # paallekkaispuhe
        elif a_on[i]:
            want[i] = "cam-a" if lead_a[i] else "wide"
        elif b_on[i]:
            want[i] = "cam-b" if lead_b[i] else "wide"
        else:
            want[i] = None                # hiljaisuus: pidetaan nykyinen

    confirm = max(1, int(round(args.confirm / HOP)))
    min_shot = args.min_shot
    lead = args.lead

    current = "wide"
    cuts = [(0.0, "wide")]
    first_cut_allowed = -min_shot  # ensimmainen leikkaus ei odota koko min-shotia
    i = 0
    while i < n:
        t = i * HOP
        since_cut = t - (cuts[-1][0] if len(cuts) > 1 else first_cut_allowed)

        if args.wide_every > 0 and current != "wide" and since_cut >= args.wide_every:
            cuts.append((t, "wide"))
            current = "wide"
            i += 1
            continue

        target = want[i]
        if target is not None and target != current and since_cut >= min_shot:
            window = want[i:i + confirm]
            if len(window) == confirm and all(w == target or w is None for w in window) \
               and any(w == target for w in window):
                floor_t = cuts[-1][0] + (min_shot if len(cuts) > 1 else 0.0)
                cut_at = max(floor_t, t - lead)
                cuts.append((cut_at, target))
                current = target
                i += confirm
                continue
        i += 1

    total = n * HOP
    segments = []
    for idx, (t, angle) in enumerate(cuts):
        end = cuts[idx + 1][0] if idx + 1 < len(cuts) else total
        if end - t <= 0:
            continue
        if segments and segments[-1].angle == angle:
            segments[-1].end = end
        else:
            segments.append(Segment(angle, t, end))
    return segments


# ---------------------------------------------------------------- fcpxml

def rational(seconds: float, fd: Fraction) -> str:
    """Kvantisoi kuviin ja muotoilee FCPXML-ajaksi."""
    frames = int(round(Fraction(seconds).limit_denominator(1000000) / fd))
    value = fd * frames
    if value.denominator == 1:
        return "%ds" % value.numerator
    return "%d/%ds" % (value.numerator, value.denominator)


def to_frames(seconds: float, fd: Fraction) -> int:
    return int(round(Fraction(seconds).limit_denominator(1000000) / fd))


def frames_str(frames: int, fd: Fraction) -> str:
    value = fd * frames
    if value.denominator == 1:
        return "%ds" % value.numerator
    return "%d/%ds" % (value.numerator, value.denominator)


def file_url(path: str) -> str:
    return "file://" + quote(path)


def format_name(width: int, height: int, fps: Fraction) -> str:
    fps_label = {
        Fraction(24000, 1001): "2398", Fraction(24, 1): "24",
        Fraction(25, 1): "25", Fraction(30000, 1001): "2997",
        Fraction(30, 1): "30", Fraction(50, 1): "50",
        Fraction(60000, 1001): "5994", Fraction(60, 1): "60",
    }.get(fps, "25")
    return "FFVideoFormat%dp%s" % (height, fps_label)


def build_fcpxml(media: dict, segments: list, fd: Fraction,
                 program_start: float, program_end: float, project_name: str) -> str:
    wide = media["wide"]
    res = []
    res.append(
        '    <format id="r1" name=%s frameDuration="%s" width="%d" height="%d" '
        'colorSpace="1-1-1 (Rec. 709)"/>'
        % (quoteattr(format_name(wide.width, wide.height, 1 / fd)),
           "%d/%ds" % (fd.numerator, fd.denominator), wide.width, wide.height)
    )

    rid = 2
    for key in ("wide", "cam-a", "cam-b", "mic-a", "mic-b"):
        m = media[key]
        m.res_id = "r%d" % rid
        rid += 1
        attrs = [
            'id="%s"' % m.res_id,
            "name=%s" % quoteattr(os.path.basename(m.path)),
            'start="0s"',
            'duration="%s"' % rational(m.duration, fd),
            'hasAudio="1"' if m.has_audio else "",
            'audioSources="1"' if m.has_audio else "",
            'audioChannels="%d"' % m.audio_channels if m.has_audio else "",
            'audioRate="%d"' % m.audio_rate if m.has_audio else "",
            'hasVideo="1" videoSources="1" format="r1"' if m.has_video else "",
        ]
        res.append("    <asset %s>" % " ".join(a for a in attrs if a))
        res.append('      <media-rep kind="original-media" src=%s/>'
                   % quoteattr(file_url(m.path)))
        res.append("    </asset>")

    program_frames = to_frames(program_end - program_start, fd)
    program_duration = program_end - program_start

    # kvantisoidaan leikkauskohdat kuviin, jotta spineen ei jaa aukkoja
    bounds = [to_frames(s.start, fd) for s in segments] + [program_frames]
    for i in range(1, len(bounds)):
        if bounds[i] <= bounds[i - 1]:
            bounds[i] = bounds[i - 1] + 1

    body = []
    for i, seg in enumerate(segments):
        m = media[seg.angle]
        seg_frames = bounds[i + 1] - bounds[i]
        src_start_frames = to_frames(program_start - m.offset, fd) + bounds[i]
        src_start = float(fd * src_start_frames)
        clip = (
            '        <asset-clip ref="%s" offset="%s" name=%s start="%s" duration="%s" '
            'format="r1" tcFormat="NDF" srcEnable="video"'
            % (m.res_id, frames_str(bounds[i], fd),
               quoteattr("%s %02d" % (seg.angle, i + 1)),
               frames_str(src_start_frames, fd), frames_str(seg_frames, fd))
        )
        if i == 0:
            body.append(clip + ">")
            for lane, key, role in ((-1, "mic-a", "dialogue.mic-a"),
                                    (-2, "mic-b", "dialogue.mic-b")):
                mic = media[key]
                mic_src = program_start - mic.offset
                body.append(
                    '          <asset-clip ref="%s" lane="%d" offset="%s" name=%s '
                    'start="%s" duration="%s" audioRole="%s"/>'
                    % (mic.res_id, lane, frames_str(src_start_frames, fd),
                       quoteattr(os.path.basename(mic.path)),
                       rational(mic_src, fd), frames_str(program_frames, fd), role)
                )
            body.append("        </asset-clip>")
        else:
            body.append(clip + "/>")

    xml = []
    xml.append('<?xml version="1.0" encoding="UTF-8"?>')
    xml.append('<!DOCTYPE fcpxml>')
    xml.append('<fcpxml version="1.10">')
    xml.append("  <resources>")
    xml.extend(res)
    xml.append("  </resources>")
    xml.append("  <library>")
    xml.append("    <event name=%s>" % quoteattr(project_name))
    xml.append("      <project name=%s>" % quoteattr(project_name))
    xml.append(
        '        <sequence format="r1" duration="%s" tcStart="0s" tcFormat="NDF" '
        'audioLayout="stereo" audioRate="48k">'
        % frames_str(program_frames, fd)
    )
    xml.append("        <spine>")
    xml.extend(body)
    xml.append("        </spine>")
    xml.append("        </sequence>")
    xml.append("      </project>")
    xml.append("    </event>")
    xml.append("  </library>")
    xml.append("</fcpxml>")
    return "\n".join(xml) + "\n"


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="Automaattinen raakaleikkaus kahdelle puhujalle.")
    p.add_argument("--wide", required=True, help="laaja kuva")
    p.add_argument("--cam-a", required=True, help="lahikuva puhujasta A")
    p.add_argument("--cam-b", required=True, help="lahikuva puhujasta B")
    p.add_argument("--mic-a", required=True, help="mikkiraita A")
    p.add_argument("--mic-b", required=True, help="mikkiraita B")
    p.add_argument("-o", "--output", default="raakaleikkaus.fcpxml")
    p.add_argument("--name", default="Raakaleikkaus", help="projektin nimi")

    p.add_argument("--offset-wide", type=float, default=0.0)
    p.add_argument("--offset-cam-a", type=float, default=0.0)
    p.add_argument("--offset-cam-b", type=float, default=0.0)
    p.add_argument("--offset-mic-a", type=float, default=0.0)
    p.add_argument("--offset-mic-b", type=float, default=0.0)

    p.add_argument("--fps", default=None, help="esim. 25, 30, 29.97; oletus laajasta kuvasta")
    p.add_argument("--min-shot", type=float, default=2.5, help="lyhin kuvan kesto sekunteina")
    p.add_argument("--lead", type=float, default=0.15, help="leikataan nain paljon ennen puheen alkua")
    p.add_argument("--confirm", type=float, default=0.4, help="kuinka kauan puheen on jatkuttava")
    p.add_argument("--margin", type=float, default=12.0, help="dB pohjakohinan ylle")
    p.add_argument("--dominance", type=float, default=5.0, help="dB ero, jolla vuoto erotetaan puheesta")
    p.add_argument("--threshold", type=float, default=None, help="kiintea kynnys dB, ohittaa marginaalin")
    p.add_argument("--wide-every", type=float, default=0.0, help="pakota laaja kuva nain usein, 0 = ei")
    p.add_argument("--report", action="store_true", help="tulosta leikkauslista")
    args = p.parse_args()

    media = {
        "wide": probe(args.wide, "wide", args.offset_wide),
        "cam-a": probe(args.cam_a, "cam-a", args.offset_cam_a),
        "cam-b": probe(args.cam_b, "cam-b", args.offset_cam_b),
        "mic-a": probe(args.mic_a, "mic-a", args.offset_mic_a),
        "mic-b": probe(args.mic_b, "mic-b", args.offset_mic_b),
    }

    if args.fps:
        fps = {"23.976": Fraction(24000, 1001),
               "29.97": Fraction(30000, 1001),
               "59.94": Fraction(60000, 1001)}.get(
            str(args.fps), Fraction(args.fps).limit_denominator(1000))
    else:
        fps = media["wide"].fps
    fd = 1 / fps

    program_start = max(m.start for m in media.values())
    program_end = min(m.end for m in media.values())
    if program_end - program_start <= 1.0:
        sys.exit("Klipeilla ei ole yhteista aikaa. Tarkista offsetit.")

    sr = 8000
    a = decode_mono(media["mic-a"].path, sr)
    b = decode_mono(media["mic-b"].path, sr)
    a = a[int((program_start - media["mic-a"].offset) * sr):
          int((program_end - media["mic-a"].offset) * sr)]
    b = b[int((program_start - media["mic-b"].offset) * sr):
          int((program_end - media["mic-b"].offset) * sr)]

    db_a = smooth(envelope_db(a, sr), 0.10)
    db_b = smooth(envelope_db(b, sr), 0.10)

    segments = decide_angles(db_a, db_b, args)

    xml = build_fcpxml(media, segments, fd, program_start, program_end, args.name)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(xml)

    counts = {}
    for s in segments:
        counts[s.angle] = counts.get(s.angle, 0) + 1
    print("%s: %d leikkausta, %.1f min"
          % (args.output, len(segments), (program_end - program_start) / 60.0))
    print("  " + ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    if args.report:
        for s in segments:
            print("  %8.2f  %8.2f  %-6s  %5.1f s"
                  % (s.start, s.end, s.angle, s.duration))


if __name__ == "__main__":
    main()
