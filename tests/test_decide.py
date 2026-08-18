"""Päätöskerroksen säännöt. Puhdasta numpyta — ei ffmpegiä eikä tiedostoja."""

import numpy as np
import pytest

from autoraffkat.decide import HOLD, WIDE, Grid, SpeakerLanes, decide
from autoraffkat.model import (HOP, OVERLAP_HOLD, OVERLAP_LOUDER, OVERLAP_WIDE,
                               Globals)


def lanes(spans_a, spans_b, n, level_a=-30.0, level_b=-30.0):
    def make(spans, level, name, key):
        on = np.zeros(n, dtype=bool)
        db = np.full(n, -60.0, dtype=np.float32)
        for start, end in spans:
            i0, i1 = int(start / HOP), int(end / HOP)
            on[i0:i1] = True
            db[i0:i1] = level
        return SpeakerLanes(name, db, on, key)
    return [make(spans_a, level_a, "A", "CA"), make(spans_b, level_b, "B", "CB")]


def grid_for(spans_a, spans_b, seconds=40.0, **kw):
    n = int(seconds / HOP)
    return Grid(n=n, program_start=0.0,
                speakers=lanes(spans_a, spans_b, n, **kw), wide_key="W")


def angles(segments):
    return [(round(s.start, 2), s.angle) for s in segments]


def test_simple_alternation():
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.4)
    d = decide(grid_for([(2, 8)], [(10, 16)]), g)
    assert angles(d.segments) == [(0.0, "W"), (2.0, "CA"), (10.0, "CB")]


def test_lead_cuts_early():
    g = Globals(min_shot=1.0, lead=0.5, confirm=0.2, min_overlap=0.4)
    d = decide(grid_for([(5, 10)], []), g)
    assert angles(d.segments)[1] == (4.5, "CA")


def test_min_shot_blocks_rapid_cutting():
    """Nopea vuorottelu ei saa tuottaa kuvaa alle vähimmäiskeston."""
    spans_a = [(2, 3), (6, 7), (10, 11)]
    spans_b = [(4, 5), (8, 9), (12, 13)]
    g = Globals(min_shot=5.0, lead=0.0, confirm=0.2, min_overlap=0.4)
    d = decide(grid_for(spans_a, spans_b), g)
    for seg in d.segments[1:-1]:
        assert seg.duration >= 5.0 - 1e-6


def test_confirm_ignores_short_bursts():
    g = Globals(min_shot=1.0, lead=0.0, confirm=1.0, min_overlap=0.4)
    d = decide(grid_for([(5, 5.4)], []), g)          # 0,4 s < vahvistusaika
    assert angles(d.segments) == [(0.0, "W")]


def test_overlap_wide():
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.5,
                overlap_rule=OVERLAP_WIDE)
    d = decide(grid_for([(2, 12)], [(6, 12)]), g)
    labels = [s.angle for s in d.segments]
    assert labels == ["W", "CA", "W"]
    assert d.segments[2].start == pytest.approx(6.0, abs=0.05)


def test_overlap_hold_stays_put():
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.5,
                overlap_rule=OVERLAP_HOLD)
    d = decide(grid_for([(2, 12)], [(6, 12)]), g)
    assert [s.angle for s in d.segments] == ["W", "CA"]


def test_overlap_louder_needs_dominance():
    """Vahvempi voittaa vain kun ero ylittää vaaditun desibelimäärän."""
    weak = decide(grid_for([(2, 12)], [(6, 12)], level_a=-30.0, level_b=-28.0),
                  Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.5,
                          overlap_rule=OVERLAP_LOUDER, dominance_db=6.0))
    assert [s.angle for s in weak.segments] == ["W", "CA"]

    strong = decide(grid_for([(2, 12)], [(6, 12)], level_a=-30.0, level_b=-20.0),
                    Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.5,
                            overlap_rule=OVERLAP_LOUDER, dominance_db=6.0))
    assert [s.angle for s in strong.segments] == ["W", "CA", "CB"]


def test_brief_backchannel_does_not_trigger_overlap():
    """Ohikiitävä myötäily ei saa viedä laajaan."""
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=1.0,
                overlap_rule=OVERLAP_WIDE)
    d = decide(grid_for([(2, 12)], [(6, 6.3)], level_a=-25.0, level_b=-40.0), g)
    assert [s.angle for s in d.segments] == ["W", "CA"]


def test_wide_every_alternates():
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, wide_every=5.0)
    d = decide(grid_for([(2, 40)], [], seconds=40.0), g)
    tail = [s.angle for s in d.segments[1:]]
    assert tail[0] == "CA" and "W" in tail
    assert all(s.duration >= 1.0 - 1e-6 for s in d.segments)


def test_segments_are_contiguous_and_cover_program():
    g = Globals(min_shot=1.5, lead=0.2, confirm=0.3, wide_every=7.0)
    d = decide(grid_for([(2, 9), (20, 30)], [(11, 18), (31, 38)]), g)
    assert d.segments[0].start == 0.0
    assert d.segments[-1].end == pytest.approx(40.0, abs=0.02)
    for a, b in zip(d.segments, d.segments[1:]):
        assert a.end == b.start


def test_speaker_without_closeup_falls_back_to_wide():
    n = int(20 / HOP)
    on = np.zeros(n, dtype=bool); on[int(2 / HOP):int(9 / HOP)] = True
    db = np.full(n, -60.0, dtype=np.float32); db[on] = -30.0
    grid = Grid(n=n, program_start=0.0, wide_key="W",
                speakers=[SpeakerLanes("A", db, on, None)])
    d = decide(grid, Globals(min_shot=1.0, lead=0.0, confirm=0.2))
    assert [s.angle for s in d.segments] == ["W"]


def test_unavailable_closeup_is_skipped():
    n = int(20 / HOP)
    on = np.zeros(n, dtype=bool); on[int(2 / HOP):int(9 / HOP)] = True
    db = np.full(n, -60.0, dtype=np.float32); db[on] = -30.0
    avail = np.zeros(n, dtype=bool)          # lähikuvaa ei ole missään
    grid = Grid(n=n, program_start=0.0, wide_key="W",
                speakers=[SpeakerLanes("A", db, on, "CA", avail)])
    d = decide(grid, Globals(min_shot=1.0, lead=0.0, confirm=0.2))
    assert [s.angle for s in d.segments] == ["W"]


def test_program_start_offsets_segments():
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2)
    grid = grid_for([(2, 8)], [])
    grid.program_start = 100.0
    d = decide(grid, g)
    assert d.segments[0].start == 100.0
    assert d.segments[1].start == pytest.approx(102.0)


def test_two_hours_is_fast():
    import time
    n = int(2 * 3600 / HOP)
    rng = np.random.default_rng(1)
    speakers = []
    for i in range(3):
        on = np.zeros(n, dtype=bool)
        t = i * 100
        while t < n:
            length = int(rng.integers(60, 500))
            on[t:t + length] = True
            t += length + int(rng.integers(60, 800))
        db = np.where(on, -28.0, -60.0).astype(np.float32)
        speakers.append(SpeakerLanes(f"S{i}", db, on, f"C{i}"))
    grid = Grid(n=n, program_start=0.0, speakers=speakers, wide_key="W")
    g = Globals(min_shot=2.5, lead=0.15, confirm=0.4)
    decide(grid, g)                                   # lämmitys
    started = time.perf_counter()
    decide(grid, g)
    elapsed = (time.perf_counter() - started) * 1000
    assert elapsed < 250, f"päätös kesti {elapsed:.0f} ms"
