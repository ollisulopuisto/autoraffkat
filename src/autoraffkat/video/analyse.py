"""Kameroiden mittaus kokonaisuutena: mitä mitataan ja missä järjestyksessä.

Vain lähikuvat mitataan. Laajaa ei tarvita — reaktiokuva on kuuntelijan
lähikuva — eikä turhaa purkua kannata tehdä, koska purku on koko työn hinta.

Ja vain ne tiedostot joista jotain ehdotettaisiin: jos puhuja ei ole vaiti
kertaakaan, hänen kamerastaan ei voi tulla reaktiokuvaa, eikä sitä pidä
purkaa. Tämä on sama rajaus kuin ``reactions.listening``illa, ja se on
tehtävä *ennen* purkua eikä sen jälkeen — muuten säästö jää saamatta.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

from . import detect, measure

# Montako tiedostoa puretaan yhtä aikaa.
#
# Purku on koko työn hinta ja se rinnakkaistuu tiedostojen kesken, koska
# yhden virran purku ei jakaudu ytimille. Mitattuna ulkoiselta USB-SSD:ltä
# 47 Mb/s:n tiedostoilla: yksi 22x reaaliaikaa, kaksi 38x, neljä 73x — ja
# **siihen se loppuu**: kuusi 72x, kahdeksan 71x.
#
# Katto ei ole levy eikä prosessori. Mitattuna purun aikana ``dd`` sai
# samalta levyltä 759 MB/s samaan aikaan kun purku piti 254 MB/s:n
# vauhtinsa, ja prosessorista oli 66 % jouten kahdeksallakin. Se on
# raudan h264-purkajien määrä, eikä sitä lisätä säikeillä.
#
# Oikea polku mitattuna kokonaan: neljä tiedostoa, 990 s sarjassa -> 476 s.
MAX_PARALLEL = 4


class VideoError(Exception):
    """Kuvan analyysi ei onnistunut."""


def close_up_files(grid, roles, timeline) -> list[tuple[str, str, str]]:
    """(puhuja, media-avain, polku) niistä lähikuvista jotka kannattaa mitata."""
    from ..reactions import listening

    out: list[tuple[str, str, str]] = []
    for lane in grid.speakers:
        key = roles.closes.get(lane.name)
        if not key:
            continue
        if not listening(grid, lane.name).any():
            continue
        for item in timeline.track_media(key):
            if item.path and item.has_video:
                out.append((lane.name, item.key, item.path))
    return out


def freshness(grid, roles, timeline, settings) -> tuple[int, int]:
    """(mitattu, kaikkiaan) — mitä käyttöliittymä kertoo painamatta mitään.

    Pelkkiä tiedostotarkistuksia, ei purkua: tämä ajetaan säätökierroksella
    kuten ``mix.freshness``, ja siksi sen on oltava halpa.
    """
    if not getattr(settings, "reactions", False):
        return (0, 0)
    try:
        detector = detect.load(settings.reaction_detector)
    except detect.DetectError:
        return (0, 0)
    files = close_up_files(grid, roles, timeline)
    ready = sum(1 for _, _, path in files
                if os.path.exists(path) and measure.is_cached(path, detector))
    return (ready, len(files))


def tables(grid, roles, timeline, settings, progress=None) -> tuple[dict, dict]:
    """Mittaustaulukot media-avaimittain, ja virheet avaimittain.

    Puuttuva tiedosto ei kaada koko ajoa — media voi olla irrotetulla
    levyllä, ja se on tavallinen tilanne eikä vika ohjelmassa. Mutta se
    **kerrotaan**: asetus päällä ja tuloksessa ei mitään on juuri se vika
    joka tässä projektissa on jäänyt huomaamatta kerta toisensa jälkeen.
    """
    out: dict = {}
    errors: dict = {}
    if not getattr(settings, "reactions", False):
        return out, errors
    try:
        detector = detect.load(settings.reaction_detector)
    except detect.DetectError as exc:
        errors["detector"] = str(exc)
        return out, errors

    files = close_up_files(grid, roles, timeline)
    todo = []
    for speaker, key, path in files:
        if not os.path.exists(path):
            errors[key] = f"{os.path.basename(path)}: mediaa ei löydy"
        else:
            todo.append((key, path))
    if not todo:
        return out, errors

    workers = max(1, min(MAX_PARALLEL, len(todo), os.cpu_count() or 1))
    # Oma tunnistin per työntekijä. Vision kestäisi todennäköisesti jaonkin,
    # mutta sama sääntö kuin liitännäisvarannolla: instanssit rakennetaan
    # etukäteen eikä jaeta säikeiden kesken, jolloin kysymystä ei tarvitse
    # ratkaista uudestaan.
    pool_detectors = [detector] + [
        detect.load(detector.name) for _ in range(workers - 1)]

    lock = threading.Lock()
    shares = [0.0] * len(todo)

    def report(index: int, fraction: float) -> None:
        if progress is None:
            return
        with lock:
            shares[index] = fraction
            progress(sum(shares) / len(shares))

    def run(index: int):
        key, path = todo[index]
        own = pool_detectors[index % workers]
        try:
            table = measure.table(
                path, own,
                progress=(lambda frac, i=index: report(i, frac))
                if progress else None)
            report(index, 1.0)
            return key, table, None
        except (measure.MeasureError, OSError, RuntimeError) as exc:
            return key, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, table, error in pool.map(run, range(len(todo))):
            if error:
                errors[key] = error
            else:
                out[key] = table
    return out, errors
