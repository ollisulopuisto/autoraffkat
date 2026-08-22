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


def get_app_icon_path() -> Path | None:
    """Ratkaisee työpöytäsovelluksen ikkuna- ja telakkakuvakkeen polun.

    macOS:ssa suositaan .icns- tai .png-tiedostoa.
    Windowsissa suositaan .ico-tiedostoa.
    """
    is_mac = sys.platform == "darwin"
    is_win = sys.platform.startswith("win")

    if is_mac:
        preferred = (
            "assets/autoraffkat.icns",
            "assets/icon.icns",
            "assets/icon_512.png",
            "assets/icon_1024.png",
            "server/static/icon.png",
            "server/static/apple-touch-icon.png",
        )
    elif is_win:
        preferred = (
            "assets/autoraffkat.ico",
            "assets/icon.ico",
            "server/static/favicon.ico",
        )
    else:
        preferred = (
            "assets/icon_512.png",
            "assets/icon_256.png",
            "assets/icon_128.png",
            "server/static/icon.png",
            "assets/autoraffkat.ico",
            "server/static/favicon.ico",
        )

    # 1. Resurssipolku (PyInstaller sys._MEIPASS tai paketin suhteellinen polku)
    for rel in preferred:
        p = get_resource_path(rel)
        if p.is_file():
            return p

    # 2. Työtilan / asennuksen juuri (kehitystila)
    app_root = get_app_dir()
    for rel in preferred:
        p = app_root / rel
        if p.is_file():
            return p

    # 3. Staattisten tiedostojen kansio varavaihtoehtona
    static_dir = get_resource_path("server/static")
    for name in ("icon.png", "favicon.ico", "apple-touch-icon.png", "favicon.png"):
        p = static_dir / name
        if p.is_file():
            return p

    return None
