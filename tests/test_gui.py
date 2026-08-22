"""GUI-ikkunan ja taustapalvelimen elinkaaritestit."""

import socket
from unittest.mock import MagicMock

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


def test_launch_gui_passes_icon(monkeypatch, tmp_path):
    """launch_gui välittää sovelluksen kuvakkeen pywebview'lle."""
    import webview

    fake_icon = tmp_path / "custom_icon.icns"
    fake_icon.touch()

    started_kwargs = {}

    def fake_start(*args, **kwargs):
        started_kwargs.update(kwargs)

    mock_win = MagicMock()
    monkeypatch.setattr(gui.paths, "get_app_icon_path", lambda: fake_icon)
    monkeypatch.setattr(webview, "create_window", lambda *a, **kw: mock_win)
    monkeypatch.setattr(webview, "start", fake_start)
    monkeypatch.setattr(
        gui.DesktopServer, "wait_until_ready", lambda self, timeout=5.0: True
    )
    monkeypatch.setattr(gui.DesktopServer, "stop", lambda self: None)

    gui.launch_gui()

    assert started_kwargs.get("icon") == str(fake_icon)
