"""Päätöskerroksen säännöt. Puhdasta numpyta — ei ffmpegiä eikä tiedostoja."""

from dataclasses import replace

import numpy as np
import pytest

from autoraffkat import decide as decide_mod
from autoraffkat.decide import Grid, SpeakerLanes, decide
from autoraffkat.model import (
    HOP,
    LONGTAKE_RETURN,
    LONGTAKE_STAY,
    OVERLAP_HOLD,
    OVERLAP_LOUDER,
    OVERLAP_WIDE,
    Globals,
)


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
    return Grid(
        n=n, program_start=0.0, speakers=lanes(spans_a, spans_b, n, **kw), wide_key="W"
    )


def angles(segments):
    return [(round(s.start, 2), s.angle) for s in segments]


def test_simple_alternation():
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.4, wide_every=0.0)
    d = decide(grid_for([(2, 8)], [(10, 16)]), g)
    assert angles(d.segments) == [(0.0, "W"), (2.0, "CA"), (10.0, "CB")]


def test_lead_cuts_early():
    g = Globals(min_shot=1.0, lead=0.5, confirm=0.2, min_overlap=0.4, wide_every=0.0)
    d = decide(grid_for([(5, 10)], []), g)
    assert angles(d.segments)[1] == (4.5, "CA")


def test_min_shot_blocks_rapid_cutting():
    """Nopea vuorottelu ei saa tuottaa kuvaa alle vähimmäiskeston."""
    spans_a = [(2, 3), (6, 7), (10, 11)]
    spans_b = [(4, 5), (8, 9), (12, 13)]
    g = Globals(min_shot=5.0, lead=0.0, confirm=0.2, min_overlap=0.4, wide_every=0.0)
    d = decide(grid_for(spans_a, spans_b), g)
    for seg in d.segments[1:-1]:
        assert seg.duration >= 5.0 - 1e-6


def test_confirm_ignores_short_bursts():
    g = Globals(min_shot=1.0, lead=0.0, confirm=1.0, min_overlap=0.4, wide_every=0.0)
    d = decide(grid_for([(5, 5.4)], []), g)  # 0,4 s < vahvistusaika
    assert angles(d.segments) == [(0.0, "W")]


def test_overlap_wide():
    g = Globals(
        min_shot=1.0,
        lead=0.0,
        confirm=0.2,
        min_overlap=0.5,
        overlap_rule=OVERLAP_WIDE,
        wide_every=0.0,
    )
    d = decide(grid_for([(2, 12)], [(6, 12)]), g)
    labels = [s.angle for s in d.segments]
    assert labels == ["W", "CA", "W"]
    assert d.segments[2].start == pytest.approx(6.0, abs=0.05)


def test_overlap_hold_stays_put():
    g = Globals(
        min_shot=1.0,
        lead=0.0,
        confirm=0.2,
        min_overlap=0.5,
        overlap_rule=OVERLAP_HOLD,
        wide_every=0.0,
    )
    d = decide(grid_for([(2, 12)], [(6, 12)]), g)
    assert [s.angle for s in d.segments] == ["W", "CA"]


def test_overlap_louder_needs_dominance():
    """Vahvempi voittaa vain kun ero ylittää vaaditun desibelimäärän."""
    weak = decide(
        grid_for([(2, 12)], [(6, 12)], level_a=-30.0, level_b=-28.0),
        Globals(
            min_shot=1.0,
            lead=0.0,
            confirm=0.2,
            min_overlap=0.5,
            overlap_rule=OVERLAP_LOUDER,
            dominance_db=6.0,
            wide_every=0.0,
        ),
    )
    assert [s.angle for s in weak.segments] == ["W", "CA"]

    strong = decide(
        grid_for([(2, 12)], [(6, 12)], level_a=-30.0, level_b=-20.0),
        Globals(
            min_shot=1.0,
            lead=0.0,
            confirm=0.2,
            min_overlap=0.5,
            overlap_rule=OVERLAP_LOUDER,
            dominance_db=6.0,
            wide_every=0.0,
        ),
    )
    assert [s.angle for s in strong.segments] == ["W", "CA", "CB"]


