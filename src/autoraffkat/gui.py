"""Natiivi työpöytäikkuna (pywebview) ja taustalla pyörivä FastAPI-palvelin."""

from __future__ import annotations

import socket
import sys
import threading
import time
from typing import Any

import uvicorn

from . import paths
from .server.app import AppState, create_app


def find_free_port(start_port: int = 8731) -> int:
    """Etsii vapaan portin alkaen annetusta portista."""
    port = start_port
    while port < 65535:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1
    raise RuntimeError("Vapaata porttia ei löytynyt.")


class DesktopServer:
    """Taustasäikeessä pyörivä Uvicorn-palvelin."""

    def __init__(self, app: Any, host: str = "127.0.0.1", port: int = 8731):
        self.host = host
        self.port = port
        self.config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            log_level="warning",
            access_log=False,
        )
        self.server = uvicorn.Server(self.config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        self.thread.start()

    def wait_until_ready(self, timeout: float = 5.0) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.server.started:
                return True
            time.sleep(0.02)
        return False

    def stop(self) -> None:
        self.server.should_exit = True


def get_window_config(title: str = "autoraffkat", url: str = "http://127.0.0.1:8731") -> dict:
    """Palauttaa webview-ikkunan oletusasetukset."""
    return {
        "title": title,
        "url": url,
        "width": 1280,
        "height": 820,
        "min_size": (960, 600),
        "background_color": "#16181c",
    }


class DesktopApi:
    """JavaScriptin ja Pythonin välinen rajapinta tiedostovalintoja ja natiividialogeja varten."""

    def __init__(self, window: Any = None):
        self.window = window

    def open_file_dialog(self) -> str | None:
        """Avaa natiivin tiedostonvalintadialogin."""
        if not self.window:
            return None
        import webview
        file_types = ("FCPXML (*.fcpxml;*.fcpxmld;*.xml)", "All files (*.*)")
        result = self.window.create_file_dialog(
            webview.OPEN_DIALOG,
            allow_multiple=False,
            file_types=file_types,
        )
        if result and len(result) > 0:
            return result[0]
        return None


def launch_gui(
    xml_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8731,
    debug: bool = False,
) -> None:
    """Käynnistää taustapalvelimen ja natiivin työpöytäikkunan."""
    import webview

    state = AppState(xml_path=xml_path)
    if xml_path:
        state.load()

    app = create_app(state)
    free_port = find_free_port(port)
    server = DesktopServer(app, host=host, port=free_port)
    server.start()

    if not server.wait_until_ready(timeout=10.0):
        server.stop()
        raise RuntimeError("Palvelimen käynnistys aikakatkaistiin.")

    api = DesktopApi()
    config = get_window_config(title="autoraffkat", url=server.url)

    window = webview.create_window(
        title=config["title"],
        url=config["url"],
        width=config["width"],
        height=config["height"],
        min_size=config["min_size"],
        background_color=config["background_color"],
        js_api=api,
    )
    api.window = window

    def on_closed():
        server.stop()

    window.events.closed += on_closed

    icon_path = paths.get_app_icon_path()
    icon_str = str(icon_path) if icon_path else None

    if sys.platform == "darwin" and icon_str:
        try:
            import AppKit
            ns_img = AppKit.NSImage.alloc().initByReferencingFile_(icon_str)
            if ns_img and ns_img.isValid():
                AppKit.NSApplication.sharedApplication().setApplicationIconImage_(ns_img)
        except Exception:
            pass

    try:
        # webview.start estää kunnes ikkuna suljetaan
        webview.start(debug=debug, icon=icon_str)
    finally:
        server.stop()
        server.thread.join(timeout=2.0)
