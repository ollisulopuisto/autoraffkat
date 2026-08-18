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

from .model import (HOP, OVERLAP_HOLD, OVERLAP_LOUDER, OVERLAP_WIDE, Globals,
                    Segment)

WIDE = -2       # want-taulukon erikoisarvot
HOLD = -1

WIDE_LABEL = "Laaja"


# ------------------------------------------------------------------ apurit


def _runs(values: np.ndarray) -> list[tuple[int, int, int]]:
    """Jaksot (alku, loppu, arvo). Loppu on poissulkeva."""
    if values.size == 0:
        return []
    change = np.flatnonzero(values[1:] != values[:-1]) + 1
    bounds = np.concatenate(([0], change, [values.size]))
    return [(int(bounds[i]), int(bounds[i + 1]), int(values[bounds[i]]))
            for i in range(bounds.size - 1)]


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


def _hops(seconds: float) -> int:
    return max(1, int(round(seconds / HOP)))


# ------------------------------------------------------------------ syöte


@dataclass
class SpeakerLanes:
    """Yhden puhujan aineisto ruudukolla."""

    name: str
    level: np.ndarray          # dB, vahvistuskorjaus jo mukana
    on: np.ndarray             # bool, kynnyksen ylitys
    close_key: str | None      # lähikuvan media key, None jos ei lähikuvaa
    available: np.ndarray | None = None   # missä lähikuva on olemassa


@dataclass
class Grid:
    """Päätöskerroksen syöte: kaikki ruudukolle kohdistettuna."""

    n: int                     # ruudukon pituus (HOP-askelta)
    program_start: float       # aikajanan sekunneissa
    speakers: list[SpeakerLanes] = field(default_factory=list)
    wide_key: str = ""

    @property
    def duration(self) -> float:
        return self.n * HOP


@dataclass
class Decision:
    segments: list[Segment]
    active: np.ndarray         # (puhujia, n) bool — esikatselupalkkia varten
    chosen: np.ndarray         # (n,) int — puhujan indeksi tai WIDE


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


def _cut_points(want: np.ndarray, g: Globals) -> list[tuple[float, int]]:
    """Kestorajoitukset: vahvistusaika, ennakko, lyhin kuvan kesto."""
    confirm = _hops(g.confirm)
    current = WIDE
    cuts: list[tuple[float, int]] = [(0.0, WIDE)]
    last_cut = -g.min_shot

    for start, end, target in _runs(want):
        if target == HOLD or target == current:
            continue
        if (end - start) < confirm:
            continue
        at = max(start * HOP - g.lead, last_cut + g.min_shot, 0.0)
        if at >= end * HOP:
            continue          # ennakko ja minimikesto söivät koko jakson
        cuts.append((at, target))
        current = target
        last_cut = at
    return cuts


def _force_wide(segments: list[Segment], g: Globals, wide_label: str,
                wide_key: str) -> list[Segment]:
    """Pilkkoo pitkät lähikuvat vuorotellen laajaan."""
    if g.wide_every <= 0 or not wide_key:
        return segments
    out: list[Segment] = []
    for seg in segments:
        if seg.angle == wide_key or seg.duration <= g.wide_every:
            out.append(seg)
            continue
        cursor = seg.start
        to_wide = False
        while cursor < seg.end:
            stop = min(cursor + g.wide_every, seg.end)
            if seg.end - stop < g.min_shot:
                stop = seg.end
            if to_wide:
                out.append(Segment(wide_key, wide_label, cursor, stop))
            else:
                out.append(Segment(seg.angle, seg.label, cursor, stop))
            cursor = stop
            to_wide = not to_wide
    return _merge(out)


def _merge(segments: list[Segment]) -> list[Segment]:
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
    cuts = _cut_points(want, g)
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
        segments.append(Segment(key, label, grid.program_start + at,
                                grid.program_start + end))
    segments = _merge(segments)
    segments = _force_wide(segments, g, WIDE_LABEL, grid.wide_key)

    # Esikatselua varten: mikä kuva milläkin hetkellä.
    chosen = np.full(grid.n, WIDE, dtype=np.int32)
    key_to_index = {sp.close_key: i for i, sp in enumerate(grid.speakers)
                    if sp.close_key}
    for seg in segments:
        lo = int(round((seg.start - grid.program_start) / HOP))
        hi = int(round((seg.end - grid.program_start) / HOP))
        chosen[max(0, lo):max(0, hi)] = key_to_index.get(seg.angle, WIDE)

    return Decision(segments=segments, active=active, chosen=chosen)
