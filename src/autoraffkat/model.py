"""Tietomallit: mediat, roolit, asetukset, leikkaukset."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from fractions import Fraction

from .timeline import ZERO

# Analyysin aika-askel sekunteina. Sama arvo verhokäyrässä ja päätöksessä.
HOP = 0.02

ROLE_WIDE = "wide"
ROLE_CLOSE = "close"
ROLE_MIC = "mic"
ROLE_UNUSED = "unused"
ROLES = (ROLE_UNUSED, ROLE_WIDE, ROLE_CLOSE, ROLE_MIC)

OVERLAP_WIDE = "wide"
OVERLAP_HOLD = "hold"
OVERLAP_LOUDER = "louder"
OVERLAP_RULES = (OVERLAP_WIDE, OVERLAP_HOLD, OVERLAP_LOUDER)


@dataclass
class Placement:
    """Yksi esiintymä aikajanalla.

    ``offset`` on aikajanan hetki, jossa klipin ``start`` osuu. Lähdeaika t
    (asset-aikapohjassa) vastaa siis aikajanan hetkeä ``offset + (t - start)``.
    """

    offset: Fraction
    start: Fraction
    duration: Fraction
    lane: int = 0

    @property
    def end(self) -> Fraction:
        return self.offset + self.duration

    def covers(self, seconds: Fraction) -> bool:
        return self.offset <= seconds < self.end

    def source_at(self, seconds: Fraction) -> Fraction:
        return self.start + (seconds - self.offset)


@dataclass
class MediaItem:
    """Yksi XML:stä löytynyt media ja sen esiintymät aikajanalla.

    Kaikki ajat ovat Fraction-sekunteja. ``asset_start`` on lähdemateriaalin
    ensimmäinen hetki asset-aikapohjassa; tiedoston t=0 vastaa sitä. Verhokäyrä
    indeksoidaan tiedostoajalla, aikajana asset-ajalla, ja ero on tämä.
    """

    key: str                       # vakaa tunniste asetusten talletusta varten
    name: str
    path: str                      # dekoodattu tiedostopolku, "" jos puuttuu
    src: str                       # alkuperäinen media-rep src (file://...)
    asset_start: Fraction = ZERO
    asset_duration: Fraction = ZERO
    has_video: bool = False
    has_audio: bool = False
    width: int = 0
    height: int = 0
    frame_duration: Fraction | None = None
    audio_rate: int = 48000
    audio_channels: int = 2
    audio_sources: int = 1
    video_sources: int = 1
    asset_id: str = ""             # lähde-XML:n resurssi-id
    format_id: str = ""
    placements: list[Placement] = field(default_factory=list)

    @property
    def timeline_start(self) -> Fraction:
        return min((p.offset for p in self.placements), default=ZERO)

    @property
    def timeline_end(self) -> Fraction:
        return max((p.end for p in self.placements), default=ZERO)

    def placement_at(self, seconds: Fraction) -> Placement | None:
        for p in self.placements:
            if p.covers(seconds):
                return p
        return None

    def file_time_at(self, seconds: Fraction) -> Fraction | None:
        """Aikajanan hetkeä vastaava aika tiedoston alusta, tai None."""
        p = self.placement_at(seconds)
        if p is None:
            return None
        return p.source_at(seconds) - self.asset_start

    def to_json(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "path": self.path,
            "missing": bool(self.path) and not os.path.exists(self.path),
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "width": self.width,
            "height": self.height,
            "fps": (round(float(1 / self.frame_duration), 3)
                    if self.frame_duration else None),
            "audio_channels": self.audio_channels,
            "timeline_start": float(self.timeline_start),
            "timeline_end": float(self.timeline_end),
            "placements": len(self.placements),
        }


@dataclass
class TrackConfig:
    """Käyttäjän antama rooli yhdelle medialle."""

    role: str = ROLE_UNUSED
    speaker: str = ""              # lähikuvan ja mikin yhdistävä nimi
    sensitivity_db: float = 12.0   # dB pohjakohinan yli
    gain_db: float = 0.0           # vahvistuksen korjaus

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "TrackConfig":
        known = {f: data[f] for f in ("role", "speaker", "sensitivity_db", "gain_db")
                 if f in data}
        return cls(**known)


@dataclass
class Globals:
    """Koko leikkausta koskevat säätimet."""

    min_shot: float = 2.5          # lyhin kuvan kesto, s
    lead: float = 0.15             # ennakko: leikataan näin paljon ennen puheen alkua, s
    confirm: float = 0.40          # vahvistusaika: puheen on jatkuttava näin kauan, s
    overlap_rule: str = OVERLAP_WIDE
    dominance_db: float = 5.0      # vaadittu ero päällekkäispuheessa
    min_overlap: float = 0.50      # lyhin päällekkäispuhe joka laukaisee säännön, s
    wide_every: float = 0.0        # pakota laaja näin usein, 0 = ei koskaan
    project_name: str = "Raakaleikkaus"

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Globals":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class Segment:
    """Yksi kuva valmiissa leikkauksessa, aikajanan sekunneissa."""

    angle: str                     # median key, tai "" jos kuvaa ei löydy
    label: str                     # puhujan nimi tai "laaja" — esikatselua varten
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Speaker:
    """Puhuja: yksi mikki ja enintään yksi lähikuva."""

    name: str
    mic_keys: list[str] = field(default_factory=list)
    close_key: str | None = None
