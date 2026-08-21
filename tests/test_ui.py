"""Käyttöliittymän savutesti.

`node --check` tarkistaa vain syntaksin. Määrittelemätön muuttuja pääsi kerran
läpi: `renderAudio` viittasi poistettuun `busy`-muuttujaan, jolloin koko piirto
keskeytyi ja «Lue uudestaan» jäi ikuisesti kehräämään. Tämä ajaa
piirtofunktiot valeselaimessa oikealla palvelimen tuottamalla tilalla, joten
ajonaikainen virhe kaatuu tänne eikä käyttäjän ruudulle.
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from autoraffkat.model import Globals, TrackConfig, ROLE_CLOSE, ROLE_MIC, ROLE_WIDE
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
