"""Päätöskerroksen säännöt. Puhdasta numpyta — ei ffmpegiä eikä tiedostoja."""

import numpy as np
import pytest

from autoraffkat import decide as decide_mod
from autoraffkat.decide import HOLD, WIDE, Grid, SpeakerLanes, decide
from autoraffkat.model import (HOP, LONGTAKE_RETURN, LONGTAKE_STAY,
                               OVERLAP_HOLD, OVERLAP_LOUDER, OVERLAP_WIDE,
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
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.4,
                wide_every=0.0)
    d = decide(grid_for([(2, 8)], [(10, 16)]), g)
    assert angles(d.segments) == [(0.0, "W"), (2.0, "CA"), (10.0, "CB")]


def test_lead_cuts_early():
    g = Globals(min_shot=1.0, lead=0.5, confirm=0.2, min_overlap=0.4,
                wide_every=0.0)
    d = decide(grid_for([(5, 10)], []), g)
    assert angles(d.segments)[1] == (4.5, "CA")


def test_min_shot_blocks_rapid_cutting():
    """Nopea vuorottelu ei saa tuottaa kuvaa alle vähimmäiskeston."""
    spans_a = [(2, 3), (6, 7), (10, 11)]
    spans_b = [(4, 5), (8, 9), (12, 13)]
    g = Globals(min_shot=5.0, lead=0.0, confirm=0.2, min_overlap=0.4,
                wide_every=0.0)
    d = decide(grid_for(spans_a, spans_b), g)
    for seg in d.segments[1:-1]:
        assert seg.duration >= 5.0 - 1e-6


def test_confirm_ignores_short_bursts():
    g = Globals(min_shot=1.0, lead=0.0, confirm=1.0, min_overlap=0.4,
                wide_every=0.0)
    d = decide(grid_for([(5, 5.4)], []), g)          # 0,4 s < vahvistusaika
    assert angles(d.segments) == [(0.0, "W")]


def test_overlap_wide():
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.5,
                overlap_rule=OVERLAP_WIDE, wide_every=0.0)
    d = decide(grid_for([(2, 12)], [(6, 12)]), g)
    labels = [s.angle for s in d.segments]
    assert labels == ["W", "CA", "W"]
    assert d.segments[2].start == pytest.approx(6.0, abs=0.05)


def test_overlap_hold_stays_put():
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.5,
                overlap_rule=OVERLAP_HOLD, wide_every=0.0)
    d = decide(grid_for([(2, 12)], [(6, 12)]), g)
    assert [s.angle for s in d.segments] == ["W", "CA"]


def test_overlap_louder_needs_dominance():
    """Vahvempi voittaa vain kun ero ylittää vaaditun desibelimäärän."""
    weak = decide(grid_for([(2, 12)], [(6, 12)], level_a=-30.0, level_b=-28.0),
                  Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.5,
                          overlap_rule=OVERLAP_LOUDER, dominance_db=6.0,
                          wide_every=0.0))
    assert [s.angle for s in weak.segments] == ["W", "CA"]

    strong = decide(grid_for([(2, 12)], [(6, 12)], level_a=-30.0, level_b=-20.0),
                    Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.5,
                            overlap_rule=OVERLAP_LOUDER, dominance_db=6.0,
                            wide_every=0.0))
    assert [s.angle for s in strong.segments] == ["W", "CA", "CB"]


def test_brief_backchannel_does_not_trigger_overlap():
    """Ohikiitävä myötäily ei saa viedä laajaan."""
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=1.0,
                overlap_rule=OVERLAP_WIDE, wide_every=0.0)
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
    d = decide(grid, Globals(min_shot=1.0, lead=0.0, confirm=0.2,
                             wide_every=0.0))
    assert [s.angle for s in d.segments] == ["W"]


def test_unavailable_closeup_is_skipped():
    n = int(20 / HOP)
    on = np.zeros(n, dtype=bool); on[int(2 / HOP):int(9 / HOP)] = True
    db = np.full(n, -60.0, dtype=np.float32); db[on] = -30.0
    avail = np.zeros(n, dtype=bool)          # lähikuvaa ei ole missään
    grid = Grid(n=n, program_start=0.0, wide_key="W",
                speakers=[SpeakerLanes("A", db, on, "CA", avail)])
    d = decide(grid, Globals(min_shot=1.0, lead=0.0, confirm=0.2,
                             wide_every=0.0))
    assert [s.angle for s in d.segments] == ["W"]


def test_program_start_offsets_segments():
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, wide_every=0.0)
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


# ------------------------------------------------- pitkä puheenvuoro


def _long_take(rule, wide_every=5.0, wide_hold=2.0, min_shot=1.0):
    """A puhuu yksin 2–40 s: yksi pitkä lähikuva, joka on katkaistava."""
    g = Globals(min_shot=min_shot, lead=0.0, confirm=0.2,
                wide_every=wide_every, wide_hold=wide_hold,
                long_take_rule=rule)
    return decide(grid_for([(2, 40)], [], seconds=40.0), g)


