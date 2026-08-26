"""Esikatselun tiivistys.

Palkki piirretään muutamaan tuhanteen pikseliin, ruudukossa on satojatuhansia
askelia. Tiivistys tehdään täällä numpylla, jotta selaimeen ei lähetetä
turhaa dataa eikä JavaScript joudu silmukoimaan koko aineistoa.
"""

from __future__ import annotations

import numpy as np

from .decide import WIDE, Decision, Grid


def _bucket_bounds(n: int, columns: int) -> np.ndarray:
    """Sarakkeiden rajat ruudukon indekseinä, ``columns + 1`` kappaletta."""
    return np.linspace(0, n, columns + 1).astype(np.int64)


def build(grid: Grid, decision: Decision, columns: int = 1400,
          reactions=None, window=None) -> dict:
    """Palauttaa palkin: kuka äänessä ja mikä kuva valittiin, sarakkeittain.

    ``reactions`` on lista ``(alku, loppu, puhujan indeksi)`` ohjelma-ajassa.
    Se tiivistetään samoihin sarakkeisiin kuin muukin, koska rivit luetaan
    päällekkäin: eri jaolla reaktiokuva näyttäisi osuvan väärään kohtaan
    puheen suhteen, ja juuri sitä suhdetta palkista katsotaan.
    """
    n = grid.n
    # Ikkuna ohjelma-ajassa -> ruudukon indeksit. Tiivistys tehdään vain
    # ikkunan yli, jolloin sama sarakemäärä tarkoittaa sitä tarkempaa kuvaa
    # mitä lähemmäs mennään — koko ohjelmassa sarake on 3,3 s, eli lyhyempi
    # leikkaus ei mahdu edes kahteen sarakkeeseen eikä reaktiokuva yhteen.
    step = grid.duration / n if n else 0.0
    first, last = 0, n
    view_start, view_end = grid.program_start, grid.program_start + grid.duration
    if window and step > 0:
        want_start = max(grid.program_start, min(float(window[0]), view_end))
        want_end = min(view_end, max(float(window[1]), want_start + step))
        first = int((want_start - grid.program_start) / step)
        last = int(round((want_end - grid.program_start) / step))
        first = max(0, min(first, max(0, n - 1)))
        last = max(first + 1, min(last, n))
        view_start = grid.program_start + first * step
        view_end = grid.program_start + last * step

    span = last - first
    columns = max(1, min(columns, max(1, span)))
    bounds = _bucket_bounds(span, columns) + first
    mids = np.clip((bounds[:-1] + bounds[1:]) // 2, 0, max(0, n - 1))

    speakers = []
    for index, lane in enumerate(grid.speakers):
        # Sarake on "äänessä", jos puhuja on äänessä missä tahansa sen sisällä:
        # muuten lyhyet repliikit katoaisivat tiivistyksessä.
        cumulative = np.concatenate(([0], np.cumsum(lane.on, dtype=np.int64)))
        hits = cumulative[bounds[1:]] - cumulative[bounds[:-1]]
        speakers.append(
            {
                "name": lane.name,
                "index": index,
                "has_close": bool(lane.close_key),
                "active": (hits > 0).astype(np.uint8).tolist(),
            }
        )

    # Reaktiokuvat omalle rivilleen: -1 = ei mitään, -2 = laaja, muuten
    # puhujan indeksi. Reaktiokuva ei aina näytä sitä kasvoa joka mitattiin.
    # Ne ovat lyhyitä — sekunnin luokkaa — joten sarake merkitään heti kun
    # yksikin osuu siihen, kuten puhujariveilläkin. Muuten koko kerros
    # katoaisi tiivistyksessä juuri niiltä kohdin jotka halutaan nähdä.
    lane = [-1] * columns
    if reactions:
        seconds = (view_end - view_start) or 1.0
        for start, end, index in reactions:
            low = int((start - view_start) / seconds * columns)
            high = int((end - view_start) / seconds * columns)
            for column in range(max(0, low), min(columns, max(high, low + 1))):
                lane[column] = int(index)

    chosen = decision.chosen[mids] if n else np.zeros(0, dtype=np.int32)
    return {
        "columns": columns,
        "reactions": lane,
        "duration": grid.duration,
        "program_start": grid.program_start,
        # Näkymän rajat: viivain piirtää näistä, ei ohjelman kokonaiskestosta.
        "view_start": float(view_start),
        "view_end": float(view_end),
        "speakers": speakers,
        # -2 = laaja, 0.. = puhujan indeksi
        "chosen": [int(v) for v in chosen],
        "wide_value": WIDE,
    }
