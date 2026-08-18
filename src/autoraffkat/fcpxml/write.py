"""FCPXML:n kirjoitus.

Ulos tulee uusi projekti: yksi leikkausraita, kameroiden oma ääni pois
(``srcEnable="video"``), mikkiraidat yhtenäisinä liitettyinä klippeinä omilla
rooleillaan. Leikkauskohdat kvantisoidaan kehyksiin niin, ettei spineen jää
aukkoja eikä päällekkäisyyksiä.
"""

from __future__ import annotations

import os
import re
from fractions import Fraction
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

from ..model import MediaItem, Segment
from ..timeline import (ZERO, FPS_LABELS, format_time, frames_str, fps_of,
                        to_frames)

STANDARD_HEIGHTS = {480, 540, 576, 720, 1080, 1440, 2160, 4320}


class WriteError(Exception):
    """Leikkausta ei voi kirjoittaa."""


def file_url(path: str) -> str:
    """Tiedostopolku file-URLiksi, kun lähde-XML:ssä ei ollut ``src``-arvoa."""
    return "file://" + quote(path)


def sanitize_role(name: str) -> str:
    """Final Cutin alirooli ei siedä pistettä eikä tyhjää nimeä."""
    cleaned = re.sub(r"[.\x00-\x1f]", " ", name or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned or "Puhuja"


def _format_name(width: int, height: int, frame_duration: Fraction) -> str | None:
    """Final Cutin nimetty formaatti, tai ``None`` jos mitat ovat epästandardit.

    Väärä nimi on pahempi kuin puuttuva nimi: Final Cut lukee formaatin
    mitoista ja ``frameDuration``ista, mutta virheellinen nimi voi ohjata sen
    väärään tulkintaan.
    """
    label = FPS_LABELS.get(fps_of(frame_duration))
    if label is None or height not in STANDARD_HEIGHTS:
        return None
    return f"FFVideoFormat{height}p{label}"


class _Formats:
    """Kokoaa tarvittavat ``<format>``-resurssit ja jakaa niille id:t.

    Sama formaatti jaetaan kaikille saman kokoisille ja saman ruutunopeuden
    asseteille, jotta resursseihin ei synny kymmentä identtistä riviä.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[int, int, Fraction], str] = {}
        self.lines: list[str] = []

    def get(self, width: int, height: int, frame_duration: Fraction,
            next_id) -> str:
        """Formaatin id, luoden resurssin ensimmäisellä kysymisellä."""
        key = (width, height, frame_duration)
        if key in self._by_key:
            return self._by_key[key]
        fid = next_id()
        self._by_key[key] = fid
        attrs = [f'id="{fid}"']
        name = _format_name(width, height, frame_duration)
        if name:
            attrs.append(f"name={quoteattr(name)}")
        attrs.append(f'frameDuration="{format_time(frame_duration)}"')
        if width and height:
            attrs.append(f'width="{width}" height="{height}"')
        attrs.append('colorSpace="1-1-1 (Rec. 709)"')
        self.lines.append("    <format " + " ".join(attrs) + "/>")
        return fid


def _asset_lines(item: MediaItem, res_id: str, format_id: str | None) -> list[str]:
    """Yhden median ``<asset>``-resurssi ``<media-rep>``-lapsineen.

    Assetin ``start`` ja ``duration`` ovat lähdemateriaalin omat, eivät
    käytetyn palan: leikkaus rajataan vasta ``<asset-clip>``-tasolla.
    """
    attrs = [
        f'id="{res_id}"',
        f"name={quoteattr(item.name or os.path.basename(item.path))}",
        f'start="{format_time(item.asset_start)}"',
        f'duration="{format_time(item.asset_duration)}"',
    ]
    if item.has_audio:
        attrs += [
            'hasAudio="1"',
            f'audioSources="{max(1, item.audio_sources)}"',
            f'audioChannels="{max(1, item.audio_channels)}"',
            f'audioRate="{item.audio_rate}"',
        ]
    if item.has_video:
        attrs += ['hasVideo="1"', f'videoSources="{max(1, item.video_sources)}"']
        if format_id:
            attrs.append(f'format="{format_id}"')
    lines = ["    <asset " + " ".join(attrs) + ">"]
    if item.src or item.path:
        src = item.src or file_url(item.path)
        lines.append(f'      <media-rep kind="original-media" src={quoteattr(src)}/>')
    lines.append("    </asset>")
    return lines


def _quantize(segments: list[Segment], program_start: Fraction,
              program_frames: int, frame_duration: Fraction
              ) -> list[tuple[Segment, int, int]]:
    """Leikkauskohdat kehyksiksi: tiiviisti, aidosti kasvavasti, ilman aukkoja.

    Jokainen kuva vie vähintään yhden kehyksen. Jos leikkauksia on enemmän kuin
    kehyksiä — mitä päätöskerros ei tuota, mutta mitä ei saa myöskään kirjoittaa
    rikkinäisenä — loput pudotetaan ja edellinen kuva jatkuu niiden yli.
    """
    kept: list[tuple[Segment, int]] = []
    cursor = 0
    for segment in segments:
        want = to_frames(
            Fraction(segment.start).limit_denominator(1_000_000) - program_start,
            frame_duration)
        start = max(cursor, want, 0)
        if not kept:
            start = 0
        if start >= program_frames:
            break
        kept.append((segment, start))
        cursor = start + 1

    spans: list[tuple[Segment, int, int]] = []
    for index, (segment, start) in enumerate(kept):
        end = kept[index + 1][1] if index + 1 < len(kept) else program_frames
        if end > start:
            spans.append((segment, start, end))
    return spans


def build_fcpxml(
    media_by_key: dict[str, MediaItem],
    segments: list[Segment],
    mic_tracks: list[tuple[str, str]],
    frame_duration: Fraction,
    program_start: Fraction,
    program_end: Fraction,
    project_name: str = "Raakaleikkaus",
    version: str = "1.10",
) -> str:
    """Rakentaa FCPXML-merkkijonon.

    ``mic_tracks`` on lista pareja (median key, puhujan nimi) siinä
    järjestyksessä, jossa mikit halutaan laneille -1, -2, ...
    """
    if not segments:
        raise WriteError("Leikkauslista on tyhjä.")

    program_frames = to_frames(program_end - program_start, frame_duration)
    if program_frames <= 0:
        raise WriteError("Ohjelman kesto on nolla.")

    spans = _quantize(segments, program_start, program_frames, frame_duration)
    if not spans:
        raise WriteError("Leikkauskohdat kutistuivat tyhjiksi.")
    used_segments = [segment for segment, _, _ in spans]

    counter = [0]

    def next_id() -> str:
        counter[0] += 1
        return f"r{counter[0]}"

    formats = _Formats()
    # Sekvenssin formaatti ensin, jotta se saa id:n r1.
    reference = next((media_by_key[s.angle] for s in used_segments
                      if s.angle in media_by_key and media_by_key[s.angle].has_video), None)
    seq_width = reference.width if reference else 1920
    seq_height = reference.height if reference else 1080
    seq_format = formats.get(seq_width, seq_height, frame_duration, next_id)

    needed: list[str] = []
    for seg in used_segments:
        if seg.angle and seg.angle not in needed:
            needed.append(seg.angle)
    for key, _ in mic_tracks:
        if key not in needed:
            needed.append(key)

    res_ids: dict[str, str] = {}
    asset_lines: list[str] = []
    for key in needed:
        item = media_by_key.get(key)
        if item is None:
            raise WriteError(f"Mediaa ei löydy: {key}")
        fmt_id = None
        if item.has_video:
            fd = item.frame_duration or frame_duration
            fmt_id = formats.get(item.width or seq_width,
                                 item.height or seq_height, fd, next_id)
        res_ids[key] = next_id()
        asset_lines += _asset_lines(item, res_ids[key], fmt_id)

    # ---------------------------------------------------------- spine
    body: list[str] = []
    first_clip_start_frames = 0
    for index, (seg, a, b) in enumerate(spans):
        item = media_by_key[seg.angle]
        seg_start_tl = program_start + frame_duration * a
        placement = item.placement_at(seg_start_tl) or (
            item.placements[0] if item.placements else None)
        if placement is None:
            raise WriteError(f"Medialla {seg.angle} ei ole paikkaa aikajanalla.")
        src_start = placement.source_at(seg_start_tl)
        src_frames = to_frames(src_start, frame_duration)
        if index == 0:
            first_clip_start_frames = src_frames
        src_enable = "video" if item.has_audio and item.has_video else None

        attrs = [
            f'ref="{res_ids[seg.angle]}"',
            f'offset="{frames_str(a, frame_duration)}"',
            f"name={quoteattr(f'{seg.label} {index + 1:02d}')}",
            f'start="{frames_str(src_frames, frame_duration)}"',
            f'duration="{frames_str(b - a, frame_duration)}"',
            f'format="{seq_format}"',
            'tcFormat="NDF"',
        ]
        if src_enable:
            attrs.append(f'srcEnable="{src_enable}"')
        clip = "            <asset-clip " + " ".join(attrs)

        if index == 0 and mic_tracks:
            body.append(clip + ">")
            body += _mic_lines(media_by_key, mic_tracks, res_ids, frame_duration,
                               program_start, program_end, first_clip_start_frames)
            body.append("            </asset-clip>")
        else:
            body.append(clip + "/>")

    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
        f'<fcpxml version="{version}">',
        "  <resources>",
        *formats.lines,
        *asset_lines,
        "  </resources>",
        "  <library>",
        f"    <event name={quoteattr(project_name)}>",
        f"      <project name={quoteattr(project_name)}>",
        f'        <sequence format="{seq_format}" '
        f'duration="{frames_str(program_frames, frame_duration)}" '
        'tcStart="0s" tcFormat="NDF" audioLayout="stereo" audioRate="48k">',
        "          <spine>",
        *body,
        "          </spine>",
        "        </sequence>",
        "      </project>",
        "    </event>",
        "  </library>",
        "</fcpxml>",
    ]
    return "\n".join(out) + "\n"


def _mic_lines(media_by_key, mic_tracks, res_ids, frame_duration,
               program_start, program_end, parent_start_frames) -> list[str]:
    """Mikit liitettyinä klippeinä ensimmäiseen spine-klippiin.

    Liitetyn klipin ``offset`` on isännän paikallisessa aikapohjassa, jonka
    nollakohta on isännän ``start``. Siksi ohjelman alkuun osuva mikki saa
    offsetiksi juuri isännän ``start``-arvon.
    """
    lines: list[str] = []
    lane = 0
    for key, speaker in mic_tracks:
        item = media_by_key.get(key)
        if item is None or not item.has_audio:
            continue
        lane -= 1
        role = f"dialogue.{sanitize_role(speaker)}"
        src_enable = ' srcEnable="audio"' if item.has_video else ""
        for placement in item.placements:
            clip_start = max(placement.offset, program_start)
            clip_end = min(placement.end, program_end)
            if clip_end <= clip_start:
                continue
            off_frames = to_frames(clip_start - program_start, frame_duration)
            dur_frames = to_frames(clip_end - clip_start, frame_duration)
            if dur_frames <= 0:
                continue
            src_frames = to_frames(placement.source_at(clip_start), frame_duration)
            lines.append(
                "              <asset-clip "
                f'ref="{res_ids[key]}" lane="{lane}" '
                f'offset="{frames_str(parent_start_frames + off_frames, frame_duration)}" '
                f"name={quoteattr(item.name)} "
                f'start="{frames_str(src_frames, frame_duration)}" '
                f'duration="{frames_str(dur_frames, frame_duration)}" '
                f'audioRole={quoteattr(role)}{src_enable}/>'
            )
    return lines


def write_fcpxml(path: str, xml: str) -> str:
    """Kirjoittaa XML:n. Palauttaa absoluuttisen polun."""
    path = os.path.abspath(path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(xml)
    return path
