"""GUI-ikkunan ja taustapalvelimen elinkaaritestit."""

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from autoraffkat import gui
from autoraffkat.server.app import AppState, create_app


def test_find_free_port_standard():
    """Vapaa portti löytyy ja on kelvollinen porttinumero."""
    port = gui.find_free_port(8731)
    assert 1024 <= port <= 65535


def test_find_free_port_fallback():
    """Jos oletusportti on varattu, etsitään seuraava vapaa portti."""
    # Varataan portti väliaikaisesti
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        occupied_port = s.getsockname()[1]

        next_port = gui.find_free_port(occupied_port)
        assert next_port != occupied_port
        assert next_port > occupied_port


def test_desktop_server_lifecycle(scratch_xml):
    """Taustapalvelin käynnistyy säikeessä ja sammuu siististi pyydettäessä."""
    state = AppState(xml_path=str(scratch_xml("multicam.fcpxml")))
    app = create_app(state)

    port = gui.find_free_port(9876)
    server = gui.DesktopServer(app, host="127.0.0.1", port=port)
    server.start()

    try:
        # Odotetaan että palvelin vastaa
        assert server.wait_until_ready(timeout=5.0)
        assert server.url == f"http://127.0.0.1:{port}"
    finally:
        server.stop()
        server.thread.join(timeout=5.0)
        assert not server.thread.is_alive()


def test_window_config():
    """Ikkunan konfiguraatiossa on oikeat oletusmitat ja otsikko."""
    config = gui.get_window_config(title="autoraffkat", url="http://127.0.0.1:8731")
    assert config["title"] == "autoraffkat"
    assert config["url"] == "http://127.0.0.1:8731"
    assert config["width"] >= 960
    assert config["height"] >= 600
    assert config["min_size"] == (960, 600)
