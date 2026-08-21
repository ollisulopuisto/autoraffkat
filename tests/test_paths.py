"""Polkumääritysten testit kehitysympäristössä ja PyInstaller-paketissa."""

import sys
from pathlib import Path

import pytest

from autoraffkat import paths


def test_get_resource_path_dev_mode():
    """Kehitystilassa staattiset tiedostot löytyvät paketin sisältä."""
    static_dir = paths.get_resource_path("server/static")
    assert static_dir.exists()
    assert (static_dir / "index.html").exists()
    assert (static_dir / "app.js").exists()


def test_get_resource_path_frozen_mode(monkeypatch, tmp_path):
    """PyInstaller-paketissa (_MEIPASS määritelty) polku ratkaistaan väliaikaishakemistosta."""
    fake_meipass = tmp_path / "meipass_test"
    fake_meipass.mkdir()
    fake_static = fake_meipass / "server" / "static"
    fake_static.mkdir(parents=True)
    (fake_static / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    resolved = paths.get_resource_path("server/static")
    assert resolved == fake_static
    assert (resolved / "index.html").exists()


def test_get_app_dir_dev_vs_frozen(monkeypatch, tmp_path):
    """App-hakemiston haku palauttaa oikean juuren."""
    app_dir = paths.get_app_dir()
    assert app_dir.exists()

    fake_exec = tmp_path / "bin" / "autoraffkat"
    fake_exec.parent.mkdir(parents=True)
    fake_exec.touch()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exec))

    frozen_app_dir = paths.get_app_dir()
    assert frozen_app_dir == fake_exec.parent
