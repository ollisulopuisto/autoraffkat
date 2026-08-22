"""Päätöskerros: nopea kerros.

Saa valmiit verhokäyrät ruudukolle kohdistettuina ja päättää kynnyksistä,
vähimmäiskestoista ja päällekkäispuheen säännöstä leikkauslistan. Ajetaan
uudestaan joka kerta kun liukusäädintä liikautetaan, joten tässä ei saa olla
tiedostojen lukua eikä silmukoita yksittäisten näytteiden yli — vain numpyta ja
silmukka jaksojen (ei näytteiden) yli.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .model import (
    HOP,
    LONGTAKE_STAY,
    OVERLAP_HOLD,
    OVERLAP_WIDE,
    Globals,
    Segment,
)

WIDE = -2  # want-taulukon erikoisarvot
HOLD = -1

WIDE_LABEL = "Laaja"


# ------------------------------------------------------------------ apurit


def _runs(values: np.ndarray) -> list[tuple[int, int, int]]:
    """Jaksot (alku, loppu, arvo). Loppu on poissulkeva."""
    if values.size == 0:
        return []
    change = np.flatnonzero(values[1:] != values[:-1]) + 1
    bounds = np.concatenate(([0], change, [values.size]))
    return [
        (int(bounds[i]), int(bounds[i + 1]), int(values[bounds[i]]))
        for i in range(bounds.size - 1)
    ]


def _close_gaps(mask: np.ndarray, k: int) -> np.ndarray:
    """Täyttää k:ta lyhyemmät epätodet jaksot. Estää sanavälien pilkkomisen."""
    if k <= 1 or mask.size == 0:
        return mask
    out = mask.copy()
    for start, end, value in _runs(mask.astype(np.int8)):
        if not value and start > 0 and end < mask.size and (end - start) < k:
            out[start:end] = True
    return out


def _open_runs(mask: np.ndarray, k: int) -> np.ndarray:
    """Poistaa k:ta lyhyemmät todet jaksot. Tämä on vahvistusaika."""
    if k <= 1 or mask.size == 0:
        return mask
    out = mask.copy()
    for start, end, value in _runs(mask.astype(np.int8)):
        if value and (end - start) < k:
            out[start:end] = False
    return out


def open_windows(
    on: np.ndarray, lookahead: float, hold: float, min_open: float
) -> np.ndarray:
    """Mistä mikki on auki, kun ``on`` on kynnyksen ylitys.

    Kynnyksen ylitys sellaisenaan on kelvoton portin ohjaukseksi: se välkkyy
    tavuvälien yli ja reagoi yksittäiseen yskäisyyn. Kolme muunnosta tekevät
    siitä käyttökelpoisen, ja ne vastaavat kolmea säädintä:

    * ``min_open`` pudottaa liian lyhyet jaksot — yskäisy ja naksahdus eivät
      avaa mikkiä.
    * ``lookahead`` avaa portin ennen puheen alkua. Tämä on mahdollista vain
      koska käsittely on jälkikäteistä; reaaliaikainen portti ei voi avautua
      ennen kuin ääni on jo tullut, ja siksi siltä katoaa sanojen alkuja.
    * ``hold`` pitää portin auki puheen jälkeen, jolloin lauseen häntä ja
      hengitys jäävät mukaan eikä väleihin tule pumppausta.

    Silmukka kulkee jaksojen yli, ei näytteiden.
    """
    if on.size == 0:
        return on
    mask = _open_runs(on, _hops(min_open)) if min_open > 0 else on
    before = _hops(lookahead) if lookahead > 0 else 0
    after = _hops(hold) if hold > 0 else 0
    if not (before or after):
        return mask
    out = np.zeros_like(mask)
    for start, end, value in _runs(mask.astype(np.int8)):
        if value:
            out[max(0, start - before) : min(mask.size, end + after)] = True
    return out


def trim_end(mask: np.ndarray, seconds: float) -> np.ndarray:
    """Lyhentää jokaista totta jaksoa lopusta annetun verran.

    Tätä tarvitaan vaimennuksen paluuseen: liu'un on ehdittävä loppuun ennen
    kuin peittävä ääni loppuu, muuten se kuuluu hiljaisuudessa.
    """
    if seconds <= 0 or mask.size == 0:
        return mask
    cut = _hops(seconds)
    out = np.zeros_like(mask)
    for start, end, value in _runs(mask.astype(np.int8)):
        if value and end - start > cut:
            out[start : end - cut] = True
    return out


def drop_short(mask: np.ndarray, seconds: float) -> np.ndarray:
    """Pudottaa annettua lyhyemmät todet jaksot pois."""
    return _open_runs(mask, _hops(seconds)) if seconds > 0 else mask


def _hops(seconds: float) -> int:
    """Sekunnit ruudukon askeliksi, aina vähintään yksi."""
    return max(1, int(round(seconds / HOP)))


# ------------------------------------------------------------------ syöte


@dataclass
class SpeakerLanes:
    """Yhden puhujan aineisto ruudukolla."""

    name: str
    level: np.ndarray  # dB, vahvistuskorjaus jo mukana
    on: np.ndarray  # bool, kynnyksen ylitys
    close_key: str | None  # lähikuvan media key, None jos ei lähikuvaa
    available: np.ndarray | None = None  # missä lähikuva on olemassa


@dataclass
class Grid:
    """Päätöskerroksen syöte: kaikki ruudukolle kohdistettuna."""

    n: int  # ruudukon pituus (HOP-askelta)
    program_start: float  # aikajanan sekunneissa
    speakers: list[SpeakerLanes] = field(default_factory=list)
    wide_key: str = ""

    @property
    def duration(self) -> float:
        return self.n * HOP


@dataclass
class Decision:
    """Päätöksen tulos: leikkauslista ja esikatselun tarvitsemat taulukot."""

    segments: list[Segment]
    active: np.ndarray  # (puhujia, n) bool — esikatselupalkkia varten
    chosen: np.ndarray  # (n,) int — puhujan indeksi tai WIDE


# ------------------------------------------------------------------ päätös


def _want_array(grid: Grid, g: Globals) -> tuple[np.ndarray, np.ndarray]:
    """Kunkin hetken toivottu kuva ilman kestorajoituksia."""
    n = grid.n
    count_speakers = len(grid.speakers)
    active = np.zeros((count_speakers, n), dtype=bool)
    levels = np.full((count_speakers, n), -200.0, dtype=np.float32)
    for i, sp in enumerate(grid.speakers):
        active[i] = sp.on
        levels[i] = sp.level

    want = np.full(n, HOLD, dtype=np.int32)
    if count_speakers == 0:
        return want, active

    count = active.sum(axis=0)
    loudest = np.argmax(levels, axis=0)

    # Yksi äänessä: hänen lähikuvansa.
    single = count == 1
    want[single] = np.argmax(active, axis=0)[single]

    if count_speakers >= 2:
        many = count >= 2
        # Ohikiitävä myötäily ei ole päällekkäispuhetta.
        overlap = _open_runs(many, _hops(g.min_overlap))
        brief = many & ~overlap
        want[brief] = loudest[brief]

        if g.overlap_rule == OVERLAP_WIDE:
            want[overlap] = WIDE
        elif g.overlap_rule == OVERLAP_HOLD:
            want[overlap] = HOLD
        else:  # OVERLAP_LOUDER
            masked = np.where(active, levels, -300.0)
            ordered = np.sort(masked, axis=0)
            margin = ordered[-1] - ordered[-2]
            strong = overlap & (margin >= g.dominance_db)
            want[strong] = loudest[strong]
            want[overlap & ~strong] = HOLD

    # Puhuja ilman lähikuvaa näytetään laajana.
    for i, sp in enumerate(grid.speakers):
        if sp.close_key is None:
            want[want == i] = WIDE
        elif sp.available is not None:
            want[(want == i) & ~sp.available] = HOLD

    return want, active


def _compute_tempo(active: np.ndarray, n: int) -> np.ndarray:
    """Laskee keskustelun paikallisen tempon (1/f -vaihtelu liukuvalla ikkunalla)."""
    if active.size == 0 or n == 0:
        return np.ones(n, dtype=np.float32)
    changes = np.sum(
        np.abs(np.diff(active.astype(np.int8), axis=1, prepend=0)), axis=0
    ).astype(np.float32)
    window = _hops(45.0)  # 45 sekunnin liukuva ikkuna
    if n < window:
        window = max(1, n)
    kernel = np.ones(window, dtype=np.float32) / window
    rate = np.convolve(changes, kernel, mode="same")
    mean_rate = float(np.mean(rate)) + 1e-4
    tempo = np.clip(rate / mean_rate, 0.7, 1.4)
    return tempo


def _cut_points(
    want: np.ndarray, g: Globals, tempo: np.ndarray | None = None
) -> list[tuple[float, int]]:
    """Kestorajoitukset: vahvistusaika, ennakko (J-cut), 1/f-tempo-ohjattu kesto."""
    confirm = _hops(g.confirm)
    current = WIDE
    cuts: list[tuple[float, int]] = [(0.0, WIDE)]
    last_cut = -g.min_shot

    for start, end, target in _runs(want):
        if target == HOLD or target == current:
            continue
        if (end - start) < confirm:
            continue

        # 1/f tempo skaalaa paikallista vähimmäiskestoa luonnollisen vaihtelun saavuttamiseksi
        if tempo is not None and start < tempo.size:
            local_min = max(0.4, g.min_shot / float(np.sqrt(tempo[start])))
        else:
            local_min = g.min_shot

        at = max(start * HOP - g.lead, last_cut + local_min, 0.0)
        if at >= end * HOP:
            continue  # ennakko ja minimikesto söivät koko jakson
        cuts.append((at, target))
        current = target
        last_cut = at
    return cuts


def _find_breath_point(
    grid: Grid | None, speaker_angle: str, target_time: float, window: float = 1.5
) -> float:
    """Etsii luontevan tauko- tai hengähdyskohdan leikkaukselle."""
    if grid is None:
        return target_time
    sp = next((s for s in grid.speakers if s.close_key == speaker_angle), None)
    if sp is None or sp.on.size == 0:
        return target_time

    t_rel = target_time - grid.program_start
    t_start = max(0.0, t_rel - window)
    t_end = min(grid.duration, t_rel + window)
    i0 = int(round(t_start / HOP))
    i1 = int(round(t_end / HOP))
    if i1 <= i0:
        return target_time

    sub_on = sp.on[i0:i1]
    # 1. Ensisijaisesti etsitään taukoa (on == False)
    if not np.all(sub_on):
        runs = _runs(sub_on.astype(np.int8))
        pause_runs = [(r_start, r_end) for r_start, r_end, val in runs if not val]
        if pause_runs:
            best = max(pause_runs, key=lambda p: p[1] - p[0])
            mid_idx = i0 + (best[0] + best[1]) // 2
            return grid.program_start + mid_idx * HOP

    # 2. Jos puhe on tasaista eikä äänessä ole selkeää notkahdusta (>3 dB), pysytään tavoiteajassa
    sub_level = sp.level[i0:i1]
    if sub_level.size > 0:
        min_val = float(np.min(sub_level))
        max_val = float(np.max(sub_level))
        if max_val - min_val >= 3.0:
            min_idx = i0 + int(np.argmin(sub_level))
            return grid.program_start + min_idx * HOP

    return target_time


def _force_wide(
    segments: list[Segment],
    g: Globals,
    wide_label: str,
    wide_key: str,
    grid: Grid | None = None,
) -> list[Segment]:
    """Katkaisee pitkän puheenvuoron laajaan tai reaktiokuvaan.

    Yksi lähikuva ei kanna loputtomiin: kun sama puhuja pitää lattiaa
    ``wide_every`` sekuntia, kuva vaihtuu laajaan tai reaktioon.
    """
    if g.wide_every <= 0 or not wide_key:
        return segments
    stay = g.long_take_rule == LONGTAKE_STAY
    reaction = g.long_take_rule == "reaction"
    hold = max(g.wide_hold, g.min_shot)

    def alt_target(speaker_angle: str) -> tuple[str, str]:
        if reaction and grid is not None:
            other = next(
                (
                    s
                    for s in grid.speakers
                    if s.close_key and s.close_key != speaker_angle
                ),
                None,
            )
            if other and other.close_key:
                return other.close_key, other.name
        return wide_key, wide_label

    out: list[Segment] = []
    for seg in segments:
        if seg.angle == wide_key or seg.duration <= g.wide_every:
            out.append(seg)
            continue
        insert_key, insert_label = alt_target(seg.angle)
        if stay:
            target_cut = seg.start + g.wide_every
            cut = _find_breath_point(
                grid, seg.angle, target_cut, window=min(1.5, g.wide_every * 0.2)
            )
            if cut < seg.start + g.min_shot or seg.end - cut < g.min_shot:
                cut = target_cut
            if seg.end - cut < g.min_shot:
                # Loppu on liian lyhyt omaksi kuvakseen; puhuja jatkaa.
                out.append(seg)
                continue
            out.append(Segment(seg.angle, seg.label, seg.start, cut))
            out.append(Segment(insert_key, insert_label, cut, seg.end))
            continue
        cursor = seg.start
        to_alt = False
        while cursor < seg.end:
            step_len = hold if to_alt else g.wide_every
            target_stop = min(cursor + step_len, seg.end)
            if not to_alt and target_stop < seg.end and grid is not None:
                stop = _find_breath_point(
                    grid, seg.angle, target_stop, window=min(1.5, g.wide_every * 0.2)
                )
                if stop < cursor + g.min_shot or seg.end - stop < g.min_shot:
                    stop = target_stop
            else:
                stop = target_stop

            if seg.end - stop < g.min_shot:
                stop = seg.end
            if to_alt:
                out.append(Segment(insert_key, insert_label, cursor, stop))
            else:
                out.append(Segment(seg.angle, seg.label, cursor, stop))
            cursor = stop
            to_alt = not to_alt
    return _merge(out)


def _merge(segments: list[Segment]) -> list[Segment]:
    """Yhdistää peräkkäiset saman kuvan jaksot ja pudottaa tyhjät."""
    merged: list[Segment] = []
    for seg in segments:
        if seg.end <= seg.start:
            continue
        if merged and merged[-1].angle == seg.angle:
            merged[-1].end = seg.end
        else:
            merged.append(seg)
    return merged


def decide(grid: Grid, g: Globals) -> Decision:
    """Leikkauslista. Tämän on pyörittävä millisekunneissa."""
    want, active = _want_array(grid, g)
    tempo = _compute_tempo(active, grid.n)
    cuts = _cut_points(want, g, tempo=tempo)
    total = grid.duration

    segments: list[Segment] = []
    for index, (at, target) in enumerate(cuts):
        end = cuts[index + 1][0] if index + 1 < len(cuts) else total
        if end <= at:
            continue
        if target == WIDE:
            key, label = grid.wide_key, WIDE_LABEL
        else:
            sp = grid.speakers[target]
            key, label = (sp.close_key or grid.wide_key), sp.name
            if not sp.close_key:
                label = WIDE_LABEL
        segments.append(
            Segment(key, label, grid.program_start + at, grid.program_start + end)
        )
    segments = _merge(segments)
    segments = _force_wide(segments, g, WIDE_LABEL, grid.wide_key, grid=grid)

    # Esikatselua varten: mikä kuva milläkin hetkellä.
    chosen = np.full(grid.n, WIDE, dtype=np.int32)
    key_to_index = {
        sp.close_key: i for i, sp in enumerate(grid.speakers) if sp.close_key
    }
    for seg in segments:
        lo = int(round((seg.start - grid.program_start) / HOP))
        hi = int(round((seg.end - grid.program_start) / HOP))
        chosen[max(0, lo) : max(0, hi)] = key_to_index.get(seg.angle, WIDE)

    return Decision(segments=segments, active=active, chosen=chosen)
