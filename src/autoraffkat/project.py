"""Projektikohtaiset asetukset.

Tallennetaan JSONina lähde-XML:n viereen, jotta seuraava jakso alkaa edellisen
asetuksilla. Avaimena on median tiedostonimi, ei XML:n resurssi-id, koska id:t
vaihtuvat joka viennillä.
"""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field

from .model import AudioSettings, Globals, TrackConfig

FORMAT_VERSION = 1

# Viennin nimen tunnus. Sama vakio molemmissa suunnissa, jotta valmiit
# leikkaukset eivät päädy tarjolle uudeksi lähteeksi.
OUTPUT_SUFFIX = "-leikattu"


SETTINGS_SUFFIX = ".autoraffkat.json"
BUNDLE_EXT = ".fcpxmld"
BUNDLE_INNER = "Info.fcpxml"


def derived_base(xml_path: str) -> str:
    """Tähän lähteeseen kuuluvien tiedostojen kantanimi ilman päätettä.

    ``.fcpxmld`` on Final Cutin oma paketti. Sen sisään ei kirjoiteta mitään:
    paketti kuuluu Final Cutille, ja sen sisältö voi vaihtua viennin mukana.
    Johdetut tiedostot menevät paketin **viereen** ja saavat paketin nimen,
    joka on muutenkin luettavampi kuin ``Info``.
    """
    path = os.path.abspath(xml_path)
    folder = os.path.dirname(path)
    if os.path.basename(path) == BUNDLE_INNER and folder.endswith(BUNDLE_EXT):
        return folder[:-len(BUNDLE_EXT)]
    return os.path.splitext(path)[0]


def settings_path(xml_path: str) -> str:
    """Asetustiedoston polku: ``jakso.fcpxml`` -> ``jakso.autoraffkat.json``."""
    return f"{derived_base(xml_path)}{SETTINGS_SUFFIX}"


def legacy_settings_path(xml_path: str) -> str:
    """Vanha sijainti paketin sisällä. Luetaan, ei kirjoiteta."""
    return f"{os.path.splitext(os.path.abspath(xml_path))[0]}{SETTINGS_SUFFIX}"


def default_output_path(xml_path: str) -> str:
    """Viennin polku: ``jakso.fcpxml`` -> ``jakso-leikattu.fcpxml``.

    Erillinen nimi on tahallinen: vienti ei saa osua lähde-XML:n päälle, koska
    silmukassa palataan aina samaan lähteeseen.
    """
    return f"{derived_base(xml_path)}{OUTPUT_SUFFIX}.fcpxml"


@dataclass
class ProjectSettings:
    """Yhden lähde-XML:n asetukset: raitakohtaiset roolit ja globaalit säätimet."""

    tracks: dict[str, TrackConfig] = field(default_factory=dict)
    globals: Globals = field(default_factory=Globals)
    audio: AudioSettings = field(default_factory=AudioSettings)

    def config_for(self, key: str) -> TrackConfig:
        """Raidan asetukset, oletuksilla luotuna jos raitaa ei ole ennen nähty."""
        cfg = self.tracks.get(key)
        if cfg is None:
            cfg = TrackConfig()
            self.tracks[key] = cfg
        return cfg

    def to_json(self) -> dict:
        return {
            "version": FORMAT_VERSION,
            "globals": self.globals.to_json(),
            "audio": self.audio.to_json(),
            "tracks": {k: v.to_json() for k, v in self.tracks.items()},
        }

    @classmethod
    def from_json(cls, data: dict) -> "ProjectSettings":
        tracks = {k: TrackConfig.from_json(v)
                  for k, v in (data.get("tracks") or {}).items()
                  if isinstance(v, dict)}
        return cls(tracks=tracks,
                   globals=Globals.from_json(data.get("globals") or {}),
                   audio=AudioSettings.from_json(data.get("audio") or {}))


def find_previous(xml_path: str) -> str | None:
    """Lähin aiempi asetustiedosto, tai ``None``.

    Sarjassa jokainen jakso on oma vientinsä mutta sama kokoonpano: samat
    kamerat, samat mikit, samat puhujat. Raita-avaimet johdetaan
    tiedostonimistä, joten ne täsmäävät jaksosta toiseen — silloin edellisen
    jakson roolit ovat oikea oletus, ja tyhjä lomake on väärä.

    Etsintä ei mene syvälle: XML:n oma hakemisto, sen yläpuoli ja yläpuolen
    ``.fcpxmld``-paketit. Kauempaa löytyvä tiedosto olisi arvaus.
    """
    own = settings_path(xml_path)
    here = os.path.dirname(own)
    above = os.path.dirname(here)
    patterns = [
        os.path.join(here, f"*{SETTINGS_SUFFIX}"),
        os.path.join(above, f"*{SETTINGS_SUFFIX}"),
        # Vanhemmat asetukset ovat pakettien sisällä.
        os.path.join(here, f"*{BUNDLE_EXT}", f"*{SETTINGS_SUFFIX}"),
        os.path.join(above, f"*{BUNDLE_EXT}", f"*{SETTINGS_SUFFIX}"),
    ]
    found: set[str] = set()
    for pattern in patterns:
        found.update(glob.glob(pattern))
    found.discard(own)
    if not found:
        return None
    return max(found, key=os.path.getmtime)


def load(xml_path: str) -> ProjectSettings:
    """Lukee asetukset XML:n vierestä.

    Puuttuva tai rikkinäinen tiedosto ei ole virhe vaan tuottaa oletukset:
    asetukset ovat mukavuus, eivät ehto työskentelylle.
    """
    return (read(settings_path(xml_path))
            or read(legacy_settings_path(xml_path))
            or ProjectSettings())


def read(path: str) -> ProjectSettings | None:
    """Lukee yhden asetustiedoston. ``None`` jos sitä ei ole tai se on rikki."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return ProjectSettings.from_json(json.load(fh))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        # Rikkinäinen asetustiedosto ei saa estää työskentelyä.
        return None


def save(xml_path: str, settings: ProjectSettings) -> str:
    """Kirjoittaa asetukset XML:n viereen.

    Kirjoitus tehdään väliaikaistiedoston kautta, koska tämä ajetaan jokaisen
    liukusäätimen liikkeen jälkeen eikä keskeytys saa jättää puolikasta JSONia.
    """
    path = settings_path(xml_path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(settings.to_json(), fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path