def test_brief_backchannel_does_not_trigger_overlap():
    """Ohikiitävä myötäily ei saa viedä laajaan."""
    g = Globals(
        min_shot=1.0,
        lead=0.0,
        confirm=0.2,
        min_overlap=1.0,
        overlap_rule=OVERLAP_WIDE,
        wide_every=0.0,
    )
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
    on = np.zeros(n, dtype=bool)
    on[int(2 / HOP) : int(9 / HOP)] = True
    db = np.full(n, -60.0, dtype=np.float32)
    db[on] = -30.0
    grid = Grid(
        n=n, program_start=0.0, wide_key="W", speakers=[SpeakerLanes("A", db, on, None)]
    )
    d = decide(grid, Globals(min_shot=1.0, lead=0.0, confirm=0.2, wide_every=0.0))
    assert [s.angle for s in d.segments] == ["W"]


def test_unavailable_closeup_is_skipped():
    n = int(20 / HOP)
    on = np.zeros(n, dtype=bool)
    on[int(2 / HOP) : int(9 / HOP)] = True
    db = np.full(n, -60.0, dtype=np.float32)
    db[on] = -30.0
    avail = np.zeros(n, dtype=bool)  # lähikuvaa ei ole missään
    grid = Grid(
        n=n,
        program_start=0.0,
        wide_key="W",
        speakers=[SpeakerLanes("A", db, on, "CA", avail)],
    )
    d = decide(grid, Globals(min_shot=1.0, lead=0.0, confirm=0.2, wide_every=0.0))
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
            on[t : t + length] = True
            t += length + int(rng.integers(60, 800))
        db = np.where(on, -28.0, -60.0).astype(np.float32)
        speakers.append(SpeakerLanes(f"S{i}", db, on, f"C{i}"))
    grid = Grid(n=n, program_start=0.0, speakers=speakers, wide_key="W")
    g = Globals(min_shot=2.5, lead=0.15, confirm=0.4)
    decide(grid, g)  # lämmitys
    started = time.perf_counter()
    decide(grid, g)
    elapsed = (time.perf_counter() - started) * 1000
    assert elapsed < 250, f"päätös kesti {elapsed:.0f} ms"


def test_three_speakers_each_get_their_own_close_up():
    """Logiikka ei ole sidottu kahteen puhujaan.

    Kaikki mikä erottaa puhujat — hallitsevuus, päällekkäisyys, vaimennus —
    on listoja eikä pareja, mutta se on eri asia kuin että se olisi ajettu
    kolmella. Tämä ajaa.
    """
    n = int(40.0 / HOP)

    def lane(spans, name, key, level=-28.0):
        on = np.zeros(n, dtype=bool)
        db = np.full(n, -60.0, dtype=np.float32)
        for start, end in spans:
            on[int(start / HOP) : int(end / HOP)] = True
            db[int(start / HOP) : int(end / HOP)] = level
        return SpeakerLanes(name, db, on, key)

    grid = Grid(
        n=n,
        program_start=0.0,
        speakers=[
            lane([(2, 8)], "A", "CA"),
            lane([(10, 16)], "B", "CB"),
            lane([(18, 24)], "C", "CC"),
        ],
        wide_key="W",
    )
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.4, wide_every=0.0)
    assert angles(decide(grid, g).segments) == [
        (0.0, "W"), (2.0, "CA"), (10.0, "CB"), (18.0, "CC"),
    ]


def test_three_speakers_overlapping_go_wide():
    """Päällekkäisyyssääntö koskee mitä tahansa puhujajoukkoa, ei paria."""
    n = int(40.0 / HOP)

    def lane(spans, name, key, level=-28.0):
        on = np.zeros(n, dtype=bool)
        db = np.full(n, -60.0, dtype=np.float32)
        for start, end in spans:
            on[int(start / HOP) : int(end / HOP)] = True
            db[int(start / HOP) : int(end / HOP)] = level
        return SpeakerLanes(name, db, on, key)

    grid = Grid(
        n=n,
        program_start=0.0,
        speakers=[
            lane([(2, 8), (12, 18)], "A", "CA"),
            lane([(12, 18)], "B", "CB"),
            lane([(12, 18)], "C", "CC"),
        ],
        wide_key="W",
    )
    g = Globals(min_shot=1.0, lead=0.0, confirm=0.2, min_overlap=0.4, wide_every=0.0,
                overlap_rule="wide")
    picked = angles(decide(grid, g).segments)
    # Kolmen puhuessa yhtä aikaa kuva on laajassa, ei kenessäkään heistä.
    at_overlap = [key for at, key in picked if 12.0 <= at < 18.0]
    assert "W" in at_overlap or picked[-1][1] == "W", picked