def test_long_take_returns_to_the_speaker():
    """«Palaa puhujaan»: laaja välissä, sitten takaisin samaan lähikuvaan."""
    d = _long_take(LONGTAKE_RETURN)
    tail = [s.angle for s in d.segments[1:]]
    assert tail[:4] == ["CA", "W", "CA", "W"]
    # Laajan kesto on wide_hold, lähikuvan wide_every.
    assert d.segments[2].duration == pytest.approx(2.0, abs=0.05)
    assert d.segments[1].duration == pytest.approx(5.0, abs=0.05)


def test_long_take_can_stay_wide():
    """«Jää laajaan»: yksi katkaisu, ja laaja jatkuu puhujan vaihtoon asti."""
    d = _long_take(LONGTAKE_STAY)
    assert [s.angle for s in d.segments] == ["W", "CA", "W"]
    assert d.segments[1].duration == pytest.approx(5.0, abs=0.05)
    assert d.segments[2].end == pytest.approx(40.0, abs=0.05)


def test_staying_wide_ends_at_the_next_speaker():
    """Laaja ei syö seuraavan puhujan lähikuvaa.

    Viimeinen jakso jatkuu aina ruudukon loppuun, joten sekin katkeaa
    laajaan — mutta vasta oman kynnyksensä jälkeen.
    """
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.4,
                wide_every=5.0, long_take_rule=LONGTAKE_STAY)
    d = decide(grid_for([(2, 20)], [(22, 38)]), g)
    assert [s.angle for s in d.segments] == ["W", "CA", "W", "CB", "W"]
    assert d.segments[2].end == pytest.approx(22.0, abs=0.1)
    assert d.segments[3].start == pytest.approx(22.0, abs=0.1)
    assert d.segments[3].duration == pytest.approx(5.0, abs=0.05)


def test_wide_hold_never_goes_under_min_shot():
    """Liian lyhyt laaja olisi välähdys; vähimmäiskesto voittaa."""
    d = _long_take(LONGTAKE_RETURN, wide_hold=0.1, min_shot=1.5)
    for seg in d.segments[1:-1]:
        assert seg.duration >= 1.5 - 1e-6


def test_zero_never_forces_a_wide():
    d = _long_take(LONGTAKE_RETURN, wide_every=0.0)
    assert [s.angle for s in d.segments] == ["W", "CA"]


def test_short_turns_are_left_alone():
    """Alle kynnyksen jäävää puheenvuoroa ei katkaista kummallakaan säännöllä."""
    for rule in (LONGTAKE_RETURN, LONGTAKE_STAY):
        g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.4,
                    wide_every=15.0, long_take_rule=rule)
        d = decide(grid_for([(2, 8)], [(10, 16)]), g)
        # A:n vuoro on 8 s eli alle kynnyksen; se jää yhdeksi kuvaksi.
        assert [s.angle for s in d.segments[:3]] == ["W", "CA", "CB"], rule
        assert d.segments[1].duration == pytest.approx(8.0, abs=0.05), rule


# ------------------------------------------------- mikin vaimennus


def mask_from(spans, seconds=20.0):
    n = int(seconds / HOP)
    out = np.zeros(n, dtype=bool)
    for start, end in spans:
        out[int(start / HOP):int(end / HOP)] = True
    return out


def spans_of(mask):
    """Maskin todet jaksot sekunteina, luettavuuden vuoksi."""
    from autoraffkat.decide import _runs
    return [(round(a * HOP, 2), round(b * HOP, 2))
            for a, b, v in _runs(mask.astype(np.int8)) if v]


def test_open_windows_drops_a_cough():
    """Yksittäinen yskäisy ei saa avata mikkiä."""
    mask = mask_from([(5.0, 5.08), (10.0, 12.0)])
    out = decide_mod.open_windows(mask, lookahead=0.0, hold=0.0, min_open=0.2)
    assert spans_of(out) == [(10.0, 12.0)]


def test_open_windows_opens_early_and_holds():
    """Ennakko pelastaa sanan alun, pito lauseen hännän."""
    mask = mask_from([(10.0, 12.0)])
    out = decide_mod.open_windows(mask, lookahead=0.15, hold=0.4, min_open=0.0)
    start, end = spans_of(out)[0]
    assert start == pytest.approx(9.85, abs=0.02)
    assert end == pytest.approx(12.4, abs=0.02)


def test_open_windows_merges_words_across_a_pause():
    """Sanaväli ei saa sulkea porttia, jos pito kattaa sen."""
    mask = mask_from([(10.0, 10.5), (10.7, 11.5)])
    out = decide_mod.open_windows(mask, lookahead=0.15, hold=0.4, min_open=0.0)
    assert len(spans_of(out)) == 1


def test_open_windows_without_knobs_is_the_input():
    mask = mask_from([(3.0, 4.0)])
    out = decide_mod.open_windows(mask, lookahead=0.0, hold=0.0, min_open=0.0)
    assert np.array_equal(out, mask)
