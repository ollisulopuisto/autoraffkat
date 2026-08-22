#!/usr/bin/env python3
"""Levykuva valmiista macOS-sovelluspaketista.

Ajetaan `build_app.py`:n jälkeen: se tekee `dist/autoraffkat.app`, tämä
pakkaa sen jaettavaan muotoon.

Paketti kopioidaan `ditto`lla eikä Pythonin `shutil`illa. PyInstaller
allekirjoittaa paketin käännöksen lopuksi, ja allekirjoitus kattaa myös
laajennetut attribuutit ja symlinkit — tavallinen kopio rikkoo sen, jolloin
Gatekeeper hylkää sovelluksen vasta toisen käyttäjän koneella.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
APP_NAME = "autoraffkat"
VOLUME_NAME = APP_NAME


def _run(cmd: list[str]) -> None:
    """Ajaa komennon ja kaatuu puhuvasti. Levykuvan teko on monta askelta,
    ja hiljaa ohitettu virhe näkyisi vasta rikkinäisenä .dmg:nä."""
    done = subprocess.run(cmd, capture_output=True, text=True)
    if done.returncode != 0:
        raise RuntimeError(
            f"{' '.join(cmd)}\n{done.stderr.strip() or done.stdout.strip()}"
        )


def make_dmg(app_path: Path | None = None, output: Path | None = None) -> int:
    if sys.platform != "darwin":
        print("Levykuva on macOS:n muoto; muualla ei ole mitä tehdä.", file=sys.stderr)
        return 1

    app = app_path or (ROOT_DIR / "dist" / f"{APP_NAME}.app")
    if not app.is_dir():
        print(f"Virhe: {app} puuttuu. Aja ensin scripts/build_app.py.", file=sys.stderr)
        return 1

    target = output or (ROOT_DIR / "dist" / f"{APP_NAME}.dmg")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Väliaikainen hakemisto on levykuvan koko sisältö: mitä tänne pannaan,
    # sen käyttäjä näkee avatessaan. Ei siis mitään muuta.
    with tempfile.TemporaryDirectory(prefix="autoraffkat-dmg-") as staging_dir:
        staging = Path(staging_dir)
        print(f"Kopioidaan {app.name}…")
        _run(["ditto", str(app), str(staging / app.name)])
        # Ohjelmat-kansio vieressä: vetäminen sinne on ainoa asennusohje jota
        # kukaan ei lue mutta kaikki osaavat.
        os.symlink("/Applications", staging / "Applications")

        # Levykuvan oma kuvake jos saatavilla
        for icon_name in ("autoraffkat.icns", "icon.icns"):
            icon_file = ROOT_DIR / "assets" / icon_name
            if icon_file.exists():
                shutil.copy2(icon_file, staging / ".VolumeIcon.icns")
                if shutil.which("SetFile"):
                    _run(["SetFile", "-a", "C", str(staging)])
                break

        if target.exists():
            target.unlink()
        print(f"Pakataan {target.name}…")
        _run(
            [
                "hdiutil",
                "create",
                "-srcfolder",
                str(staging),
                "-volname",
                VOLUME_NAME,
                "-fs",
                "HFS+",
                "-format",
                "UDZO",  # pakattu, vain luku — sama kuin ennen
                "-imagekey",
                "zlib-level=9",
                "-quiet",
                str(target),
            ]
        )

    size = target.stat().st_size / 1e6
    print(f"\nValmis! Levykuva:\n  {target}  ({size:.0f} MB)")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pakkaa dist/autoraffkat.app jaettavaksi levykuvaksi."
    )
    parser.add_argument(
        "--app",
        type=Path,
        default=None,
        help="sovelluspaketti (oletus dist/autoraffkat.app)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="kohdetiedosto (oletus dist/autoraffkat.dmg)",
    )
    args = parser.parse_args()
    if shutil.which("hdiutil") is None:
        print("hdiutil puuttuu.", file=sys.stderr)
        raise SystemExit(1)
    try:
        raise SystemExit(make_dmg(app_path=args.app, output=args.output))
    except RuntimeError as exc:
        print(f"Levykuvan teko epäonnistui:\n{exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