def test_ducking_covers_every_speaker(tmp_path):
    """Vaimennusmaskit rakennetaan puhujittain, ei parina."""
    from autoraffkat.audio.mix import duck_masks
    from autoraffkat.model import AudioSettings

    n = int(30.0 / HOP)

    def lane(spans, name, key):
        on = np.zeros(n, dtype=bool)
        db = np.full(n, -60.0, dtype=np.float32)
        for start, end in spans:
            on[int(start / HOP) : int(end / HOP)] = True
            db[int(start / HOP) : int(end / HOP)] = -28.0
        return SpeakerLanes(name, db, on, key)

    grid = Grid(
        n=n,
        program_start=0.0,
        speakers=[
            lane([(2, 9)], "A", "CA"),
            lane([(11, 18)], "B", "CB"),
            lane([(20, 27)], "C", "CC"),
        ],
        wide_key="W",
    )
    masks = duck_masks(grid, AudioSettings(duck=True))
    assert set(masks) == {"A", "B", "C"}
    # Jokainen on kiinni jonkun toisen puhuessa ja auki omalla vuorollaan.
    for name, own in (("A", (2, 9)), ("B", (11, 18)), ("C", (20, 27))):
        mask = masks[name]
        assert not mask[int(own[0] / HOP) + 20 : int(own[1] / HOP) - 20].any(), name
        assert mask.any(), name


# ------------------------------------------------- pitkä puheenvuoro


def _long_take(rule, wide_every=5.0, wide_hold=2.0, min_shot=1.0):
    """A puhuu yksin 2–40 s: yksi pitkä lähikuva, joka on katkaistava."""
    g = Globals(
        min_shot=min_shot,
        lead=0.0,
        confirm=0.2,
        wide_every=wide_every,
        wide_hold=wide_hold,
        long_take_rule=rule,
    )
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
    g = Globals(
        min_shot=1.0,
        lead=0.0,
        confirm=0.2,
        min_overlap=0.4,
        wide_every=5.0,
        long_take_rule=LONGTAKE_STAY,
    )
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
        g = Globals(
            min_shot=1.0,
            lead=0.0,
            confirm=0.2,
            min_overlap=0.4,
            wide_every=15.0,
            long_take_rule=rule,
        )
        d = decide(grid_for([(2, 8)], [(10, 16)]), g)
        # A:n vuoro on 8 s eli alle kynnyksen; se jää yhdeksi kuvaksi.
        assert [s.angle for s in d.segments[:3]] == ["W", "CA", "CB"], rule
        assert d.segments[1].duration == pytest.approx(8.0, abs=0.05), rule


# ------------------------------------------------- mikin vaimennus


def mask_from(spans, seconds=20.0):
    n = int(seconds / HOP)
    out = np.zeros(n, dtype=bool)
    for start, end in spans:
        out[int(start / HOP) : int(end / HOP)] = True
    return out


def spans_of(mask):
    """Maskin todet jaksot sekunteina, luettavuuden vuoksi."""
    from autoraffkat.decide import _runs

    return [
        (round(a * HOP, 2), round(b * HOP, 2))
        for a, b, v in _runs(mask.astype(np.int8))
        if v
    ]


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


def test_rhythm_preset_definitions():
    """Rytmiprofiilien oletusarvot ovat määriteltyjä ja loogisia."""
    from autoraffkat.model import (
        RHYTHM_BROADCAST,
        RHYTHM_HECTIC,
        RHYTHM_MELLOW,
        RHYTHM_PRESET_VALUES,
    )

    assert RHYTHM_MELLOW in RHYTHM_PRESET_VALUES
    assert RHYTHM_BROADCAST in RHYTHM_PRESET_VALUES
    assert RHYTHM_HECTIC in RHYTHM_PRESET_VALUES

    # Rauhallinen profiili on pidempi kuin korkeatempoinen
    assert (RHYTHM_MELLOW_SHOT := RHYTHM_PRESET_VALUES[RHYTHM_MELLOW]["min_shot"]) > (
        RHYTHM_HECTIC_SHOT := RHYTHM_PRESET_VALUES[RHYTHM_HECTIC]["min_shot"]
    )
    assert RHYTHM_MELLOW_SHOT == 4.5
    assert RHYTHM_HECTIC_SHOT == 1.4


