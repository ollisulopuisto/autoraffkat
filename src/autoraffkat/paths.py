"""Tiedosto- ja resurssipolkujen ratkaisu kehitystilassa ja pakatuissa sovelluksissa."""

import sys
from pathlib import Path


def get_resource_path(relative_path: str | Path) -> Path:
    """Ratkaisee resurssin (kuten staattisen web-sisällön) polun.

    PyInstaller-paketissa resurssit puretaan väliaikaiseen ``sys._MEIPASS``-hakemistoon.
    Kehitystilassa käytetään moduulin suhteellista polkua.
    """
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base_dir = Path(sys._MEIPASS)
    else:
        base_dir = Path(__file__).resolve().parent

    return base_dir / relative_path


def get_app_dir() -> Path:
    """Palauttaa sovelluksen pääasiallisen asennus- tai suoritushakemiston."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]
