"""Reaktiokuvat: pisteytys ja jaksot.

Tunnistin on se osa jonka odotetaan vaihtuvan, joten testit eivät saa
riippua siitä. Taulukot kirjoitetaan tässä käsin — se on sama muoto jonka
``video.measure`` tuottaa, ja siksi testit kertovat pisteytyksestä eivätkä
macOS:n kasvontunnistuksesta.
"""

import numpy as np
import pytest

from autoraffkat import reactions
from autoraffkat.model import Globals

FIELDS = ("yaw", "roll", "size", "x", "y", "w", "h", "eyes", "smile",
          "cx", "cy", "turn", "tilt")


def table(n=40, **columns):
    """Mittaustaulukko oletuksilla, joita testi muuttaa nimeltä."""
    out = {"times": np.arange(n, dtype=np.float32),
           "found": np.ones(n, dtype=bool)}
    for name in FIELDS:
        out[name] = np.zeros(n, dtype=np.float32)
    for name, value in columns.items():
        out[name] = (np.asarray(value, dtype=np.float32) if np.ndim(value)
                     else np.full(n, float(value), dtype=np.float32))
    return out


def test_a_frame_without_a_face_can_never_be_chosen():
    """«Ei kasvoja» on tulos, ei nolla.

    Nollana se kilpailisi muiden kanssa ja voisi voittaa, koska nolla on
    z-luvuissa keskiarvo — eli reaktiokuvaksi valikoituisi ruutu jossa ei
    näy ketään.
    """
    data = table(smile=np.linspace(-1, 1, 40))
    data["found"][:10] = False
    points = reactions.scores(data, {"turn": 0})
    assert np.all(np.isneginf(points[:10]))
    assert np.all(np.isfinite(points[10:]))


def test_the_gate_keeps_the_facing_frames_and_stops_the_rest():
    """Portti ratkaisee, ei järjestys.

    Reaktiokuvan rima on «ei kelvoton», ei «loistava». Mitattuna oikealla
    jaksolla raja 0,057 päästi läpi kaikki kuusi hyväksi arvioitua eikä
    yhtään viidestätoista huonosta. Tässä sama muoto pienoiskoossa: pää
    kääntyneenä ei kelpaa millään muulla osalla.
    """
    turn = np.full(40, 0.30)      # perusasento, ei nolla
    turn[5] = 0.30 + 0.20         # kääntynyt selvästi pois
    turn[6] = 0.30 + 0.01         # käytännössä suoraan
    data = table(turn=turn, smile=np.full(40, 5.0))   # hymy ei saa pelastaa
    points = reactions.scores(data, {"turn_max": reactions.TURN_MAX})
    assert np.isneginf(points[5]), "kääntynyt pää läpäisi portin"
    assert np.isfinite(points[6])


def test_the_gate_default_sits_between_the_marked_classes():
    """Raja on mitattu, ei valittu.

    Kaksikymmentäkolme käsin arvioitua ruutua eivät mene päällekkäin:
    huonoin hyväksi merkitty 0,0721, paras huonoksi merkitty 0,0943.
    Oletuksen on oltava siinä välissä, ja välin tiukemmalla puoliskolla —
    ohi mennyt reaktiokuva ei maksa mitään, kelvoton maksaa oton.

    Jos joku siirtää lukua, tämä kertoo kumman virheen hän valitsi.
    """
    worst_good, best_bad = 0.0721, 0.0943
    assert worst_good < reactions.TURN_MAX < best_bad
    middle = (worst_good + best_bad) / 2
    assert reactions.TURN_MAX <= middle, "raja päästää huonoja ennemmin kuin hylkää hyviä"


def test_the_turn_baseline_is_measured_not_assumed():
    """Kamera ei ole kohtisuorassa, joten «puhujaan päin» ei ole nolla.

    Nollaan sidottu portti hylkäisi tässä koko kameran tai päästäisi kaiken
    sen mukaan miten kamera sattui olemaan.
    """
    turn = np.full(40, 0.42)      # kaikki katsovat vakaasti sivuun
    data = table(turn=turn)
    points = reactions.scores(data, {"turn_max": reactions.TURN_MAX})
    assert np.all(np.isfinite(points)), "perusasento luettiin nollaksi"


def test_the_gaze_baseline_is_measured_not_assumed():
    """Kamera ei ole kohtisuorassa, joten «puhujaan päin» ei ole yaw nolla.

    Nollaan sidottu ehto antaisi tämän kameran jokaiselle ruudulle saman
    surkean pisteen, ja katse lakkaisi erottelemasta yhtään mitään.
    """
    # Kaikki katsovat vakaasti 0,8 radiaanissa paitsi yksi joka kääntyy pois.
    yaw = np.full(40, 0.8)
    yaw[7] = 0.0
    points = reactions.scores(table(yaw=yaw), {"gaze": 1.0, "turn": 0, "smile": 0,
                                               "eyes": 0, "motion": 0, "size": 0})
    assert points[7] == pytest.approx(points.min())
    # Perusasennossa olevat ovat keskenään samanarvoisia.
    rest = np.delete(points, 7)
    assert rest.std() < 1e-6


def test_weights_change_the_ranking_without_new_measurements():
    """Painot ovat se osa jota säädetään, eikä säätö saa maksaa purkua."""
    data = table(smile=np.linspace(0, 1, 40), eyes=np.linspace(1, 0, 40))
    smiley = reactions.scores(data, {"turn": 0, "gaze": 0, "smile": 1, "eyes": 0,
                                     "motion": 0, "size": 0})
    wide_eyed = reactions.scores(data, {"turn": 0, "gaze": 0, "smile": 0, "eyes": 1,
                                        "motion": 0, "size": 0})
    assert int(np.argmax(smiley)) == 39
    assert int(np.argmax(wide_eyed)) == 0


