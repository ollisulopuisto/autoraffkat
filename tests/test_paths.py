"""Polkumääritysten testit kehitysympäristössä ja PyInstaller-paketissa."""

import sys

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


def test_get_app_icon_path_mac(monkeypatch):
    """macOS-alustalla palautetaan .icns tai .png-kuvakepolku."""
    monkeypatch.setattr(sys, "platform", "darwin")
    icon = paths.get_app_icon_path()
    assert icon is not None
    assert icon.exists()
    assert icon.suffix in (".icns", ".png")


def test_get_app_icon_path_windows(monkeypatch):
    """Windows-alustalla palautetaan .ico-kuvakepolku."""
    monkeypatch.setattr(sys, "platform", "win32")
    icon = paths.get_app_icon_path()
    assert icon is not None
    assert icon.exists()
    assert icon.suffix == ".ico"


def test_get_app_icon_path_frozen(monkeypatch, tmp_path):
    """Pakattuna haetaan kuvake _MEIPASS-hakemistosta."""
    fake_meipass = tmp_path / "meipass_icon_test"
    fake_assets = fake_meipass / "assets"
    fake_assets.mkdir(parents=True)
    (fake_assets / "autoraffkat.icns").touch()
    (fake_assets / "autoraffkat.ico").touch()

    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")

    icon = paths.get_app_icon_path()
    assert icon == fake_assets / "autoraffkat.icns"


def test_the_export_can_be_opened_in_final_cut(tmp_path, monkeypatch):
    """Viennin ainoa jatko on tuonti, joten se on nappi eikä käsin kopiointi.

    Nappi ei saa olla hiljainen: jos ``open`` epäonnistuu — Final Cutia ei
    ole — käyttäjä saa syyn eikä onnistuneen näköistä painallusta.
    """
    import sys

    from fastapi.testclient import TestClient

    from autoraffkat.server import app as server_app
    from autoraffkat.server.app import AppState, create_app

    source = tmp_path / "a.fcpxml"
    source.write_text("<x/>", encoding="utf-8")
    out = tmp_path / "a-cut.fcpxml"
    out.write_text("<x/>", encoding="utf-8")
    client = TestClient(create_app(AppState(xml_path=str(source))))

    calls = []

    class Done:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(server_app.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or Done())
    assert client.post("/api/final-cut", json={"path": str(out)}).json() == {"ok": True}
    assert calls == [["open", "-a", "Final Cut Pro", str(out)]]

    # Puuttuva tiedosto ei ole onnistuminen.
    missing = client.post("/api/final-cut", json={"path": str(tmp_path / "ei.xml")})
    assert missing.status_code == 404

    class Failed:
        returncode = 1
        stderr = "Unable to find application"

    monkeypatch.setattr(server_app.subprocess, "run", lambda cmd, **kw: Failed())
    broken = client.post("/api/final-cut", json={"path": str(out)})
    assert broken.status_code == 400
    assert "Unable to find" in broken.json()["detail"]


def test_the_pan_is_a_track_setting_not_a_calculation(tmp_path):
    """Säätimen arvo ja viennin arvo ovat sama luku.

    Panorointi voisi yhtä hyvin laskea itsensä mittauksesta joka viennissä.
    Silloin kortin säädin näyttäisi arvoa jota vienti ei käytä — juuri se
    vika joka tässä projektissa toistuu. Arvo on siis raidan asetus, jonka
    mittaus **täyttää** kerran napista, ja sen jälkeen se on tavallinen
    luku joka kulkee tallennuksen läpi.
    """
    from autoraffkat.model import ROLE_MIC, TrackConfig

    cfg = TrackConfig(role=ROLE_MIC, speaker="Nyman", pan=-3.0)
    again = TrackConfig.from_json(cfg.to_json())
    assert again.pan == -3.0
    # Vanha asetustiedosto ilman kenttää ei kaadu eikä keksi arvoa.
    assert TrackConfig.from_json({"role": ROLE_MIC, "speaker": "X"}).pan == 0.0
