"""Kerrosten liitos: verhokäyristä ruudukoksi.

Hidas osa (ffmpeg + RMS) on ``audio.envelope``. Tämä moduuli kohdistaa valmiit
verhokäyrät aikajanan ruudukolle ja soveltaa raitakohtaiset säätimet. Kohdistus
on pelkkää numpy-indeksointia, joten se kestää roolimuutoksenkin ilman uutta
purkua.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

from .audio.envelope import FLOOR_DB, EnvelopeError, envelope_for
from .decide import Grid, SpeakerLanes
from .fcpxml.read import Timeline
from .model import (HOP, ROLE_CLOSE, ROLE_MIC, ROLE_WIDE, MediaItem,
                    TrackConfig)

SMOOTH_SECONDS = 0.10
NOISE_PERCENTILE = 20.0


class AnalysisError(Exception):
    """Aineisto ei riitä päätökseen."""


def _smooth(db: np.ndarray, seconds: float) -> np.ndarray:
    """Liukuva keskiarvo. Tasoittaa tavuvälit, joita ei haluta leikkauksiksi."""
    k = max(1, int(round(seconds / HOP)))
    if k <= 1 or db.size < k:
        return db
    kernel = np.ones(k, dtype=np.float32) / k
    return np.convolve(db, kernel, mode="same").astype(np.float32)


def align(item: MediaItem, envelope: np.ndarray, program_start: Fraction,
          n: int) -> tuple[np.ndarray, np.ndarray]:
    """Verhokäyrä aikajanan ruudukolle. Palauttaa (dB, onko mediaa)."""
    out = np.full(n, FLOOR_DB, dtype=np.float32)
    valid = np.zeros(n, dtype=bool)
    if n <= 0 or envelope.size == 0:
        return out, valid
    start_f = float(program_start)
    program_end = program_start + Fraction(n) * Fraction(HOP).limit_denominator(1000)

    for p in item.placements:
        lo = max(p.offset, program_start)
        hi = min(p.end, program_end)
        if hi <= lo:
            continue
        i0 = max(0, int(np.ceil((float(lo) - start_f) / HOP)))
        i1 = min(n, int(np.floor((float(hi) - start_f) / HOP)))
        if i1 <= i0:
            continue
        idx = np.arange(i0, i1)
        # tiedostoaika = klipin start - assetin start + (aikajana - klipin offset)
        base = float(p.start - item.asset_start - p.offset)
        file_t = base + start_f + idx * HOP
        e = np.rint(file_t / HOP).astype(np.int64)
        ok = (e >= 0) & (e < envelope.size)
        out[idx[ok]] = envelope[e[ok]]
        valid[idx[ok]] = True
    return out, valid


def availability(items: MediaItem | list[MediaItem], program_start: Fraction,
                 n: int) -> np.ndarray:
    """Missä ruudukon kohdissa raidalla on kuvaa.

    Raita voi koostua useasta assetista — monikamerassa sama kulma on oma
    tiedostonsa joka osassa — joten peitto on niiden yhdiste.
    """
    if isinstance(items, MediaItem):
        items = [items]
    mask = np.zeros(n, dtype=bool)
    start_f = float(program_start)
    for item in items:
        for p in item.placements:
            i0 = max(0, int(np.ceil((float(p.offset) - start_f) / HOP)))
            i1 = min(n, int(np.floor((float(p.end) - start_f) / HOP)))
            if i1 > i0:
                mask[i0:i1] = True
    return mask


@dataclass
class Analysis:
    """Kerran laskettu hidas osa: verhokäyrä per media."""

    timeline: Timeline
    envelopes: dict[str, np.ndarray] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    _aligned: dict[tuple, tuple[np.ndarray, np.ndarray, float]] = field(default_factory=dict)

    def media_by_key(self) -> dict[str, MediaItem]:
        """Mediat avaimella haettavina."""
        return {m.key: m for m in self.timeline.media}

    def aligned(self, item: MediaItem, program_start: Fraction, n: int):
        """Kohdistettu käyrä välimuistista: ``(dB, onko mediaa, pohjakohina)``.

        Välimuisti on avainnettu ohjelman rajoilla, koska roolin vaihto siirtää
        niitä. Pohjakohina lasketaan tässä eikä säätimien yhteydessä, koska se
        ei riipu säätimistä.
        """
        cache_key = (item.key, program_start, n)
        hit = self._aligned.get(cache_key)
        if hit is None:
            env = self.envelopes.get(item.key)
            if env is None:
                hit = (np.full(n, FLOOR_DB, dtype=np.float32),
                       np.zeros(n, dtype=bool), FLOOR_DB)
            else:
                db, valid = align(item, env, program_start, n)
                db = _smooth(db, SMOOTH_SECONDS)
                # Pohjakohina riippuu vain verhokäyrästä, ei säätimistä, joten
                # se lasketaan kerran tähän välimuistiin.
                floor = (float(np.percentile(db[valid], NOISE_PERCENTILE))
                         if valid.any() else FLOOR_DB)
                hit = (db, valid, floor)
            self._aligned[cache_key] = hit
        return hit


def analyze(timeline: Timeline, progress=None, keys: list[str] | None = None) -> Analysis:
    """Laskee tai lukee välimuistista verhokäyrät. Hidas — ajetaan kerran."""
    analysis = Analysis(timeline=timeline)
    targets = [m for m in timeline.media
               if m.has_audio and (keys is None or m.key in keys)]
    for index, item in enumerate(targets):
        if progress is not None:
            progress(index, len(targets), item.name)
        try:
            analysis.envelopes[item.key] = envelope_for(item.path)
        except EnvelopeError as exc:
            analysis.errors[item.key] = str(exc)
    if progress is not None:
        progress(len(targets), len(targets), "")
    return analysis


# ------------------------------------------------------------------ ruudukko


@dataclass
class Roles:
    """Roolituksesta johdettu rakenne.

    Avaimet ovat raita-avaimia (``Timeline.tracks``), eivät media-avaimia:
    monikamerassa yksi raita kokoaa saman kulman kaikista osista.
    """

    wide_key: str = ""
    speakers: list[str] = field(default_factory=list)
    mics: dict[str, list[str]] = field(default_factory=dict)     # puhuja -> mikit
    closes: dict[str, str] = field(default_factory=dict)         # puhuja -> lähikuva
    problems: list[str] = field(default_factory=list)


def resolve_roles(timeline: Timeline, tracks: dict[str, TrackConfig]) -> Roles:
    """Kääntää raitakohtaiset roolit puhujiksi ja kerää puutteet.

    Puutteet palautetaan listana eikä nosteta poikkeuksena: käyttöliittymässä
    ollaan jatkuvasti puolivalmiissa tilassa, ja puute on näytettävä ilman että
    edellinen tulos katoaa.
    """
    roles = Roles()
    for track in timeline.tracks:
        cfg = tracks.get(track.key)
        if cfg is None:
            continue
        if cfg.role == ROLE_WIDE and not roles.wide_key:
            roles.wide_key = track.key
        elif cfg.role == ROLE_MIC:
            name = cfg.speaker.strip()
            if not name:
                roles.problems.append(f"Mikille «{track.name}» ei ole puhujaa.")
                continue
            if name not in roles.speakers:
                roles.speakers.append(name)
            roles.mics.setdefault(name, []).append(track.key)
        elif cfg.role == ROLE_CLOSE:
            name = cfg.speaker.strip()
            if not name:
                roles.problems.append(f"Lähikuvalle «{track.name}» ei ole puhujaa.")
                continue
            if name not in roles.speakers:
                roles.speakers.append(name)
            roles.closes[name] = track.key

    if not roles.wide_key:
        roles.problems.append("Valitse yksi media laajaksi kuvaksi.")
    if not roles.mics:
        roles.problems.append("Valitse ainakin yksi mikki ja anna sille puhuja.")
    for name in roles.speakers:
        if name not in roles.mics:
            roles.problems.append(f"Puhujalta «{name}» puuttuu mikki.")
    return roles


def program_range(timeline: Timeline, roles: Roles) -> tuple[Fraction, Fraction]:
    """Ohjelman rajat: laaja kuva ja mikit rajaavat, lähikuvat eivät.

    Raidan väli on ensimmäisestä osasta viimeiseen. Kahdessa osassa kuvattu
    mikki alkaa siis osan A alusta eikä osan B alusta, vaikka jälkimmäinen
    onkin oma assettinsa.
    """
    spans = [timeline.track_span(roles.wide_key)] if roles.wide_key else []
    for keys in roles.mics.values():
        spans += [timeline.track_span(k) for k in keys]
    spans = [s for s in spans if s is not None]
    if not spans:
        return timeline.start, timeline.end
    return max(s[0] for s in spans), min(s[1] for s in spans)


def build_grid(analysis: Analysis, tracks: dict[str, TrackConfig],
               roles: Roles | None = None) -> tuple[Grid, Fraction, Fraction]:
    """Ruudukko päätöskerrokselle. Ajetaan joka säädöllä — pysyttävä millisekunneissa."""
    timeline = analysis.timeline
    roles = roles or resolve_roles(timeline, tracks)
    program_start, program_end = program_range(timeline, roles)
    span = float(program_end - program_start)
    if span <= 1.0:
        raise AnalysisError(
            "Laajalla kuvalla ja mikeillä ei ole yhteistä aikaa. "
            "Tarkista roolit ja lähde-XML:n synkkaus."
        )
    n = int(span / HOP)

    lanes: list[SpeakerLanes] = []
    for name in roles.speakers:
        mic_keys = roles.mics.get(name, [])
        if not mic_keys:
            continue
        level = np.full(n, FLOOR_DB, dtype=np.float32)
        on = np.zeros(n, dtype=bool)
        for key in mic_keys:
            cfg = tracks.get(key, TrackConfig())
            # Raidan osat ovat eri tiedostoja mutta sama mikki: sama säädin,
            # sama puhuja, eri kohta aikajanaa.
            for item in timeline.track_media(key):
                db, valid, floor = analysis.aligned(item, program_start, n)
                if not valid.any():
                    continue
                # Herkkyys on kynnys pohjakohinan yli; vahvistus siirtää sekä
                # signaalin että pohjan, joten se ei vaikuta kynnykseen — vain
                # mikkien keskinäiseen vertailuun päällekkäispuheessa.
                on |= valid & (db > floor + cfg.sensitivity_db)
                level = np.maximum(level, db + cfg.gain_db)

        close_key = roles.closes.get(name)
        avail = None
        if close_key:
            items = timeline.track_media(close_key)
            if items:
                avail = availability(items, program_start, n)
        lanes.append(SpeakerLanes(name=name, level=level, on=on,
                                  close_key=close_key, available=avail))

    grid = Grid(n=n, program_start=float(program_start), speakers=lanes,
                wide_key=roles.wide_key)
    return grid, program_start, program_end
