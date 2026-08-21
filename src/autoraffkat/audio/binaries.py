"""ffmpeg- ja ffprobe-binäärien paikannus pakatusta sovelluksesta tai järjestelmästä."""

import os
import shutil
import sys
from pathlib import Path


def get_binary_path(name: str) -> str:
    """Etsii suoritettavan binäärin (esim. ffmpeg tai ffprobe).

    Tarkistusjärjestys:
    1. PyInstallerin purkuhakemisto (``sys._MEIPASS / bin`` tai ``sys._MEIPASS``)
    2. Suoritettavan tiedoston vieressä oleva hakemisto tai sen ``bin/``
    3. Järjestelmän ``PATH``
    """
    bin_name = f"{name}.exe" if os.name == "nt" else name

    # 1. PyInstaller _MEIPASS
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        meipass_path = Path(sys._MEIPASS)
        for candidate in (meipass_path / "bin" / bin_name, meipass_path / bin_name):
            if candidate.is_file():
                return str(candidate)

    # 2. Suoritettavan tiedoston viereiset hakemistot
    if getattr(sys, "frozen", False):
        exec_dir = Path(sys.executable).resolve().parent
        for candidate in (exec_dir / "bin" / bin_name, exec_dir / bin_name):
            if candidate.is_file():
                return str(candidate)

    # 3. Järjestelmän PATH
    which_path = shutil.which(bin_name) or shutil.which(name)
    if which_path:
        return which_path

    raise FileNotFoundError(
        f"{name} puuttuu polusta. Asenna: brew install ffmpeg (macOS) tai lataa ffmpeg (Windows)."
    )


def require_ffmpeg() -> None:
    """Varmistaa että sekä ffmpeg että ffprobe ovat käytettävissä."""
    for tool in ("ffmpeg", "ffprobe"):
        get_binary_path(tool)