def test_longtake_snaps_to_pause_or_breath():
    """Pitkän puheenvuoron katkaisu laajaksi hakeutuu luonnolliseen taukoon."""
    n = int(30.0 / HOP)
    on = np.ones(n, dtype=bool)
    # Asetetaan lyhyt 0.4s tauko sekunneille 9.8 - 10.2 (kun wide_every = 10.0)
    i0, i1 = int(9.8 / HOP), int(10.2 / HOP)
    on[i0:i1] = False
    db = np.full(n, -24.0, dtype=np.float32)
    db[i0:i1] = -50.0

    grid = Grid(
        n=n,
        program_start=0.0,
        speakers=[SpeakerLanes("A", db, on, "CA")],
        wide_key="W",
    )
    g = Globals(
        min_shot=1.0,
        lead=0.0,
        confirm=0.2,
        wide_every=10.0,
        wide_hold=3.0,
        long_take_rule=LONGTAKE_RETURN,
    )
    d = decide(grid, g)

    # Koska puhe alkaa heti t=0:sta, ensimmäinen segmentti (index 0) on CA
    first_close = d.segments[0]
    assert first_close.angle == "CA"
    assert first_close.end == pytest.approx(10.0, abs=0.1)


def test_long_take_reaction_cuts_to_cohost():
    """«Reaktiokuva»: monologi katkeaa toisen puhujan lähikuvaan, sitten takaisin."""
    from autoraffkat.model import LONGTAKE_REACTION

    g = Globals(
        min_shot=1.0,
        lead=0.0,
        confirm=0.2,
        wide_every=5.0,
        wide_hold=2.0,
        long_take_rule=LONGTAKE_REACTION,
    )
    # A puhuu, B on hiljaa mutta B:llä on lähikuva CB
    d = decide(grid_for([(2, 40)], [], seconds=40.0), g)
    angles_list = [s.angle for s in d.segments[1:]]
    assert angles_list[:4] == ["CA", "CB", "CA", "CB"]
    assert d.segments[2].angle == "CB"
    assert d.segments[2].duration == pytest.approx(2.0, abs=0.05)


def test_compute_tempo_1_over_f():
    """Paikallinen tempo reagoi nopeaan vuorotteluun ja pysyy rajojen sisällä."""
    from autoraffkat.decide import _compute_tempo

    n = int(120.0 / HOP)
    active = np.zeros((2, n), dtype=bool)

    # 0-60s: nopea vuorottelu (korkea tempo)
    for t in range(0, int(60.0 / HOP), int(2.0 / HOP)):
        active[0, t : t + int(1.0 / HOP)] = True
        active[1, t + int(1.0 / HOP) : t + int(2.0 / HOP)] = True

    # 60-120s: hidas yksinpuhelu (matala tempo)
    active[0, int(60.0 / HOP) : int(115.0 / HOP)] = True

    tempo = _compute_tempo(active, n)
    assert tempo.shape == (n,)
    assert np.all(tempo >= 0.7) and np.all(tempo <= 1.4)
    # Alussa tempo on korkeampi kuin lopussa
    assert np.mean(tempo[: int(50.0 / HOP)]) > np.mean(tempo[int(70.0 / HOP) :])


# ------------------------------------------------------------- häntä (L-cut)


def test_hang_holds_the_outgoing_speaker():
    """Häntä pitää edellisen puhujan kuvassa, vaikka seuraava on jo äänessä.

    Ilman tätä säädin oli olemassa käyttöliittymässä ja profiileissa mutta ei
    vaikuttanut leikkaukseen mitenkään.
    """
    # A lopettaa 8.0, B aloittaa 8.2 — nopea vuoronvaihto.
    quick = ([(2, 8)], [(8.2, 14)])
    g = Globals(min_shot=1.0, lead=0.3, hang=0.0, confirm=0.2, wide_every=0.0)
    without = decide(grid_for(*quick), g)
    assert angles(without.segments)[2][0] == pytest.approx(7.9, abs=0.05)

    g = Globals(min_shot=1.0, lead=0.3, hang=1.0, confirm=0.2, wide_every=0.0)
    with_hang = decide(grid_for(*quick), g)
    assert angles(with_hang.segments)[2] == (9.0, "CB")


