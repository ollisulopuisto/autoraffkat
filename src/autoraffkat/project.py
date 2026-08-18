"""Projektikohtaiset asetukset.

Tallennetaan JSONina lähde-XML:n viereen, jotta seuraava jakso alkaa edellisen
asetuksilla. Avaimena on median tiedostonimi, ei XML:n resurssi-id, koska id:t
vaihtuvat joka viennillä.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .model import Globals, TrackConfig

FORMAT_VERSION = 1


def settings_path(xml_path: str) -> str:
    base, _ = os.path.splitext(os.path.abspath(xml_path))
    return f"{base}.autoraffkat.json"


def default_output_path(xml_path: str) -> str:
    base, ext = os.path.splitext(os.path.abspath(xml_path))
    return f"{base}-leikattu{ext or '.fcpxml'}"


@dataclass
class ProjectSettings:
    tracks: dict[str, TrackConfig] = field(default_factory=dict)
    globals: Globals = field(default_factory=Globals)

    def config_for(self, key: str) -> TrackConfig:
        cfg = self.tracks.get(key)
        if cfg is None:
            cfg = TrackConfig()
            self.tracks[key] = cfg
        return cfg

    def to_json(self) -> dict:
        return {
            "version": FORMAT_VERSION,
            "globals": self.globals.to_json(),
            "tracks": {k: v.to_json() for k, v in self.tracks.items()},
        }

    @classmethod
    def from_json(cls, data: dict) -> "ProjectSettings":
        tracks = {k: TrackConfig.from_json(v)
                  for k, v in (data.get("tracks") or {}).items()
                  if isinstance(v, dict)}
        return cls(tracks=tracks, globals=Globals.from_json(data.get("globals") or {}))


def load(xml_path: str) -> ProjectSettings:
    path = settings_path(xml_path)
    if not os.path.exists(path):
        return ProjectSettings()
    try:
        with open(path, encoding="utf-8") as fh:
            return ProjectSettings.from_json(json.load(fh))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        # Rikkinäinen asetustiedosto ei saa estää työskentelyä.
        return ProjectSettings()


def save(xml_path: str, settings: ProjectSettings) -> str:
    path = settings_path(xml_path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings.to_json(), fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path
