#!/usr/bin/env python3
"""Yhden komennon käännöstyökalu autoraffkat-työpöytäsovellukselle."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def build(clean: bool = False, fetch_bins: bool = True) -> int:
    os.chdir(ROOT_DIR)

    bin_dir = ROOT_DIR / "bin"
    has_ffmpeg = (bin_dir / "ffmpeg").exists() or (bin_dir / "ffmpeg.exe").exists()

    if fetch_bins and not has_ffmpeg:
        print("Haetaan staattiset ffmpeg- ja ffprobe-binäärit...")
        from fetch_binaries import fetch_binaries
        fetch_binaries()

    spec_file = ROOT_DIR / "autoraffkat.spec"
    if not spec_file.exists():
        print(f"Virhe: {spec_file} puuttuu!", file=sys.stderr)
        return 1

    cmd = [sys.executable, "-m", "PyInstaller"]
    if clean:
        cmd.append("--clean")
    cmd.extend(["-y", str(spec_file)])

    print(f"Käännetään PyInstallerilla: {' '.join(cmd)}")
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print("Käännös epäonnistui.", file=sys.stderr)
        return res.returncode

    dist_dir = ROOT_DIR / "dist"
    if sys.platform == "darwin":
        app_bundle = dist_dir / "autoraffkat.app"
        if app_bundle.exists():
            print(f"\nValmis! macOS-sovelluspaketti löytyy polusta:\n  {app_bundle}")
    elif sys.platform.startswith("win"):
        exe_file = dist_dir / "autoraffkat" / "autoraffkat.exe"
        if exe_file.exists():
            print(f"\nValmis! Windows-suoritustiedosto löytyy polusta:\n  {exe_file}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Käännä autoraffkat itsenäiseksi työpöytäsovellukseksi.")
    parser.add_argument("--clean", action="store_true", help="Puhdista väliaikaiset build-hakemistot ennen käännöstä")
    parser.add_argument("--no-fetch", action="store_false", dest="fetch", help="Älä lataa ffmpeg-binäärejä automaattisesti")
    args = parser.parse_args()

    sys.exit(build(clean=args.clean, fetch_bins=args.fetch))


if __name__ == "__main__":
    main()
