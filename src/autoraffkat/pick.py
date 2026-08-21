"""Lähde-XML:n valinta ilman polun kirjoittamista.

Tavallinen työjärjestys on: vie Final Cutista hakemistoon, aja työkalu samassa
hakemistossa. Polun kirjoittaminen on siinä pelkkää kitkaa, joten se etsitään:
hakemistosta löytyvä ainoa vienti kelpaa sellaisenaan, useammasta kysytään, ja
jos hakemisto on tyhjä, avataan Finderin valintaikkuna.

Final Cut vie joko yksittäisen ``.fcpxml``-tiedoston tai ``.fcpxmld``-paketin,
jonka sisällä XML on nimellä ``Info.fcpxml``. Molemmat kelpaavat lähteeksi ja
molemmat tarkoittavat tässä samaa asiaa.
"""

from __future__ import annotations

import os
import subprocess
import sys

from .project import OUTPUT_SUFFIX

BUNDLE_EXT = ".fcpxmld"
BUNDLE_INNER = "Info.fcpxml"
XML_EXT = ".fcpxml"


def resolve(path: str) -> str:
    """Käyttäjän antama polku varsinaiseksi XML-tiedostoksi.

    ``.fcpxmld``-paketti on hakemisto, joten sen sisältä otetaan XML. Näin
    työkalulle voi antaa sen mitä Finderissa näkyy.
    """
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isdir(path):
        inner = os.path.join(path, BUNDLE_INNER)
        if os.path.exists(inner):
            return inner
    return path


def _is_output(path: str) -> bool:
    """Onko tiedosto tämän työkalun oma vienti."""
    base, _ = os.path.splitext(os.path.basename(path))
    return base.endswith(OUTPUT_SUFFIX)


def candidates(directory: str) -> list[str]:
    """Hakemiston lähdekelpoiset viennit, uusin ensin.

    Omat viennit jätetään pois: silmukassa palataan aina alkuperäiseen
    lähteeseen, eikä valmis leikkaus ole se.
    """
    found: list[str] = []
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return []
    for name in entries:
        full = os.path.join(directory, name)
        if name.endswith(BUNDLE_EXT) and os.path.isdir(full):
            inner = os.path.join(full, BUNDLE_INNER)
            if os.path.exists(inner) and not _is_output(full):
                found.append(inner)
        elif name.endswith(XML_EXT) and os.path.isfile(full):
            if not _is_output(full):
                found.append(full)
    found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return found


def label(path: str) -> str:
    """Näyttönimi: paketista sen oma nimi, ei sisällön ``Info.fcpxml``."""
    if os.path.basename(path) == BUNDLE_INNER:
        return os.path.basename(os.path.dirname(path))
    return os.path.basename(path)


def interactive() -> bool:
    """Onko vastaajaa.

    Ilman päätettä ei saa kysyä mitään eikä varsinkaan avata valintaikkunaa:
    putkessa tai ajastettuna se jäisi odottamaan käyttäjää, jota ei ole.
    """
    try:
        return sys.stdin.isatty() and sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def ask(paths: list[str]) -> str | None:
    """Numeroitu valinta terminaalissa. Tyhjä rivi valitsee uusimman."""
    if not interactive():
        return paths[0]
    print("Useampi vienti tässä hakemistossa:\n", file=sys.stderr)
    for index, path in enumerate(paths, 1):
        print(f"  {index}. {label(path)}", file=sys.stderr)
    print("", file=sys.stderr)
    try:
        answer = input("Numero (Enter = 1, uusin): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("", file=sys.stderr)
        return None
    if not answer:
        return paths[0]
    if answer.isdigit() and 1 <= int(answer) <= len(paths):
        return paths[int(answer) - 1]
    print(f"Ei valintaa: {answer!r}", file=sys.stderr)
    return None


# AppleScript kelpuuttaa myös paketin, koska ``.fcpxmld`` on Finderille
# tiedosto. Jos tyyppirajaus ei löydä mitään, kysytään hakemistoa: silloin
# käyttäjällä on paketti, jota järjestelmä ei tunne paketiksi.
_CHOOSE_FILE = '''
try
    set f to choose file with prompt {prompt} of type {{"fcpxml", "fcpxmld"}} {start}
    return POSIX path of f
on error number -128
    return ""
end try
'''

_CHOOSE_FOLDER = '''
try
    set f to choose folder with prompt {prompt} {start}
    return POSIX path of f
on error number -128
    return ""
end try
'''


def _osascript(script: str) -> str | None:
    """Ajaa AppleScriptin. Palauttaa tulosteen tai ``None`` jos ei onnistu."""
    try:
        done = subprocess.run(["osascript", "-e", script],
                              capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.strip()


def native(directory: str = "", force: bool = False) -> str | None:
    """Finderin valintaikkuna. ``None`` jos peruttiin tai ei ole macOS.

    ``force`` on ``--pick``: silloin ikkuna avataan vaikka pääte puuttuisi,
    koska käyttäjä nimenomaan pyysi sitä.
    """
    if sys.platform != "darwin":
        return None
    if not force and not interactive():
        return None
    prompt = '"Valitse Final Cutista viety FCPXML"'
    start = (f'default location POSIX file "{directory}"' if directory else "")
    for template in (_CHOOSE_FILE, _CHOOSE_FOLDER):
        result = _osascript(template.format(prompt=prompt, start=start))
        if result:
            return resolve(result.rstrip("/"))
        if result == "":
            return None            # käyttäjä perui, ei yritetä uudestaan
    return None


def pick(directory: str) -> str | None:
    """Lähde ilman argumenttia: yksi löytyi, useampi kysytään, tyhjästä ikkuna."""
    found = candidates(directory)
    if len(found) == 1:
        return found[0]
    if found:
        return ask(found)
    return native(directory)
