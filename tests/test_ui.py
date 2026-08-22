"""Käyttöliittymän savutesti.

`node --check` tarkistaa vain syntaksin. Määrittelemätön muuttuja pääsi kerran
läpi: `renderAudio` viittasi poistettuun `busy`-muuttujaan, jolloin koko piirto
keskeytyi ja «Lue uudestaan» jäi ikuisesti kehräämään. Tämä ajaa
piirtofunktiot valeselaimessa oikealla palvelimen tuottamalla tilalla, joten
ajonaikainen virhe kaatuu tänne eikä käyttäjän ruudulle.
"""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from autoraffkat.model import ROLE_CLOSE, ROLE_MIC, ROLE_WIDE, Globals, TrackConfig
from autoraffkat.server.app import AppState, _state_json, create_app

STATIC = Path(__file__).resolve().parents[1] / "src" / "autoraffkat" / "server" / "static"
SMOKE = Path(__file__).parent / "ui_smoke.js"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node puuttuu")


def _roles():
    return {
        "WIDE": TrackConfig(role=ROLE_WIDE),
        "CLOSE_A": TrackConfig(role=ROLE_CLOSE, speaker="Host"),
        "CLOSE_B": TrackConfig(role=ROLE_CLOSE, speaker="Guest"),
        "host Track1": TrackConfig(role=ROLE_MIC, speaker="Host"),
        "guest Track2": TrackConfig(role=ROLE_MIC, speaker="Guest"),
    }


@needs_node
def test_interface_renders_without_errors(scratch_xml, tmp_path):
    """Jokainen piirtofunktio ajetaan molemmilla kielillä.

    Tila tulee palvelimelta oikeasti eikä käsin kirjoitettuna, joten testi
    huomaa myös sen jos kenttä nimetään uudelleen vain toisessa päässä.
    """
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    state.load()
    for _ in range(200):
        if state.progress.get("ready"):
            break
        time.sleep(0.05)

    from fastapi.testclient import TestClient
    client = TestClient(create_app(state))
    payload = {"tracks": {k: v.to_json() for k, v in _roles().items()},
               "globals": Globals().to_json()}
    latest = client.post("/api/settings", json=payload).json()
    assert latest.get("ok"), latest.get("problems")

    state_file = tmp_path / "state.json"
    latest_file = tmp_path / "latest.json"
    state_file.write_text(json.dumps(_state_json(state)), encoding="utf-8")
    latest_file.write_text(json.dumps(latest), encoding="utf-8")

    done = subprocess.run(
        ["node", str(SMOKE), str(STATIC), str(state_file), str(latest_file)],
        capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr or done.stdout


@needs_node
def test_smoke_catches_an_undefined_variable(tmp_path):
    """Vartio itse vartijalle.

    Jos savutesti ei huomaa määrittelemätöntä muuttujaa, se ei suojaa
    miltään — ja juuri sen se jätti huomaamatta viimeksi.
    """
    broken = tmp_path / "static"
    shutil.copytree(STATIC, broken)
    app = broken / "app.js"
    text = app.read_text(encoding="utf-8")
    marker = "function renderLegend() {"
    assert marker in text
    app.write_text(text.replace(marker, marker + "\n  puuttuvaMuuttuja.x;", 1),
                   encoding="utf-8")

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({
        "tracks": [], "globals": Globals().to_json(), "audio": {},
        "mix": {"progress": {}}, "languages": ["fi", "en"], "language": "fi",
        "kind": "multicam", "fps": 25, "parts": 2,
        "output_path": "/x", "settings_path": "/y", "name": "t",
    }), encoding="utf-8")

    done = subprocess.run(
        ["node", str(SMOKE), str(broken), str(empty), str(empty)],
        capture_output=True, text=True, timeout=120)
    assert done.returncode != 0
    assert "puuttuvaMuuttuja" in (done.stderr + done.stdout)


def _strings():
    """`i18n.js`:n avaimet kielittäin. Luetaan tekstinä, jotta tämä toimii
    ilman nodea: kääntämättömän merkkijonon huomaaminen ei saa olla kiinni
    siitä onko koneella JavaScript-ajoympäristö."""
    text = (STATIC / "i18n.js").read_text(encoding="utf-8")
    out = {}
    for lang in ("fi", "en"):
        start = text.index(f"\n  {lang}: {{") + len(f"\n  {lang}: {{")
        depth, end = 1, start
        while depth:
            if text[end] == "{":
                depth += 1
            elif text[end] == "}":
                depth -= 1
            end += 1
        out[lang] = set(re.findall(r"^\s*'([^']+)':", text[start:end], re.M))
    return out


def test_every_visible_string_is_translated_in_both_languages():
    """Kovakoodattu merkkijono näkyy väärällä kielellä eikä kukaan huomaa.

    Leikkauslistassa luki «Laaja» englanninkielisessä käyttöliittymässä, ja
    kuvien määrä oli suoraan koodissa suomeksi. Tämä ei löydä kovakoodattua
    tekstiä, mutta löytää sen mitä se voi: avaimen joka on vain toisessa
    kielessä, ja avaimen jota koodi kysyy muttei ole olemassa.
    """
    strings = _strings()
    assert strings["fi"], "i18n.js:n avaimia ei löytynyt"
    assert strings["fi"] == strings["en"], (
        f"vain fi: {sorted(strings['fi'] - strings['en'])}, "
        f"vain en: {sorted(strings['en'] - strings['fi'])}")

    app = (STATIC / "app.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r"T\(\s*'([^']+)'", app))
    used |= set(re.findall(r'data-t="([^"]+)"', html))
    # Kootut avaimet (`kind.${...}`) eivät näy suoraan, joten ne tarkistetaan
    # etuliitteen kautta.
    for prefix in re.findall(r"T\(`([a-z]+)\.\$\{", app):
        assert any(k.startswith(prefix + ".") for k in strings["fi"]), prefix
    missing = sorted(used - strings["fi"])
    assert not missing, f"i18n.js:stä puuttuu: {missing}"


def test_static_icon_files_exist():
    """Varmistaa että käyttöliittymän tarvitsemat kuvakkeet ovat olemassa."""
    for name in ("favicon.ico", "favicon.png", "favicon.svg", "apple-touch-icon.png", "icon.png"):
        icon_file = STATIC / name
        assert icon_file.is_file(), f"{name} puuttuu staattisista tiedostoista"
        assert icon_file.stat().st_size > 0, f"{name} on tyhjä tiedosto"


def test_root_icon_endpoints_return_ok(scratch_xml):
    """Selainten suoraan kyselemät juuripolut /favicon.ico ja /apple-touch-icon.png toimivat."""
    from fastapi.testclient import TestClient
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    client = TestClient(create_app(state))

    res_ico = client.get("/favicon.ico")
    assert res_ico.status_code == 200
    assert len(res_ico.content) > 0

    res_apple = client.get("/apple-touch-icon.png")
    assert res_apple.status_code == 200
    assert len(res_apple.content) > 0
