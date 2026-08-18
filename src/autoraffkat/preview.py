"""Esikatselun tiivistys.

Palkki piirretään muutamaan tuhanteen pikseliin, ruudukossa on satojatuhansia
askelia. Tiivistys tehdään täällä numpylla, jotta selaimeen ei lähetetä
turhaa dataa eikä JavaScript joudu silmukoimaan koko aineistoa.
"""

from __future__ import annotations

import numpy as np

from .decide import WIDE, Decision, Grid


def _bucket_bounds(n: int, columns: int) -> np.ndarray:
    return np.linspace(0, n, columns + 1).astype(np.int64)


def build(grid: Grid, decision: Decision, columns: int = 1400) -> dict:
    """Palauttaa palkin: kuka äänessä ja mikä kuva valittiin, sarakkeittain."""
    n = grid.n
    columns = max(1, min(columns, max(1, n)))
    bounds = _bucket_bounds(n, columns)
    mids = np.clip((bounds[:-1] + bounds[1:]) // 2, 0, max(0, n - 1))

    speakers = []
    for index, lane in enumerate(grid.speakers):
        # Sarake on "äänessä", jos puhuja on äänessä missä tahansa sen sisällä:
        # muuten lyhyet repliikit katoaisivat tiivistyksessä.
        cumulative = np.concatenate(([0], np.cumsum(lane.on, dtype=np.int64)))
        hits = cumulative[bounds[1:]] - cumulative[bounds[:-1]]
        speakers.append({
            "name": lane.name,
            "index": index,
            "has_close": bool(lane.close_key),
            "active": (hits > 0).astype(np.uint8).tolist(),
        })

    chosen = decision.chosen[mids] if n else np.zeros(0, dtype=np.int32)
    return {
        "columns": columns,
        "duration": grid.duration,
        "program_start": grid.program_start,
        "speakers": speakers,
        # -2 = laaja, 0.. = puhujan indeksi
        "chosen": [int(v) for v in chosen],
        "wide_value": WIDE,
    }
