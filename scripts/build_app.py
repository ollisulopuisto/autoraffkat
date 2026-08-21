#!/usr/bin/env python3
"""Yhden komennon käännöstyökalu autoraffkat-työpöytäsovellukselle."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def ensure_icons() -> None:
    """Varmistaa että macOS-kuvake (.icns) on olemassa assets-kansiossa."""
    if sys.platform != "darwin":
        return
    assets_dir = ROOT_DIR / "assets"
    if (assets_dir / "autoraffkat.icns").exists() or (assets_dir / "icon.icns").exists():
        return
    if shutil.which("iconutil") is None or not (assets_dir / "icon_1024.png").exists():
        return

    iconset = assets_dir / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    mapping = {
        "icon_16x16.png": assets_dir / "icon_16.png",
        "icon_16x16@2x.png": assets_dir / "icon_32.png",
        "icon_32x32.png": assets_dir / "icon_32.png",
        "icon_32x32@2x.png": assets_dir / "icon_64.png",
        "icon_128x128.png": assets_dir / "icon_128.png",
        "icon_128x128@2x.png": assets_dir / "icon_256.png",
        "icon_256x256.png": assets_dir / "icon_256.png",
        "icon_256x256@2x.png": assets_dir / "icon_512.png",
        "icon_512x512.png": assets_dir / "icon_512.png",
        "icon_512x512@2x.png": assets_dir / "icon_1024.png",
    }
    for target_name, src in mapping.items():
        if src.exists():
            shutil.copy2(src, iconset / target_name)
    res = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(assets_dir / "autoraffkat.icns")])
    if res.returncode == 0:
        shutil.copy2(assets_dir / "autoraffkat.icns", assets_dir / "icon.icns")
    shutil.rmtree(iconset, ignore_errors=True)


def build(clean: bool = False, fetch_bins: bool = True, dmg: bool = False) -> int:
    os.chdir(ROOT_DIR)
    ensure_icons()

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
        if dmg:
            from make_dmg import make_dmg
            return make_dmg(app_path=app_bundle)
    elif sys.platform.startswith("win"):
        exe_file = dist_dir / "autoraffkat" / "autoraffkat.exe"
        if exe_file.exists():
            print(f"\nValmis! Windows-suoritustiedosto löytyy polusta:\n  {exe_file}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Käännä autoraffkat itsenäiseksi työpöytäsovellukseksi.")
    parser.add_argument("--clean", action="store_true", help="Puhdista väliaikaiset build-hakemistot ennen käännöstä")
    parser.add_argument("--no-fetch", action="store_false", dest="fetch", help="Älä lataa ffmpeg-binäärejä automaattisesti")
    parser.add_argument("--dmg", action="store_true", help="Pakkaa valmis paketti myös levykuvaksi (vain macOS)")
    args = parser.parse_args()

    sys.exit(build(clean=args.clean, fetch_bins=args.fetch, dmg=args.dmg))


if __name__ == "__main__":
    main()
