#!/usr/bin/env python3
"""Lataa staattiset ffmpeg- ja ffprobe-binäärit pakkausta varten.

Tukee macOS- ja Windows-alustoja.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
import tarfile
from pathlib import Path

# Tunnetut luotettavat staattiset jakelulähteet
BIN_SOURCES = {
    "darwin-arm64": {
        "ffmpeg": "https://evermeet.cx/ffmpeg/getrelease/zip",
        "ffprobe": "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip",
    },
    "darwin-x86_64": {
        "ffmpeg": "https://evermeet.cx/ffmpeg/getrelease/zip",
        "ffprobe": "https://evermeet.cx/ffmpeg/getrelease/ffprobe/zip",
    },
    "windows-x86_64": {
        "archive": "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    },
}


def download_file(url: str, dest: Path) -> None:
    print(f"Ladataan: {url} -> {dest}")
    urllib.request.urlretrieve(url, str(dest))


def fetch_binaries(target_os: str | None = None, output_dir: Path | None = None) -> Path:
    sys_name = (target_os or platform.system()).lower()
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        arch = "x86_64"

    dest_dir = output_dir or (Path(__file__).resolve().parents[1] / "bin")
    dest_dir.mkdir(parents=True, exist_ok=True)

    print(f"Haetaan binäärit alustalle {sys_name}-{arch} kohteeseen {dest_dir}...")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        if "darwin" in sys_name or "mac" in sys_name:
            # macOS
            for tool in ("ffmpeg", "ffprobe"):
                tool_dest = dest_dir / tool
                if tool_dest.exists():
                    print(f"{tool} on jo olemassa kohteessa {tool_dest}")
                    continue

                url = f"https://evermeet.cx/ffmpeg/getrelease/{tool if tool == 'ffprobe' else ''}/zip"
                zip_path = tmp_path / f"{tool}.zip"
                download_file(url, zip_path)
                with zipfile.ZipFile(zip_path, "r") as z:
                    z.extractall(tmp_path)
                extracted_tool = tmp_path / tool
                if extracted_tool.exists():
                    shutil.copy2(extracted_tool, tool_dest)
                    tool_dest.chmod(0o755)
                    print(f"Asennettu: {tool_dest}")

        elif "win" in sys_name:
            # Windows
            ffmpeg_exe = dest_dir / "ffmpeg.exe"
            ffprobe_exe = dest_dir / "ffprobe.exe"
            if ffmpeg_exe.exists() and ffprobe_exe.exists():
                print(f"Windows-binäärit ovat jo olemassa kohteessa {dest_dir}")
                return dest_dir

            zip_url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
            zip_path = tmp_path / "ffmpeg.zip"
            download_file(zip_url, zip_path)
            with zipfile.ZipFile(zip_path, "r") as z:
                for member in z.namelist():
                    if member.endswith("bin/ffmpeg.exe"):
                        with z.open(member) as src, open(ffmpeg_exe, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        print(f"Asennettu: {ffmpeg_exe}")
                    elif member.endswith("bin/ffprobe.exe"):
                        with z.open(member) as src, open(ffprobe_exe, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        print(f"Asennettu: {ffprobe_exe}")

    return dest_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Hae staattiset ffmpeg- ja ffprobe-binäärit.")
    parser.add_argument("--os", choices=["darwin", "windows", "linux"], help="Kohdekäyttöjärjestelmä")
    parser.add_argument("--dest", type=Path, help="Kohdehakemisto (oletus: ./bin)")
    args = parser.parse_args()

    fetch_binaries(target_os=args.os, output_dir=args.dest)


if __name__ == "__main__":
    main()
