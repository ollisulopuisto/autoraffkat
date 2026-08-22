#!/usr/bin/env python3
"""Lataa staattiset ffmpeg- ja ffprobe-binäärit pakkausta varten.

Tukee macOS- ja Windows-alustoja.
"""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

# macOS-binäärit tulevat `ffmpeg-static`-projektin julkaisuista. Sitä käyttää
# samanniminen npm-paketti, joten sitä ladataan päivittäin miljoonia kertoja —
# eli se on jotain muuta kuin yhden ihmisen kotisivu.
#
# Syy vaihtoon: evermeet.cx julkaisee vain x86_64-käännöksiä. Apple Siliconilla
# sellainen toimii vain Rosettan kautta, ja koko työkalun hitain vaihe on juuri
# ffmpeg. Rosettaa ei myöskään ole valmiiksi asennettuna, joten paketti pyysi
# ensimmäisellä ajolla asentamaan sen — tai jäi ilman verhokäyriä.
#
# Versio on naulattu ja tarkistussummat mukana. Käännösten pitää olla samat
# koneesta ja päivästä toiseen, eikä ulkopuolisen palvelimen vaihtama tiedosto
# saa päätyä pakettiin huomaamatta.
MACOS_RELEASE = "b6.1.1"
MACOS_BASE = (
    f"https://github.com/eugeneware/ffmpeg-static/releases/download/{MACOS_RELEASE}"
)
MACOS_SHA256 = {
    (
        "arm64",
        "ffmpeg",
    ): "a90e3db6a3fd35f6074b013f948b1aa45b31c6375489d39e572bea3f18336584",
    (
        "arm64",
        "ffprobe",
    ): "bb2db6f5d8cef919da12fbf592119a987202a8c060a886f3cab091f9cab90b64",
    (
        "x86_64",
        "ffmpeg",
    ): "ebdddc936f61e14049a2d4b549a412b8a40deeff6540e58a9f2a2da9e6b18894",
    (
        "x86_64",
        "ffprobe",
    ): "fa3add0ce901f7241abe0dfc0155d958fc834aca3f8ce61f87cc712ae669c1e0",
}

WINDOWS_ARCHIVE = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"


def _verify(path: Path, expected: str) -> None:
    """Tarkistussumma. Väärä tiedosto ei saa päätyä pakettiin hiljaa."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise SystemExit(
            f"Tarkistussumma ei täsmää: {path.name}\n  odotettu {expected}\n  saatu    {digest}"
        )


def _arch_of(path: Path) -> str:
    """Mach-O-binäärin arkkitehtuuri, tai '?' jos sitä ei saa selville.

    Luetaan otsikosta eikä `file`-komennolla, jotta tämä toimii myös siellä
    missä sitä ei ole.
    """
    with open(path, "rb") as fh:
        head = fh.read(8)
    if len(head) < 8 or head[:4] not in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        return "?"
    cpu = int.from_bytes(head[4:8], "little")
    return {0x0100000C: "arm64", 0x01000007: "x86_64"}.get(cpu, "?")


def download_file(url: str, dest: Path) -> None:
    print(f"Ladataan: {url} -> {dest}")
    urllib.request.urlretrieve(url, str(dest))


def fetch_binaries(
    target_os: str | None = None, output_dir: Path | None = None
) -> Path:
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
            asset_arch = "arm64" if arch == "arm64" else "x64"
            for tool in ("ffmpeg", "ffprobe"):
                tool_dest = dest_dir / tool
                if tool_dest.exists():
                    if _arch_of(tool_dest) == arch:
                        print(f"{tool} on jo olemassa kohteessa {tool_dest}")
                        continue
                    # Väärän arkkitehtuurin binääri on pahempi kuin puuttuva:
                    # se toimii kääntäjän koneella ja hajoaa käyttäjän koneella.
                    print(f"{tool} on väärää arkkitehtuuria, haetaan uudestaan")
                    tool_dest.unlink()

                temp = tmp_path / tool
                download_file(f"{MACOS_BASE}/{tool}-darwin-{asset_arch}", temp)
                _verify(temp, MACOS_SHA256[(arch, tool)])
                shutil.copy2(temp, tool_dest)
                tool_dest.chmod(0o755)
                print(f"Asennettu: {tool_dest}  ({_arch_of(tool_dest)})")

        elif "win" in sys_name:
            # Windows
            ffmpeg_exe = dest_dir / "ffmpeg.exe"
            ffprobe_exe = dest_dir / "ffprobe.exe"
            if ffmpeg_exe.exists() and ffprobe_exe.exists():
                print(f"Windows-binäärit ovat jo olemassa kohteessa {dest_dir}")
                return dest_dir

            zip_url = WINDOWS_ARCHIVE
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
    parser = argparse.ArgumentParser(
        description="Hae staattiset ffmpeg- ja ffprobe-binäärit."
    )
    parser.add_argument(
        "--os", choices=["darwin", "windows", "linux"], help="Kohdekäyttöjärjestelmä"
    )
    parser.add_argument("--dest", type=Path, help="Kohdehakemisto (oletus: ./bin)")
    args = parser.parse_args()

    fetch_binaries(target_os=args.os, output_dir=args.dest)


if __name__ == "__main__":
    main()
