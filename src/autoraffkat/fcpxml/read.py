"""FCPXML:n luku.

Tuetaan kahta lähdettä, joista molemmista saadaan sama tieto:

* ``<sync-clip>`` — Final Cutin synkronoitu klippi, kamerat ja mikit laneilla
* ``<project><sequence><spine>`` — käsin aseteltu aikajana

Synkkaus luetaan XML:stä, ei lasketa. Ruutunopeus tulee sekvenssin tai
video-assetin formaatista.

Aikamuunnos: klipin ``offset`` on isännän paikallisessa aikapohjassa, jonka
nollakohta on isännän ``start``. Lapsen absoluuttinen aikajanapaikka on siis
``isännän_absoluuttinen + (lapsen_offset - isännän_start)``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from fractions import Fraction
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

from ..model import MediaItem, Placement
from ..timeline import ZERO, parse_time

# Elementit jotka viittaavat suoraan assettiin.
LEAF_TAGS = {"asset-clip", "video", "audio"}
# Elementit joiden sisään mennään.
CONTAINER_TAGS = {"clip", "sync-clip", "spine", "ref-clip", "mc-clip", "gap"}

DEFAULT_FRAME_DURATION = Fraction(1, 25)


class ReadError(Exception):
    """Luettava XML ei kelpaa."""


@dataclass
class Timeline:
    """Sisäänluettu aikajana."""

    media: list[MediaItem]
    frame_duration: Fraction
    kind: str                       # "project" tai "sync-clip"
    name: str
    source_path: str = ""

    @property
    def start(self) -> Fraction:
        """Ensimmäinen hetki, jolla on mediaa."""
        return min((m.timeline_start for m in self.media), default=ZERO)

    @property
    def end(self) -> Fraction:
        return max((m.timeline_end for m in self.media), default=ZERO)


# ------------------------------------------------------------------ resurssit


@dataclass
class _Asset:
    """``<asset>``-resurssi sellaisenaan. Ei vielä tietoa aikajanapaikoista."""

    id: str
    name: str
    path: str
    src: str
    start: Fraction
    duration: Fraction
    has_video: bool
    has_audio: bool
    audio_rate: int
    audio_channels: int
    audio_sources: int
    video_sources: int
    format_id: str


def _src_to_path(src: str) -> str:
    """``media-rep src`` tiedostopoluksi.

    Final Cut kirjoittaa polun URL-koodattuna file-URLina, joten ääkköset ja
    välilyönnit tulevat prosenttimuodossa.
    """
    if not src:
        return ""
    if src.startswith("file://"):
        parsed = urlparse(src)
        return unquote(parsed.path)
    return unquote(src)


def _int_attr(elem, name: str, default: int) -> int:
    """Kokonaislukuattribuutti. Puuttuva tai kelvoton antaa oletuksen."""
    raw = elem.get(name)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _audio_rate(raw: str | None) -> int:
    """``audioRate`` on joko luku tai ``"48k"``."""
    if not raw:
        return 48000
    text = raw.strip().lower()
    try:
        if text.endswith("k"):
            return int(round(float(text[:-1]) * 1000))
        return int(round(float(text)))
    except ValueError:
        return 48000


def _collect_resources(root) -> tuple[dict[str, _Asset], dict[str, dict], dict]:
    """Kerää ``<resources>``-lohkon: assetit, formaatit ja media-kääreet.

    Palauttaa kolmikon ``(assets, formats, medias)`` id:llä avainnettuna.
    ``medias`` sisältää ``<media>``-elementit sellaisenaan, koska yhdistetyn
    klipin sisään mennään vasta kävelyvaiheessa.
    """
    assets: dict[str, _Asset] = {}
    formats: dict[str, dict] = {}
    medias: dict[str, ET.Element] = {}

    for res in root.iter():
        if res.tag == "format":
            formats[res.get("id", "")] = {
                "frame_duration": parse_time(res.get("frameDuration"), ZERO) or None,
                "width": _int_attr(res, "width", 0),
                "height": _int_attr(res, "height", 0),
                "name": res.get("name", ""),
            }
        elif res.tag == "asset":
            rep = res.find("media-rep")
            src = rep.get("src", "") if rep is not None else res.get("src", "")
            assets[res.get("id", "")] = _Asset(
                id=res.get("id", ""),
                name=res.get("name", "") or os.path.basename(_src_to_path(src)),
                path=_src_to_path(src),
                src=src,
                start=parse_time(res.get("start"), ZERO),
                duration=parse_time(res.get("duration"), ZERO),
                has_video=res.get("hasVideo") == "1" or _int_attr(res, "videoSources", 0) > 0,
                has_audio=res.get("hasAudio") == "1" or _int_attr(res, "audioSources", 0) > 0,
                audio_rate=_audio_rate(res.get("audioRate")),
                audio_channels=_int_attr(res, "audioChannels", 2),
                audio_sources=_int_attr(res, "audioSources", 1),
                video_sources=_int_attr(res, "videoSources", 1),
                format_id=res.get("format", ""),
            )
        elif res.tag == "media":
            medias[res.get("id", "")] = res

    return assets, formats, medias


# ------------------------------------------------------------------ kävely


@dataclass
class _Ctx:
    """Kävelyn tila.

    ``hits`` kerää löydöt järjestyksessä, ``seen`` estää yhdistetyn klipin
    päätymisen ikuiseen rekursioon jos se viittaa itseensä.
    """

    assets: dict[str, _Asset]
    formats: dict[str, dict]
    medias: dict[str, ET.Element]
    hits: list[tuple[str, Placement, str]] = field(default_factory=list)
    seen: set[int] = field(default_factory=set)


def _walk(elem, abs_offset: Fraction, local_start: Fraction, ctx: _Ctx, depth: int = 0) -> None:
    """Kerää ``elem``:n lapsista media-esiintymät absoluuttisin aikajana-ajoin."""
    if depth > 12:
        return
    for child in elem:
        tag = child.tag
        if tag not in LEAF_TAGS and tag not in CONTAINER_TAGS and tag != "audition":
            continue

        child_offset = parse_time(child.get("offset"), ZERO)
        child_start = parse_time(child.get("start"), ZERO)
        child_dur = parse_time(child.get("duration"), ZERO)
        child_abs = abs_offset + (child_offset - local_start)
        lane = _int_attr(child, "lane", 0)

        if tag == "audition":
            # Vain aktiivinen vaihtoehto, joka on ensimmäinen lapsi.
            first = next(iter(child), None)
            if first is not None:
                _walk(child, abs_offset, local_start, ctx, depth + 1)
            continue

        if tag == "gap":
            _walk(child, child_abs, child_start, ctx, depth + 1)
            continue

        if tag == "spine":
            # Toissijainen tarina: lasten offsetit ovat spinen omasta nollasta.
            _walk(child, child_abs, ZERO, ctx, depth + 1)
            continue

        ref = child.get("ref", "")
        if tag in LEAF_TAGS and ref in ctx.assets:
            if child_dur <= 0:
                asset = ctx.assets[ref]
                child_dur = asset.duration
            ctx.hits.append((ref, Placement(child_abs, child_start, child_dur, lane), tag))
            # Liitetyt klipit asset-clipin sisällä.
            _walk(child, child_abs, child_start, ctx, depth + 1)
            continue

        if tag == "ref-clip" and ref in ctx.medias:
            media_elem = ctx.medias[ref]
            if id(media_elem) in ctx.seen:
                continue
            ctx.seen.add(id(media_elem))
            inner = media_elem.find("sequence")
            if inner is not None:
                spine = inner.find("spine")
                tc = parse_time(inner.get("tcStart"), ZERO)
                if spine is not None:
                    _walk(spine, child_abs - (child_start - tc), ZERO, ctx, depth + 1)
            ctx.seen.discard(id(media_elem))
            _walk(child, child_abs, child_start, ctx, depth + 1)
            continue

        # clip / sync-clip / mc-clip ja tuntemattomat viittaukset
        _walk(child, child_abs, child_start, ctx, depth + 1)


# ------------------------------------------------------------------ julkinen


def _pick_container(root) -> tuple[ET.Element, str, str]:
    """Valitsee luettavan rakenteen: projekti ensin, muuten sync-clip."""
    for project in root.iter("project"):
        sequence = project.find("sequence")
        if sequence is not None and sequence.find("spine") is not None:
            return sequence, "project", project.get("name", "Projekti")
    for sync in root.iter("sync-clip"):
        return sync, "sync-clip", sync.get("name", "Synkkaklippi")
    # Viimeinen oljenkorsi: irrallinen sequence tai event-tason clip.
    for sequence in root.iter("sequence"):
        if sequence.find("spine") is not None:
            return sequence, "project", "Sekvenssi"
    raise ReadError(
        "XML:stä ei löytynyt projektia eikä synkronoitua klippiä. "
        "Vie Final Cutista joko synkkaklippi tai projekti."
    )


def _stable_keys(items: list[MediaItem]) -> None:
    """Antaa medioille tunnisteet, jotka säilyvät XML:n uudelleenviennissä."""
    used: dict[str, int] = {}
    for item in items:
        base = os.path.basename(item.path) if item.path else (item.name or item.asset_id)
        count = used.get(base, 0)
        used[base] = count + 1
        item.key = base if count == 0 else f"{base}#{count + 1}"


def read_fcpxml(path: str) -> Timeline:
    """Lukee FCPXML:n aikajanaksi."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ReadError(f"XML ei jäsenny: {exc}") from exc
    root = tree.getroot()
    if root.tag != "fcpxml":
        raise ReadError(f"Juurielementti on <{root.tag}>, odotettiin <fcpxml>.")

    assets, formats, medias = _collect_resources(root)
    container, kind, name = _pick_container(root)

    ctx = _Ctx(assets, formats, medias)
    if kind == "project":
        spine = container.find("spine")
        tc_start = parse_time(container.get("tcStart"), ZERO)
        _walk(spine, -tc_start, ZERO, ctx)
        seq_format = formats.get(container.get("format", ""), {})
        frame_duration = seq_format.get("frame_duration")
    else:
        _walk(container, ZERO, parse_time(container.get("start"), ZERO), ctx)
        frame_duration = formats.get(container.get("format", ""), {}).get("frame_duration")

    if not ctx.hits:
        raise ReadError("Aikajanalta ei löytynyt yhtään mediaa.")

    # Ryhmitellään esiintymät asseteittain.
    items: dict[str, MediaItem] = {}
    order: list[str] = []
    for ref, placement, tag in ctx.hits:
        asset = assets[ref]
        item = items.get(ref)
        if item is None:
            fmt = formats.get(asset.format_id, {})
            item = MediaItem(
                key="",
                name=asset.name,
                path=asset.path,
                src=asset.src,
                asset_start=asset.start,
                asset_duration=asset.duration,
                has_video=asset.has_video,
                has_audio=asset.has_audio,
                width=fmt.get("width", 0),
                height=fmt.get("height", 0),
                frame_duration=fmt.get("frame_duration"),
                audio_rate=asset.audio_rate,
                audio_channels=asset.audio_channels,
                audio_sources=asset.audio_sources,
                video_sources=asset.video_sources,
                asset_id=asset.id,
                format_id=asset.format_id,
            )
            items[ref] = item
            order.append(ref)
        # <video>/<audio> samasta assetista samaan kohtaan ovat sama esiintymä.
        if not any(p.offset == placement.offset and p.duration == placement.duration
                   for p in item.placements):
            item.placements.append(placement)

    media = [items[ref] for ref in order]
    for item in media:
        item.placements.sort(key=lambda p: p.offset)

    if frame_duration is None:
        for item in media:
            if item.has_video and item.frame_duration:
                frame_duration = item.frame_duration
                break
    if frame_duration is None:
        frame_duration = DEFAULT_FRAME_DURATION

    _stable_keys(media)
    return Timeline(media=media, frame_duration=frame_duration, kind=kind,
                    name=name, source_path=os.path.abspath(path))
