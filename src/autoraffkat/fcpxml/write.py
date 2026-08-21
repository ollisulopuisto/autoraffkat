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

from dataclasses import replace

from ..audio.mix import ROOM_ROLE
from ..i18n import t
from ..model import DEFAULT_PROJECT_NAME, MediaItem, Segment
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


def _asset_lines(item: MediaItem, res_id: str, format_id: str | None,
                 path: str = "", name: str = "") -> list[str]:
    """Yhden median ``<asset>``-resurssi ``<media-rep>``-lapsineen.

    Assetin ``start`` ja ``duration`` ovat lähdemateriaalin omat, eivät
    käytetyn palan: leikkaus rajataan vasta ``<asset-clip>``-tasolla.

    ``path`` korvaa lähdetiedoston. Käsitelty ääni on näytteelleen saman
    pituinen kuin alkuperäinen, joten ajat kelpaavat sellaisenaan.
    """
    attrs = [
        f'id="{res_id}"',
        f"name={quoteattr(name or item.name or os.path.basename(item.path))}",
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
    src = file_url(path) if path else (item.src or file_url(item.path))
    if src:
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
    project_name: str = DEFAULT_PROJECT_NAME,
    version: str = "1.10",
    replacements: dict[str, str] | None = None,
    room: list[tuple[str, str]] | None = None,
) -> str:
    """Rakentaa FCPXML-merkkijonon.

    ``mic_tracks`` on lista pareja (median key, puhujan nimi) siinä
    järjestyksessä, jossa mikit halutaan laneille -1, -2, ...

    ``replacements`` ohjaa median käsiteltyyn tiedostoon, ``room`` liittää
    tilaäänen omalle lanelleen. Molemmat ovat saman pituisia kuin lähteensä,
    joten aikoihin ei kosketa.
    """
    replacements = replacements or {}
    room = room or []
    if not segments:
        raise WriteError(t("write.empty_cut"))

    program_frames = to_frames(program_end - program_start, frame_duration)
    if program_frames <= 0:
        raise WriteError(t("write.zero_duration"))

    spans = _quantize(segments, program_start, program_frames, frame_duration)
    if not spans:
        raise WriteError(t("write.cuts_collapsed"))
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
            raise WriteError(t("write.media_missing", key=key))
        fmt_id = None
        if item.has_video:
            fd = item.frame_duration or frame_duration
            fmt_id = formats.get(item.width or seq_width,
                                 item.height or seq_height, fd, next_id)
        res_ids[key] = next_id()
        asset_lines += _asset_lines(item, res_ids[key], fmt_id,
                                    replacements.get(key, ""))

    # Tilaääni on oma assettinsa, vaikka lähde olisi sama kamera jota
    # käytetään kuvaan: eri tiedosto, eri rooli, eri lane.
    room_ids: dict[str, str] = {}
    for key, path in room:
        item = media_by_key.get(key)
        if item is None:
            continue
        room_ids[key] = next_id()
        asset_lines += _asset_lines(
            _audio_only(item), room_ids[key], None, path,
            f"{item.name} tilaääni")

    # ---------------------------------------------------------- spine
    body: list[str] = []
    first_clip_start_frames = 0
    for index, (seg, a, b) in enumerate(spans):
        item = media_by_key[seg.angle]
        seg_start_tl = program_start + frame_duration * a
        placement = item.placement_at(seg_start_tl) or (
            item.placements[0] if item.placements else None)
        if placement is None:
            raise WriteError(t("write.no_placement", key=seg.angle))
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

        if index == 0 and (mic_tracks or room_ids):
            body.append(clip + ">")
            attached = [(k, f"dialogue.{sanitize_role(name)}", res_ids)
                        for k, name in mic_tracks]
            attached += [(k, ROOM_ROLE, room_ids) for k, _ in room
                         if k in room_ids]
            body += _mic_lines(media_by_key, attached, frame_duration,
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


def _audio_only(item: MediaItem) -> MediaItem:
    """Kopio mediasta pelkkänä äänenä.

    Tilaääni irrotetaan kameratiedostosta omaksi WAViksi, joten sen assetissa
    ei saa olla kuvaa eikä formaattia — muuten Final Cut etsii kuvaraitaa
    jota tiedostossa ei ole.
    """
    return replace(item, has_video=False, video_sources=0, format_id="",
                   width=0, height=0, frame_duration=None,
                   audio_channels=max(1, item.audio_channels), placements=item.placements)


def _mic_lines(media_by_key, attached, frame_duration,
               program_start, program_end, parent_start_frames) -> list[str]:
    """Liitetyt ääniklipit ensimmäiseen spine-klippiin.

    ``attached`` on lista kolmikoita (median key, rooli, resurssi-id-taulukko).

    Liitetyn klipin ``offset`` on isännän paikallisessa aikapohjassa, jonka
    nollakohta on isännän ``start``. Siksi ohjelman alkuun osuva mikki saa
    offsetiksi juuri isännän ``start``-arvon.
    """
    lines: list[str] = []
    lane = 0
    for key, role, res_ids in attached:
        item = media_by_key.get(key)
        if item is None or not item.has_audio or key not in res_ids:
            continue
        lane -= 1
        src_enable = ""
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


# ------------------------------------------------------------------ multicam


def _boundaries(timeline, program_start: Fraction, frame_duration: Fraction,
                program_frames: int) -> list[int]:
    """Osien rajat kehyksinä ohjelman alusta.

    Kuva ei saa jatkua osasta toiseen: seuraava osa on eri ``<mc-clip>`` eri
    angleID:illä, joten leikkaus on pakko katkaista rajalle.
    """
    marks = set()
    for mc in timeline.multicams:
        for edge in (mc.offset, mc.end):
            frame = to_frames(edge - program_start, frame_duration)
            if 0 < frame < program_frames:
                marks.add(frame)
    return sorted(marks)


def _split_spans(spans, marks: list[int]):
    """Pilkkoo kehysvälit osien rajoilla."""
    out = []
    for segment, a, b in spans:
        cursor = a
        for mark in marks:
            if a < mark < b:
                out.append((segment, cursor, mark))
                cursor = mark
        out.append((segment, cursor, b))
    return [(s, x, y) for s, x, y in out if y > x]


def _mc_sources(video_angle: str, audio_angles: list[tuple[str, str]],
                roles: dict[str, str]) -> list[str]:
    """``<mc-source>``-rivit: yksi kuva, loput ääntä omilla rooleillaan.

    Kuvakulman oma ääni kytketään pois samalla tavalla kuin Final Cut sen
    kirjoittaa: rooli jää näkyviin mutta ``active="0"``.
    """
    lines: list[str] = []
    if video_angle:
        role = roles.get(video_angle, "dialogue.dialogue-1")
        lines += [
            f'              <mc-source angleID={quoteattr(video_angle)} srcEnable="video">',
            f'                <audio-role-source role={quoteattr(role)} active="0"/>',
            "              </mc-source>",
        ]
    for angle_id, speaker in audio_angles:
        role = f"dialogue.{sanitize_role(speaker)}"
        lines += [
            f'              <mc-source angleID={quoteattr(angle_id)} srcEnable="audio">',
            f'                <audio-role-source role={quoteattr(role)}/>',
            "              </mc-source>",
        ]
    return lines


def _redirect_asset(asset, path: str) -> None:
    """Ohjaa assetin toiseen tiedostoon.

    ``<bookmark>`` on **poistettava**. Se on macOS:n tiedostoviite, joka
    osoittaa alkuperäiseen tiedostoon ja voittaa ``src``:n: ilman poistoa
    Final Cut käyttäisi käsittelemätöntä ääntä eikä kertoisi siitä mitään.
    """
    rep = asset.find("media-rep")
    if rep is None:
        return
    rep.set("src", file_url(path))
    for bookmark in rep.findall("bookmark"):
        rep.remove(bookmark)


def _room_asset(source, res_id: str, path: str):
    """Tilaäänestä oma ``<asset>`` kameran assetin pohjalta.

    Ajat peritään lähteestä, koska käsitelty tiedosto on näytteelleen saman
    pituinen. Kuvaan liittyvät tiedot jätetään pois: tilaääni on WAV.
    """
    from xml.etree import ElementTree as ET

    asset = ET.Element("asset")
    asset.set("id", res_id)
    asset.set("name", (source.get("name", "") or "Tilaääni") + " tilaääni")
    for name in ("start", "duration", "audioSources", "audioChannels", "audioRate"):
        if source.get(name):
            asset.set(name, source.get(name))
    asset.set("hasAudio", "1")
    asset.set("audioSources", "1")
    # Tilaääni kirjoitetaan monona, joten kanavamäärä ei peri kameran arvoa.
    asset.set("audioChannels", "1")
    ET.SubElement(asset, "media-rep", {"kind": "original-media",
                                       "src": file_url(path)})
    return asset


def _next_resource_id(resources) -> str:
    """Vapaa resurssi-id kopioidusta lohkosta."""
    used = {child.get("id", "") for child in resources.iter()}
    index = 1
    while f"a{index}" in used:
        index += 1
    return f"a{index}"


def _source_resources(path: str, redirects: dict[str, str] | None = None,
                      room: list[tuple[str, str]] | None = None
                      ) -> tuple[str, str, str, dict[str, str]]:
    """Lähde-XML:n ``<resources>``, versio, sekvenssin formaatti ja tilaääni-id:t.

    Multicam-määrittelyä ei rakenneta uudestaan vaan se kopioidaan: kulmien
    angleID:t ja assettien synkkaus ovat juuri se osa, jota ei saa muuttaa.
    Käsitelty ääni ohjataan paikalleen tätä kopiota muokkaamalla, jolloin
    kaikki muu säilyy koskemattomana.
    """
    from xml.etree import ElementTree as ET

    tree = ET.parse(path)
    root = tree.getroot()
    resources = root.find("resources")
    if resources is None:
        raise WriteError(t("write.no_resources"))
    sequence = root.find(".//sequence")
    seq_format = sequence.get("format", "") if sequence is not None else ""

    by_id = {a.get("id", ""): a for a in resources.iter("asset")}
    for asset_id, target in (redirects or {}).items():
        asset = by_id.get(asset_id)
        if asset is not None:
            _redirect_asset(asset, target)

    room_ids: dict[str, str] = {}
    for asset_id, target in (room or []):
        source = by_id.get(asset_id)
        if source is None:
            continue
        res_id = _next_resource_id(resources)
        resources.append(_room_asset(source, res_id, target))
        room_ids[asset_id] = res_id

    body = ET.tostring(resources, encoding="unicode")
    return body, root.get("version", "1.10"), seq_format, room_ids


def _room_lines(timeline, room, room_ids, frame_duration, program_start,
                program_end, parent_start_frames) -> list[str]:
    """Tilaääni liitettynä klippinä. Sama aikasääntö kuin littanan mikeillä.

    Tilaääni ei ole kulma vaan oma tiedostonsa: kuvakulma vaihtuu joka
    leikkauksessa, tilaäänen on jatkuttava yli niiden.
    """
    lines: list[str] = []
    by_key = timeline.media_by_key()
    for key, _ in room:
        item = by_key.get(key)
        if item is None or item.asset_id not in room_ids:
            continue
        # Kaikki osat ovat samaa tilaääntä eivätkä mene päällekkäin, joten ne
        # kuuluvat samalle lanelle. Oma lane per osa näyttäisi Final Cutissa
        # monelta eri raidalta.
        lane = -1
        res_id = room_ids[item.asset_id]
        for placement in item.placements:
            clip_start = max(placement.offset, program_start)
            clip_end = min(placement.end, program_end)
            if clip_end <= clip_start:
                continue
            off = to_frames(clip_start - program_start, frame_duration)
            dur = to_frames(clip_end - clip_start, frame_duration)
            if dur <= 0:
                continue
            src = to_frames(placement.source_at(clip_start), frame_duration)
            lines.append(
                f'              <asset-clip ref="{res_id}" lane="{lane}" '
                f'offset="{frames_str(parent_start_frames + off, frame_duration)}" '
                f"name={quoteattr(item.name + ' tilaääni')} "
                f'start="{frames_str(src, frame_duration)}" '
                f'duration="{frames_str(dur, frame_duration)}" '
                f'audioRole={quoteattr(ROOM_ROLE)}/>')
    return lines


def build_multicam_fcpxml(
    timeline,
    segments: list[Segment],
    mic_tracks: list[tuple[str, str]],
    program_start: Fraction,
    program_end: Fraction,
    project_name: str = DEFAULT_PROJECT_NAME,
    replacements: dict[str, str] | None = None,
    room: list[tuple[str, str]] | None = None,
) -> str:
    """Rakentaa monikameraleikkauksen: yksi ``<mc-clip>`` per kuva.

    Tulos on natiivi monikameraleikkaus, ei littana: kuvakulman voi vaihtaa
    Final Cutissa jälkikäteen kulmanäkymästä. Resurssit tulevat lähde-XML:stä
    sellaisenaan, joten multicamin sisäinen synkkaus säilyy bittiä myöten.
    """
    if not segments:
        raise WriteError(t("write.empty_cut"))
    if not timeline.multicams:
        raise WriteError(t("write.not_multicam"))

    frame_duration = timeline.frame_duration
    program_frames = to_frames(program_end - program_start, frame_duration)
    if program_frames <= 0:
        raise WriteError(t("write.zero_duration"))

    spans = _quantize(segments, program_start, program_frames, frame_duration)
    spans = _split_spans(spans, _boundaries(timeline, program_start,
                                            frame_duration, program_frames))
    if not spans:
        raise WriteError(t("write.cuts_collapsed"))

    angles_of = {t.key: t.angle_ids for t in timeline.tracks}

    # Käsitelty ääni ohjataan resurssitasolla: kulmat ja mc-sourcet viittaavat
    # assettiin, joten yksi src riittää eikä leikkauslistaan tarvitse koskea.
    by_key = timeline.media_by_key()
    redirects = {by_key[k].asset_id: path
                 for k, path in (replacements or {}).items() if k in by_key}
    room_jobs = [(by_key[k].asset_id, path)
                 for k, path in (room or []) if k in by_key]
    resources, version, seq_format, room_ids = _source_resources(
        timeline.source_path, redirects, room_jobs)

    body: list[str] = []
    attached_room = False
    for index, (seg, a, b) in enumerate(spans):
        at = program_start + frame_duration * a
        mc = timeline.multicam_at(at)
        if mc is None:
            # Osien välinen aukko: sisältöä ei ole, mutta spine ei saa katketa.
            body.append(f'            <gap name="Gap" '
                        f'offset="{frames_str(a, frame_duration)}" start="0s" '
                        f'duration="{frames_str(b - a, frame_duration)}"/>')
            continue

        own = set(mc.angle_ids)
        video_angle = next((x for x in angles_of.get(seg.angle, []) if x in own), "")
        audio_angles = []
        for key, speaker in mic_tracks:
            angle_id = next((x for x in angles_of.get(key, []) if x in own), "")
            if angle_id and angle_id != video_angle:
                audio_angles.append((angle_id, speaker))

        start_frames = to_frames(mc.source_at(at), frame_duration)
        attrs = [
            f'ref={quoteattr(mc.media_id)}',
            f'offset="{frames_str(a, frame_duration)}"',
            f"name={quoteattr(f'{seg.label} {index + 1:02d}')}",
            f'start="{frames_str(start_frames, frame_duration)}"',
            f'duration="{frames_str(b - a, frame_duration)}"',
        ]
        sources = _mc_sources(video_angle, audio_angles, mc.angle_roles)
        if not attached_room and room_ids:
            attached_room = True
            sources = sources + _room_lines(
                timeline, room or [], room_ids, frame_duration,
                program_start, program_end, to_frames(mc.source_at(at), frame_duration))
        if sources:
            body.append("            <mc-clip " + " ".join(attrs) + ">")
            body += sources
            body.append("            </mc-clip>")
        else:
            body.append("            <mc-clip " + " ".join(attrs) + "/>")

    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
        f'<fcpxml version="{version}">',
        "  " + resources.strip(),
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
