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

from . import detect, measure


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
    for index, (speaker, key, path) in enumerate(files):
        if not os.path.exists(path):
            errors[key] = f"{os.path.basename(path)}: mediaa ei löydy"
            continue
        try:
            out[key] = measure.table(
                path, detector,
                progress=(lambda frac, i=index: progress(
                    (i + frac) / max(1, len(files)))) if progress else None)
        except (measure.MeasureError, OSError) as exc:
            errors[key] = str(exc)
    return out, errors