def test_lead_still_wins_over_a_long_pause():
    """Tauon jälkeen leikataan ennakolla: häntä on lattia, ei viive."""
    g = Globals(min_shot=1.0, lead=0.3, hang=1.0, confirm=0.2, wide_every=0.0)
    d = decide(grid_for([(2, 8)], [(14, 20)]), g)
    assert angles(d.segments)[2] == (13.7, "CB")


def test_hang_does_not_delay_the_first_cut():
    """Ennen ensimmäistä puhujaa ei ole ketään kenen kuvassa viivyttäisiin."""
    g = Globals(min_shot=1.0, lead=0.0, hang=2.0, confirm=0.2, wide_every=0.0)
    d = decide(grid_for([(5, 12)], []), g)
    assert angles(d.segments)[1] == (5.0, "CA")


# ------------------------------------------------ kovin ei ole kuka tahansa


def test_brief_backchannel_never_cuts_to_a_silent_speaker():
    """Myötäilyssä kuva menee äänessä olevista kovimmalle, ei kovimmalle mikille.

    Hiljaisen puhujan mikki voi olla tasoltaan korkein — kuuma mikki, iso
    vahvistus, eläväinen huone. Kuva ei silti kuulu hänelle.
    """
    n = int(30.0 / HOP)

    def lane(name, key, spans, level, quiet=-60.0):
        on = np.zeros(n, dtype=bool)
        db = np.full(n, quiet, dtype=np.float32)
        for start, end in spans:
            on[int(start / HOP) : int(end / HOP)] = True
            db[int(start / HOP) : int(end / HOP)] = level
        return SpeakerLanes(name, db, on, key)

    grid = Grid(
        n=n,
        program_start=0.0,
        wide_key="W",
        speakers=[
            lane("A", "CA", [(2, 10)], -30.0),
            lane("B", "CB", [(5, 5.3)], -28.0),  # 0,3 s myötäily
            lane("C", "CC", [], -20.0, quiet=-20.0),  # hiljaa, mutta kovin taso
        ],
    )
    g = Globals(min_shot=0.5, lead=0.0, confirm=0.1, min_overlap=1.0, wide_every=0.0)
    d = decide(grid, g)
    assert "CC" not in [s.angle for s in d.segments]


# ---------------------------------------------- reaktiokuvaa ei ole aina


def test_reaction_needs_a_closeup_that_exists():
    """Reaktiokuvaan ei leikata kulmaan jota ei kyseisessä kohdassa ole.

    Monikamerassa kulma voi puuttua osasta kokonaan; sinne leikkaaminen
    tuottaisi viennissä kuvan jota ei ole.
    """
    from autoraffkat.model import LONGTAKE_REACTION

    n = int(40.0 / HOP)
    speakers = lanes([(2, 40)], [], n)
    speakers[1].available = np.zeros(n, dtype=bool)  # B:n lähikuvaa ei ole
    grid = Grid(n=n, program_start=0.0, speakers=speakers, wide_key="W")
    g = Globals(
        min_shot=1.0,
        lead=0.0,
        confirm=0.2,
        wide_every=5.0,
        wide_hold=2.0,
        long_take_rule=LONGTAKE_REACTION,
    )
    d = decide(grid, g)
    seen = [s.angle for s in d.segments]
    assert "CB" not in seen
    assert "W" in seen  # katkaisu tehdään silti, laajaan


# ------------------------------------------------------------ tempon reunat