def test_motion_is_not_measured_across_a_gap():
    """Kahden eri ikkunan yli mitattu «liike» on eri hetki, ei elettä."""
    times = np.array([0.0, 1.0, 2.0, 900.0, 901.0], dtype=np.float32)
    data = table(5)
    data["times"] = times
    data["cx"] = np.array([0.0, 0.0, 0.0, 0.9, 0.9], dtype=np.float32)
    points = reactions.scores(data, {"turn": 0, "gaze": 0, "smile": 0, "eyes": 0,
                                     "motion": 1, "size": 0})
    # Hyppy ruutuun 3 on 898 sekunnin päässä edellisestä eikä saa näkyä.
    assert points[3] == pytest.approx(points[0], abs=1e-6)


def _grid(pattern):
    """Ruudukko kahdella puhujalla; pattern on merkkijono A/B/-."""
    class Lane:
        def __init__(self, name, on):
            self.name, self.on = name, np.asarray(on, dtype=bool)

    class Grid:
        speakers = [Lane("A", [c == "A" for c in pattern]),
                    Lane("B", [c == "B" for c in pattern])]
    return Grid()


def test_listening_is_silence_under_someone_elses_voice():
    """Ei mikä tahansa hiljaisuus: hiljaisuudessa ei ole mihin reagoida."""
    grid = _grid("AAB--B")
    assert list(reactions.listening(grid, "A")) == [False, False, True, False,
                                                    False, True]
    assert list(reactions.listening(grid, "B")) == [True, True, False, False,
                                                    False, False]


def test_nothing_is_proposed_below_the_threshold():
    """Reaktiokuva jossa kuuntelija katsoo puhelintaan on huonompi kuin ei
    reaktiokuvaa. Puuttuva löydös on oikea tulos."""
    settings = Globals(reactions=True, reaction_threshold=99.0)
    grid = _grid("B" * 200)

    class Item:
        key = "cam"
        asset_start = 0
        placements = []

    class Timeline:
        def track_media(self, key):
            return [Item()]

    class Roles:
        closes = {"A": "camA"}

    assert reactions.find(grid, Roles(), Timeline(), {"cam": table()},
                          settings, 0.0) == []


def test_reactions_off_means_nothing_is_computed():
    """Asetus pois päältä ei saa tuottaa mitään — eikä kaatua puuttuvaan
    taulukkoon."""
    assert reactions.find(_grid("AB"), None, None, {}, Globals(), 0.0) == []


def test_crowded_candidates_are_thinned_best_first():
    """Sama hyvä hetki tulisi muuten valituksi monta kertaa peräkkäin."""
    settings = Globals(reactions=True, reaction_length=2.0, reaction_spacing=10.0)
    found = [reactions.Reaction("A", t, t + 2.0, score)
             for t, score in ((10.0, 1.0), (11.0, 3.0), (12.0, 2.0), (60.0, 0.5))]
    kept = reactions._thin(found, settings)
    assert [r.start for r in kept] == [11.0, 60.0]
    assert kept[0].score == 3.0


def test_the_preview_lane_lines_up_with_the_speech_rows():
    """Reaktiorivi tiivistetään samoihin sarakkeisiin kuin puhujarivit.

    Palkkia luetaan päällekkäin: rivien suhde toisiinsa *on* se mitä siitä
    katsotaan. Eri jaolla reaktiokuva näyttäisi osuvan väärään kohtaan
    puheen suhteen, eikä mikään kertoisi siitä.
    """
    from autoraffkat.decide import Decision
    from autoraffkat.preview import build

    class Lane:
        def __init__(self, name, on):
            self.name, self.on = name, np.asarray(on, dtype=bool)
            self.close_key = "cam"

    class Grid:
        n = 200
        duration = 100.0
        program_start = 0.0
        speakers = [Lane("A", [1] * 100 + [0] * 100),
                    Lane("B", [0] * 100 + [1] * 100)]

    decision = Decision(segments=[], active=np.zeros((2, 200), dtype=bool),
                        chosen=np.zeros(200, dtype=np.int32))
    # Reaktio ohjelman puolivälistä eteenpäin, viisi sekuntia.
    out = build(Grid(), decision, columns=100, reactions=[(50.0, 55.0, 1)])
    lane = out["reactions"]
    assert len(lane) == len(out["chosen"]) == out["columns"]
    on = [i for i, v in enumerate(lane) if v >= 0]
    assert on, "reaktio katosi tiivistyksessä"
    # Puolivälissä ohjelmaa = puolivälissä sarakkeita, samalla jaolla.
    assert 48 <= on[0] <= 52, on
    assert all(lane[i] == 1 for i in on), "puhujan indeksi ei säilynyt"


def test_a_short_reaction_survives_the_squeeze():
    """Reaktiokuva on sekunnin luokkaa ja palkki on tuhat saraketta.

    Keskiarvoistava tiivistys hukkaisi ne juuri niiltä kohdin jotka
    halutaan nähdä — sarake merkitään heti kun yksikin osuu siihen.
    """
    from autoraffkat.decide import Decision
    from autoraffkat.preview import build

    class Lane:
        name, close_key = "A", "cam"
        on = np.ones(4000, dtype=bool)

    class Grid:
        n = 4000
        duration = 4000.0
        program_start = 0.0
        speakers = [Lane()]

    decision = Decision(segments=[], active=np.zeros((1, 4000), dtype=bool),
                        chosen=np.zeros(4000, dtype=np.int32))
    out = build(Grid(), decision, columns=1400, reactions=[(1000.0, 1001.6, 0)])
    assert any(v >= 0 for v in out["reactions"]), "lyhyt reaktio katosi"
