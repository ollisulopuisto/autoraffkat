"""Reaktiokuvat: mittauksista jaksoiksi.

Nopea kerros. Lukee valmiin mittaustaulukon (``video.measure``) ja
ruudukon, ja päättää millisekunneissa mitkä sekunnit kelpaavat
reaktiokuvaksi. **Ei avaa yhtään tiedostoa** — sama sääntö kuin
``decide.py``:llä, ja samasta syystä: tämä ajetaan säätökierroksella.

**Mitä pisteytetään.** Ei «nyökkäsikö hän»: sekunnin välein otetusta
näytteestä nyökkäys on yksi piste eikä liike. Kysymys on «onko tämä hyvä
sekunti olla hänen kasvoillaan», ja se on tila — katse puhujaan päin,
silmät auki, suupielet ylhäällä, kasvot lähellä ja liikkeessä. Kaikki
yhdestä ruudusta, ei aikamallista.

Pisteet ovat z-lukuja jakson omasta jakaumasta. Mikään näistä ei ole
absoluuttisesti luettavissa: «paljon liikettä» riippuu ihmisestä, kamerasta
ja huoneesta, ja kiinteä kynnys tarkoittaisi eri asiaa joka jaksossa.

**Katseen perusasento mitataan, ei oleteta.** Kamera ei ole kohtisuorassa,
joten «puhujaan päin» ei ole yaw nolla vaan tämän kameran mediaani. Nollaan
sidottu ehto hylkäisi kaiken tai hyväksyisi kaiken sen mukaan miten kamera
sattui olemaan.

Kynnyksen alle jäävistä ei tehdä mitään. Reaktiokuva jossa kuuntelija
katsoo puhelintaan on huonompi kuin ei reaktiokuvaa lainkaan, joten
puuttuva löydös on oikea tulos eikä epäonnistuminen.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .decide import _runs
from .model import HOP

# Katseen sallittu poikkeama perusasennosta, radiaaneina. Vision antaa yawin
# radiaaneina; 0,35 on noin kaksikymmentä astetta, eli pään kääntö pois
# puhujasta erottuu mutta tavallinen keinunta ei.
GAZE_SPREAD = 0.35

# Peräkkäisten näytteiden väli, jota kauempaa liikettä ei lasketa: kahden
# eri ikkunan yli mitattu «liike» on eri hetki, ei elettä.
MOVE_GAP_S = 4.0


@dataclass
class Reaction:
    """Yksi ehdotettu reaktiokuva, aikajanan aikaa."""

    speaker: str
    start: float
    end: float
    score: float


def scores(table: dict, weights: dict) -> np.ndarray:
    """Pisteet ruuduittain. ``-inf`` niille joista ei löytynyt kasvoja.

    Painot tulevat asetuksista, koska tämä on se osa jota säädetään: purkua
    ei tarvita uudestaan, kun taulukossa on mittaukset eikä pisteitä.
    """
    found = np.asarray(table.get("found"), dtype=bool)
    n = len(found)
    out = np.full(n, -np.inf, dtype=np.float64)
    if not found.any():
        return out

    def column(name: str) -> np.ndarray:
        return np.asarray(table.get(name, np.zeros(n)), dtype=np.float64)

    def z(values: np.ndarray) -> np.ndarray:
        picked = values[found]
        spread = float(picked.std()) or 1e-9
        return (values - float(picked.mean())) / spread

    yaw = column("yaw")
    # Perusasento vain löytyneistä: nollat sotkisivat mediaanin.
    base = float(np.median(yaw[found]))
    gaze = np.exp(-(((yaw - base) / GAZE_SPREAD) ** 2))

    times = column("times")
    move = np.zeros(n)
    if n > 1:
        gap = np.diff(times, prepend=times[0])
        step = np.hypot(np.diff(column("cx"), prepend=column("cx")[0]),
                        np.diff(column("cy"), prepend=column("cy")[0]))
        with np.errstate(divide="ignore", invalid="ignore"):
            move = np.where(gap > 1e-6, step / np.maximum(gap, 1e-6), 0.0)
        move[gap > MOVE_GAP_S] = 0.0
        move[0] = 0.0

    total = (float(weights.get("gaze", 1.2)) * z(gaze)
             + float(weights.get("smile", 0.9)) * z(column("smile"))
             + float(weights.get("eyes", 0.7)) * z(column("eyes"))
             + float(weights.get("motion", 0.5)) * z(move)
             + float(weights.get("size", 0.3)) * z(column("size")))
    out[found] = total[found]
    return out


def listening(grid, speaker: str) -> np.ndarray:
    """Ruudut joissa tämä puhuja on vaiti ja joku toinen äänessä."""
    names = [lane.name for lane in grid.speakers]
    if speaker not in names:
        return np.zeros(0, dtype=bool)
    active = np.stack([lane.on for lane in grid.speakers])
    me = names.index(speaker)
    others = np.zeros_like(active[me])
    for other in range(len(names)):
        if other != me:
            others |= active[other]
    return others & ~active[me]


def to_timeline(item, file_times: np.ndarray, program_start: float) -> np.ndarray:
    """Tiedostoajat aikajanan ajaksi. ``nan`` niille jotka jäävät ulos.

    Sama muunnos kuin ``mix.closed_ranges``illa mutta toisin päin:
    tiedostoaika = base + aikajana, joten aikajana = tiedostoaika - base.
    """
    out = np.full(len(file_times), np.nan)
    for placement in item.placements:
        base = float(placement.start - item.asset_start - placement.offset)
        stamps = file_times - base
        inside = (stamps >= float(placement.offset)) & (stamps < float(placement.end))
        out[inside] = stamps[inside]
    return out


def find(grid, roles, timeline, tables: dict, settings, program_start: float
         ) -> list[Reaction]:
    """Ehdotetut reaktiokuvat, aikajärjestyksessä.

    ``tables`` on media-avain -> mittaustaulukko. Puuttuva taulukko ei ole
    virhe: se tarkoittaa ettei sitä kameraa ole mitattu, ja silloin siitä ei
    ehdoteta mitään.
    """
    if not getattr(settings, "reactions", False):
        return []
    weights = {
        "gaze": settings.reaction_gaze,
        "smile": settings.reaction_smile,
        "eyes": settings.reaction_eyes,
        "motion": settings.reaction_motion,
        "size": settings.reaction_size,
    }
    found: list[Reaction] = []
    for speaker in [lane.name for lane in grid.speakers]:
        key = roles.closes.get(speaker)
        if not key:
            continue
        quiet = listening(grid, speaker)
        if not quiet.any():
            continue
        for item in timeline.track_media(key):
            table = tables.get(item.key)
            if table is None:
                continue
            points = scores(table, weights)
            stamps = to_timeline(item, np.asarray(table["times"], dtype=np.float64),
                                 program_start)
            for index in np.argsort(points)[::-1]:
                value = points[index]
                if not np.isfinite(value) or value < settings.reaction_threshold:
                    break
                at = stamps[index]
                if not np.isfinite(at):
                    continue
                cell = int((at - program_start) / HOP)
                if cell < 0 or cell >= len(quiet) or not quiet[cell]:
                    continue
                found.append(Reaction(speaker, at, at + settings.reaction_length,
                                      float(value)))
    return _thin(found, settings)


def _thin(found: list[Reaction], settings) -> list[Reaction]:
    """Karsii päällekkäiset ja liian tiheät, paras ensin.

    Ilman tätä sama hyvä hetki tulisi valituksi monta kertaa peräkkäisistä
    ruuduista, ja jakso täyttyisi reaktiokuvista siellä missä pisteet
    sattuvat olemaan korkeat.
    """
    kept: list[Reaction] = []
    for candidate in sorted(found, key=lambda r: r.score, reverse=True):
        if any(candidate.start < other.end + settings.reaction_spacing
               and other.start < candidate.end + settings.reaction_spacing
               for other in kept):
            continue
        kept.append(candidate)
    return sorted(kept, key=lambda r: r.start)