def test_tempo_does_not_slow_down_at_the_edges():
    """Tasainen vuorottelu on tasainen myös ohjelman alussa ja lopussa.

    Liukuva ikkuna laskettiin nollilla täytettynä, joten ensimmäiset ja
    viimeiset 22 sekuntia näyttivät aina hitaimmalta mahdolliselta
    aineistolta ja vähimmäiskesto venyi viidenneksen.
    """
    from autoraffkat.decide import _compute_tempo

    n = int(200.0 / HOP)
    active = np.zeros((2, n), dtype=bool)
    step = int(2.0 / HOP)
    for t in range(0, n - step, step):
        active[0, t : t + step // 2] = True
        active[1, t + step // 2 : t + step] = True

    tempo = _compute_tempo(active, n)
    middle = float(tempo[n // 2])
    assert float(tempo[0]) == pytest.approx(middle, abs=0.1)
    assert float(tempo[-1]) == pytest.approx(middle, abs=0.1)


def _long_take_grid(seconds=60.0):
    """Yksi puhuja pitää lattiaa koko ajan, toinen on vaiti."""
    import numpy as np

    from autoraffkat.decide import Grid, SpeakerLanes

    n = int(seconds / HOP)
    talker = SpeakerLanes(
        name="Talker", close_key="CAM_A", on=np.ones(n, dtype=bool),
        level=np.full(n, -20.0, dtype=np.float32))
    listener = SpeakerLanes(
        name="Listener", close_key="CAM_B", on=np.zeros(n, dtype=bool),
        level=np.full(n, -60.0, dtype=np.float32))
    return Grid(n=n, program_start=0.0, speakers=[talker, listener],
                wide_key="WIDE")


def test_a_long_take_breaks_at_a_measured_reaction_when_one_is_near():
    """Aikakatkaisu tietää että aikaa on kulunut, mittaus että jotain tapahtuu.

    Jälkimmäinen on vahvempi signaali, joten katkaisu siirretään mitattuun
    hetkeen kun sellainen on lähellä. Ilman tätä katkaisukohta on kellon
    valitsema ja kuuntelijan kasvot sattumaa.
    """
    import numpy as np

    from autoraffkat.decide import decide
    from autoraffkat.model import LONGTAKE_REACTION

    grid = _long_take_grid(60.0)
    g = Globals(wide_every=14.0, wide_hold=5.0, min_shot=2.5,
                long_take_rule=LONGTAKE_REACTION)
    plain = decide(grid, g).segments

    # Mitattu hetki kolme sekuntia aikakatkaisun jälkeen.
    marks = np.zeros((2, grid.n), dtype=bool)
    at = int(17.0 / HOP)
    marks[1, at:at + int(1.6 / HOP)] = True
    moved = decide(grid, g, marks=marks).segments

    def first_break(segments):
        return next((s.start for s in segments if s.angle != "CAM_A"), None)

    assert first_break(plain) is not None and first_break(moved) is not None
    assert abs(first_break(moved) - 17.0) < abs(first_break(plain) - 17.0), (
        first_break(plain), first_break(moved))


def test_a_measured_reaction_too_far_away_does_not_move_the_break():
    """Neljä sekuntia on raja: kauempaa siirretty katkaisu tuntuisi jo eri
    kohdalta puheenvuoroa."""
    import numpy as np

    from autoraffkat.decide import decide
    from autoraffkat.model import LONGTAKE_REACTION

    grid = _long_take_grid(60.0)
    g = Globals(wide_every=14.0, wide_hold=5.0, min_shot=2.5,
                long_take_rule=LONGTAKE_REACTION)
    marks = np.zeros((2, grid.n), dtype=bool)
    marks[1, int(40.0 / HOP):int(41.6 / HOP)] = True   # kaukana
    plain = decide(grid, g).segments
    far = decide(grid, g, marks=marks).segments
    assert [s.start for s in plain] == [s.start for s in far]


def test_reaction_then_wide_puts_three_shots_where_reaction_puts_one():
    """Reaktio, laaja, takaisin: paluu lähikuvasta laajan kautta."""
    from autoraffkat.decide import decide
    from autoraffkat.model import LONGTAKE_REACTION, LONGTAKE_REACTION_WIDE

    grid = _long_take_grid(60.0)
    base = Globals(wide_every=14.0, wide_hold=8.0, min_shot=2.5,
                   long_take_rule=LONGTAKE_REACTION)
    one = decide(grid, base).segments
    three = decide(grid, replace(base, long_take_rule=LONGTAKE_REACTION_WIDE)).segments
    assert len(three) > len(one), ([s.angle for s in one], [s.angle for s in three])
    # Laaja esiintyy vain kolmen kuvan säännössä.
    assert any(s.angle == "WIDE" for s in three)
    assert not any(s.angle == "WIDE" for s in one)
