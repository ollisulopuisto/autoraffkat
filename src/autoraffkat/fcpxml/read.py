"""FCPXML:n luku.

Tuetaan kolmea lähdettä, joista kaikista saadaan sama tieto:

* ``<sync-clip>`` — Final Cutin synkronoitu klippi, kamerat ja mikit laneilla
* ``<project><sequence><spine>`` — käsin aseteltu aikajana
* ``<mc-clip>`` — monikameraklippi, kamerat ja mikit kulmina

Synkkaus luetaan XML:stä, ei lasketa. Ruutunopeus tulee sekvenssin tai
video-assetin formaatista.

Aikamuunnos: klipin ``offset`` on isännän paikallisessa aikapohjassa, jonka
nollakohta on isännän ``start``. Lapsen absoluuttinen aikajanapaikka on siis
``isännän_absoluuttinen + (lapsen_offset - isännän_start)``.

Monikamerassa isäntä on ``<mc-clip>`` ja sisältö ``<media><multicam>``:in
kulmissa. Kulman sisältö ulottuu koko multicamin yli, joten se on rajattava
``mc-clip``:n kestoon — muuten kaksi osaa samasta multicamista tuottaisi
päällekkäiset esiintymät.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from fractions import Fraction
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from xml.etree import ElementTree as ET

from ..i18n import t
from ..model import MediaItem, Placement
from ..timeline import ZERO, parse_time

# Elementit jotka viittaavat suoraan assettiin.
LEAF_TAGS = {"asset-clip", "video", "audio"}
# Elementit joiden sisään mennään.
CONTAINER_TAGS = {
    "clip",
    "sync-clip",
    "spine",
    "ref-clip",
    "mc-clip",
    "gap",
    "mc-angle",
}

DEFAULT_FRAME_DURATION = Fraction(1, 25)

# Aikaväli, johon esiintymät rajataan. ``None`` = ei rajausta.
Bounds = tuple[Fraction, Fraction] | None


class ReadError(Exception):
    """Luettava XML ei kelpaa."""


@dataclass
class MulticamRef:
    """Yksi ``<mc-clip>`` aikajanalla.

    ``start`` on multicamin paikallinen aika siinä kohdassa, jossa klippi
    alkaa aikajanalla. Kirjoitus tarvitsee tämän, jotta leikatut palat
    osaavat asettaa oman ``start``-arvonsa samaan aikapohjaan.
    """

    media_id: str
    name: str
    offset: Fraction
    duration: Fraction
    start: Fraction
    angle_ids: list[str] = field(default_factory=list)
    # angleID -> lähteen ääniroolin nimi. Kirjoitus toistaa saman roolin
    # pois kytkettynä kuvakulmalle, kuten Final Cut itse tekee.
    angle_roles: dict[str, str] = field(default_factory=dict)

    @property
    def end(self) -> Fraction:
        return self.offset + self.duration

    def covers(self, seconds: Fraction) -> bool:
        return self.offset <= seconds < self.end

    def source_at(self, seconds: Fraction) -> Fraction:
        """Aikajanan hetki multicamin paikalliseksi ajaksi."""
        return self.start + (seconds - self.offset)


@dataclass
class Track:
    """Roolitettava yksikkö.

    Tavallisessa aikajanassa raita on yksi media. Monikamerassa sama kulma
    esiintyy erillisenä assettina joka osassa — kolme kameraa kahdessa osassa
    on kuusi assettia mutta kolme raitaa — joten raita kokoaa ne yhteen.
    Roolit, säätimet ja päätöskerroksen avaimet viittaavat aina raitaan.
    """

    key: str
    name: str
    media_keys: list[str] = field(default_factory=list)
    angle_ids: list[str] = field(default_factory=list)
    has_video: bool = False
    has_audio: bool = False


@dataclass
class Timeline:
    """Sisäänluettu aikajana."""

    media: list[MediaItem]
    frame_duration: Fraction
    kind: str  # "project", "sync-clip" tai "multicam"
    name: str
    source_path: str = ""
    tracks: list[Track] = field(default_factory=list)
    multicams: list[MulticamRef] = field(default_factory=list)

    @property
    def start(self) -> Fraction:
        """Ensimmäinen hetki, jolla on mediaa."""
        return min((m.timeline_start for m in self.media), default=ZERO)

    @property
    def end(self) -> Fraction:
        return max((m.timeline_end for m in self.media), default=ZERO)

    def media_by_key(self) -> dict[str, MediaItem]:
        return {m.key: m for m in self.media}

    def track_media(self, key: str) -> list[MediaItem]:
        """Raidan mediat. Tyhjä lista jos raitaa ei ole."""
        by_key = self.media_by_key()
        for track in self.tracks:
            if track.key == key:
                return [by_key[k] for k in track.media_keys if k in by_key]
        return []

    def track_span(self, key: str) -> tuple[Fraction, Fraction] | None:
        """Raidan aikajanaväli: ensimmäisestä osasta viimeiseen."""
        items = self.track_media(key)
        if not items:
            return None
        return (
            min(m.timeline_start for m in items),
            max(m.timeline_end for m in items),
        )

    def multicam_at(self, seconds: Fraction) -> MulticamRef | None:
        for mc in self.multicams:
            if mc.covers(seconds):
                return mc
        return None


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
        # Windowsissa polku on ``/C:/...`` ja se on käännettävä asemaksi ja
        # kenoviivoiksi; POSIXissa ``url2pathname`` on pelkkä ``unquote``.
        # Verkkolevy on ``file://palvelin/jako``, jolloin netloc kuuluu polkuun.
        host = "" if parsed.netloc in ("", "localhost") else parsed.netloc
        return url2pathname(f"//{host}{parsed.path}" if host else parsed.path)
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
    klipin ja multicamin sisään mennään vasta kävelyvaiheessa.
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
                has_video=res.get("hasVideo") == "1"
                or _int_attr(res, "videoSources", 0) > 0,
                has_audio=res.get("hasAudio") == "1"
                or _int_attr(res, "audioSources", 0) > 0,
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
class _Hit:
    """Yksi löydetty media-esiintymä."""

    ref: str
    placement: Placement
    tag: str
    angle_id: str = ""


@dataclass
class _Ctx:
    """Kävelyn tila.

    ``hits`` kerää löydöt järjestyksessä, ``seen`` estää yhdistetyn klipin
    päätymisen ikuiseen rekursioon jos se viittaa itseensä.
    """

    assets: dict[str, _Asset]
    formats: dict[str, dict]
    medias: dict[str, ET.Element]
    hits: list[_Hit] = field(default_factory=list)
    seen: set[int] = field(default_factory=set)
    multicams: list[MulticamRef] = field(default_factory=list)
    angle_names: dict[str, str] = field(default_factory=dict)  # angleID -> nimi
    angle_owner: dict[str, str] = field(default_factory=dict)  # angleID -> media id


def _intersect(outer: Bounds, inner: Bounds) -> Bounds:
    """Kahden aikavälin leikkaus. ``None`` tarkoittaa rajatonta."""
    if outer is None:
        return inner
    if inner is None:
        return outer
    return (max(outer[0], inner[0]), min(outer[1], inner[1]))


def _walk(
    elem,
    abs_offset: Fraction,
    local_start: Fraction,
    ctx: _Ctx,
    depth: int = 0,
    bounds: Bounds = None,
    angle_id: str = "",
) -> None:
    """Kerää ``elem``:n lapsista media-esiintymät absoluuttisin aikajana-ajoin.

    ``bounds`` rajaa löydöt isännän kestoon. Sitä tarvitaan multicamissa ja
    yhdistetyssä klipissä, joiden sisältö on pidempi kuin käytetty pala.
    """
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
                _walk(child, abs_offset, local_start, ctx, depth + 1, bounds, angle_id)
            continue

        if tag == "gap":
            _walk(child, child_abs, child_start, ctx, depth + 1, bounds, angle_id)
            continue

        if tag == "spine":
            # Toissijainen tarina: lasten offsetit ovat spinen omasta nollasta.
            _walk(child, child_abs, ZERO, ctx, depth + 1, bounds, angle_id)
            continue

        ref = child.get("ref", "")
        if tag in LEAF_TAGS and ref in ctx.assets:
            if child_dur <= 0:
                child_dur = ctx.assets[ref].duration
            placement = _clip(child_abs, child_start, child_dur, lane, bounds)
            if placement is not None:
                ctx.hits.append(_Hit(ref, placement, tag, angle_id))
            # Liitetyt klipit asset-clipin sisällä.
            _walk(child, child_abs, child_start, ctx, depth + 1, bounds, angle_id)
            continue

        if tag == "mc-clip" and ref in ctx.medias:
            _walk_multicam(child, ctx, child_abs, child_start, child_dur, depth, bounds)
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
                    span = _intersect(bounds, _span(child_abs, child_dur))
                    _walk(
                        spine,
                        child_abs - (child_start - tc),
                        ZERO,
                        ctx,
                        depth + 1,
                        span,
                        angle_id,
                    )
            ctx.seen.discard(id(media_elem))
            _walk(child, child_abs, child_start, ctx, depth + 1, bounds, angle_id)
            continue

        # clip / sync-clip ja tuntemattomat viittaukset
        _walk(child, child_abs, child_start, ctx, depth + 1, bounds, angle_id)


def _span(offset: Fraction, duration: Fraction) -> Bounds:
    """Klipin oma aikaväli, tai ``None`` jos kestoa ei ole ilmoitettu."""
    return (offset, offset + duration) if duration > 0 else None


def _clip(
    offset: Fraction, start: Fraction, duration: Fraction, lane: int, bounds: Bounds
) -> Placement | None:
    """Esiintymä rajattuna isännän aikaväliin.

    Vasemmalta rajattaessa myös lähdeaika siirtyy saman verran, jotta
    aikajanan ja tiedoston aikojen vastaavuus säilyy.
    """
    lo, hi = offset, offset + duration
    if bounds is not None:
        lo = max(lo, bounds[0])
        hi = min(hi, bounds[1])
    if hi <= lo:
        return None
    return Placement(lo, start + (lo - offset), hi - lo, lane)


def _walk_multicam(
    child,
    ctx: _Ctx,
    child_abs: Fraction,
    child_start: Fraction,
    child_dur: Fraction,
    depth: int,
    bounds: Bounds,
) -> None:
    """``<mc-clip>``: kaikki kulmat, kukin omalla angleID:llään.

    Kulmien sisältö on multicamin omassa aikapohjassa, jonka nollakohta on
    ``tcStart``. Aikajanan ja multicamin vastaavuus tulee ``mc-clip``:n
    ``start``-arvosta, ja kesto rajaa kaiken löydetyn.
    """
    media_elem = ctx.medias[child.get("ref", "")]
    mcam = media_elem.find("multicam")
    if mcam is None or id(media_elem) in ctx.seen:
        return
    ctx.seen.add(id(media_elem))

    tc = parse_time(mcam.get("tcStart"), ZERO)
    base = child_abs - (child_start - tc)
    span = _intersect(bounds, _span(child_abs, child_dur))

    angle_ids: list[str] = []
    for angle in mcam.findall("mc-angle"):
        aid = angle.get("angleID", "")
        if not aid:
            continue
        angle_ids.append(aid)
        ctx.angle_names.setdefault(aid, angle.get("name", "") or aid)
        ctx.angle_owner.setdefault(aid, media_elem.get("id", ""))
        _walk(angle, base, ZERO, ctx, depth + 1, span, aid)

    ctx.seen.discard(id(media_elem))
    ctx.multicams.append(
        MulticamRef(
            media_id=media_elem.get("id", ""),
            name=child.get("name", "") or media_elem.get("name", ""),
            offset=child_abs,
            duration=child_dur,
            start=child_start,
            angle_ids=angle_ids,
            angle_roles=_source_roles(child),
        )
    )


def _source_roles(mc_clip) -> dict[str, str]:
    """``<mc-clip>``:n kulmakohtaiset ääniroolit sellaisina kuin ne ovat."""
    roles: dict[str, str] = {}
    for source in mc_clip.findall("mc-source"):
        aid = source.get("angleID", "")
        role = source.find("audio-role-source")
        if aid and role is not None and role.get("role"):
            roles[aid] = role.get("role", "")
    return roles


# ------------------------------------------------------------------ raidat


def _group_key(name: str) -> str:
    """Kulman nimi ryhmittelyavaimeksi.

    Osat erotetaan tyypillisesti yhdellä kirjaimella — ``"host a Track2"`` ja
    ``"host b Track2"`` ovat sama mikki — joten yksikirjaimiset sanat
    pudotetaan. Pelkkä numero on kelvollinen kulman nimi (``"1"``), joten
    numeroihin ei kosketa.
    """
    tokens = [t for t in re.split(r"[\s_]+", name.strip().lower()) if t]
    kept = [t for t in tokens if not (len(t) == 1 and t.isalpha())]
    return " ".join(kept or tokens)


def _common_name(names: list[str]) -> str:
    """Nimien yhteinen osa: erottavat sanat pois, järjestys säilyttäen.

    Ryhmän avain johdetaan tiedostonimistä eikä kulman nimestä, koska kulmien
    nimet ja angleID:t vaihtuvat viennistä toiseen. ``"CAM 1 01"`` ja
    ``"CAM 1 02"`` antavat ``"CAM 1"``, joka osoittaa saman
    kameran vielä silloinkin kun kulmat on numeroitu uusiksi.
    """
    if len(set(names)) <= 1:
        return names[0]
    split = [re.split(r"\s+", n.strip()) for n in names]
    if len({len(t) for t in split}) != 1:
        return names[0]
    kept = [tokens[0] for tokens in zip(*split) if len(set(tokens)) == 1]
    return " ".join(kept) or names[0]


def _group_angles(
    order: list[str], names: dict[str, str], owner: dict[str, str]
) -> list[list[str]]:
    """Kulmat ryhmiksi: ensin tarkka nimi, sitten löyhä osamerkin ohitus.

    Kahta saman multicamin kulmaa ei koskaan yhdistetä, vaikka nimet
    normalisoituisivat samaksi: ne ovat varmasti eri kuvia.
    """
    exact: dict[str, list[str]] = {}
    for aid in order:
        exact.setdefault(names.get(aid, aid), []).append(aid)

    groups: list[list[str]] = []
    by_norm: dict[str, int] = {}
    for name, aids in exact.items():
        norm = _group_key(name)
        index = by_norm.get(norm)
        if index is not None:
            owners = {owner.get(a, "") for a in groups[index]}
            if not owners & {owner.get(a, "") for a in aids}:
                groups[index].extend(aids)
                continue
        by_norm.setdefault(norm, len(groups))
        groups.append(list(aids))
    return groups


def _build_tracks(
    media: list[MediaItem], ctx: _Ctx, angles_of: dict[str, list[str]]
) -> list[Track]:
    """Raidat medioista ja kulmista.

    Ilman multicamia jokainen media on oma raitansa, jolloin avain on
    tiedostonimi kuten ennenkin ja vanhat asetustiedostot kelpaavat.
    """
    by_key = {m.key: m for m in media}
    order = [
        aid for aid in ctx.angle_names if any(aid in v for v in angles_of.values())
    ]
    grouped = _group_angles(order, ctx.angle_names, ctx.angle_owner)

    tracks: list[Track] = []
    claimed: set[str] = set()
    used_keys: dict[str, int] = {}

    def add(key: str, name: str, media_keys: list[str], angle_ids: list[str]) -> None:
        count = used_keys.get(key, 0)
        used_keys[key] = count + 1
        unique = key if count == 0 else f"{key}#{count + 1}"
        items = [by_key[k] for k in media_keys if k in by_key]
        tracks.append(
            Track(
                key=unique,
                name=name,
                media_keys=media_keys,
                angle_ids=angle_ids,
                has_video=any(m.has_video for m in items),
                has_audio=any(m.has_audio for m in items),
            )
        )

    for group in grouped:
        keys = [k for k in by_key if any(a in angles_of.get(k, ()) for a in group)]
        if not keys:
            continue
        claimed.update(keys)
        # Kulman nimi ryhmittelee, tiedostonimi nimeää: nimi ja angleID
        # vaihtuvat viennistä toiseen, mutta avain päätyy asetustiedostoon
        # ja sen on kestettävä uusi vienti samasta projektista.
        stems = [os.path.splitext(by_key[k].name or k)[0] for k in keys]
        add(_common_name(stems), _common_name(stems), keys, list(group))

    # Multicamin ulkopuoliset mediat omina raitoinaan.
    for item in media:
        if item.key not in claimed:
            add(item.key, item.name, [item.key], [])

    # Järjestys: kuvat ensin, sitten äänet — sama kuin ennen medialistassa.
    order_index = {m.key: i for i, m in enumerate(media)}
    tracks.sort(
        key=lambda t: min(
            (order_index[k] for k in t.media_keys), default=len(order_index)
        )
    )
    return tracks


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
    raise ReadError(t("read.no_project"))


def _stable_keys(items: list[MediaItem]) -> None:
    """Antaa medioille tunnisteet, jotka säilyvät XML:n uudelleenviennissä."""
    used: dict[str, int] = {}
    for item in items:
        base = (
            os.path.basename(item.path) if item.path else (item.name or item.asset_id)
        )
        count = used.get(base, 0)
        used[base] = count + 1
        item.key = base if count == 0 else f"{base}#{count + 1}"


def read_fcpxml(path: str) -> Timeline:
    """Lukee FCPXML:n aikajanaksi."""
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        raise ReadError(t("read.bad_xml", error=exc)) from exc
    root = tree.getroot()
    if root.tag != "fcpxml":
        raise ReadError(t("read.bad_root", tag=root.tag))

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
        frame_duration = formats.get(container.get("format", ""), {}).get(
            "frame_duration"
        )

    if not ctx.hits:
        raise ReadError(t("read.no_media"))

    # Ryhmitellään esiintymät asseteittain.
    items: dict[str, MediaItem] = {}
    order: list[str] = []
    angles_of: dict[str, list[str]] = {}
    for hit in ctx.hits:
        asset = assets[hit.ref]
        item = items.get(hit.ref)
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
            items[hit.ref] = item
            order.append(hit.ref)
        # <video>/<audio> samasta assetista samaan kohtaan ovat sama esiintymä.
        if not any(
            p.offset == hit.placement.offset and p.duration == hit.placement.duration
            for p in item.placements
        ):
            item.placements.append(hit.placement)
        if hit.angle_id and hit.angle_id not in item.angle_ids:
            item.angle_ids.append(hit.angle_id)

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
    for item in media:
        angles_of[item.key] = item.angle_ids
        item.angle_name = (
            ctx.angle_names.get(item.angle_ids[0], "") if item.angle_ids else ""
        )

    ctx.multicams.sort(key=lambda mc: mc.offset)
    tracks = _build_tracks(media, ctx, angles_of)
    if ctx.multicams:
        kind = "multicam"

    return Timeline(
        media=media,
        frame_duration=frame_duration,
        kind=kind,
        name=name,
        source_path=os.path.abspath(path),
        tracks=tracks,
        multicams=ctx.multicams,
    )
