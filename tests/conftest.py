import pathlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_fixture  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg puuttuu")

# Final Cutin omat DTD:t. Ne ovat ainoa tapa tarkistaa vienti sillä
# mittapuulla, jolla Final Cut sen hylkää — oma lukija hyväksyy paljon
# enemmän kuin tuonti.
DTD_DIR = pathlib.Path(
    "/Applications/Final Cut Pro.app/Contents/Frameworks/Interchange.framework"
    "/Versions/A/Resources"
)
HAS_DTD = DTD_DIR.is_dir() and shutil.which("xmllint") is not None
needs_dtd = pytest.mark.skipif(
    not HAS_DTD, reason="Final Cutin DTD tai xmllint puuttuu"
)


def dtd_for(version: str) -> pathlib.Path | None:
    """Version mukainen DTD, tai uusin saatavilla oleva."""
    exact = DTD_DIR / f"FCPXMLv{version.replace('.', '_')}.dtd"
    if exact.exists():
        return exact
    found = sorted(
        DTD_DIR.glob("FCPXMLv1_*.dtd"), key=lambda p: int(p.stem.split("_")[-1])
    )
    return found[-1] if found else None


@pytest.fixture
def validate_fcpxml(tmp_path):
    """Tarkistaa XML:n Final Cutin DTD:tä vasten. Ohitetaan jos sitä ei ole."""

    def check(xml: str, name: str = "out.fcpxml"):
        if not HAS_DTD:
            pytest.skip("Final Cutin DTD tai xmllint puuttuu")
        version = re.search(r'<fcpxml version="([^"]+)"', xml)
        dtd = dtd_for(version.group(1) if version else "1.10")
        assert dtd is not None, "DTD:tä ei löytynyt"
        # DTD kopioidaan, koska sen polussa on välilyöntejä.
        local = tmp_path / "fcpxml.dtd"
        local.write_bytes(dtd.read_bytes())
        path = tmp_path / name
        path.write_text(xml, encoding="utf-8")
        done = subprocess.run(
            ["xmllint", "--noout", "--dtdvalid", str(local), str(path)],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, done.stderr.strip()
        return path

    return check


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory):
    """Syntetisoitu aineisto: mediat vain jos ffmpeg löytyy."""
    target = tmp_path_factory.mktemp("fixture")
    media = {
        name: str(target / filename)
        for name, filename in (
            ("wide", "WIDE.mp4"),
            ("close_a", "CLOSE_A.mp4"),
            ("close_b", "CLOSE_B.mp4"),
            ("mic_a", "MIC_A.wav"),
            ("mic_b", "MIC_B.wav"),
        )
    }
    if HAS_FFMPEG:
        return Path(make_fixture.build(str(target))["sync"]).parent
    # Ilman ffmpegiä kirjoitetaan pelkät XML:t; lukija ei tarvitse mediaa.
    make_fixture.write_sync_clip_xml(str(target / "sync.fcpxml"), media)
    make_fixture.write_project_xml(str(target / "project.fcpxml"), media)
    make_fixture.write_multicam_xml(
        str(target / "multicam.fcpxml"), make_fixture.make_parts(str(target), media)
    )
    return target


@pytest.fixture
def scratch_xml(fixture_dir, tmp_path):
    """Kopio lähde-XML:stä omaan hakemistoon.

    Asetukset ja vienti kirjoitetaan XML:n viereen, joten jaettu hakemisto
    vuotaisi tilaa testien välillä.
    """

    def copy(name="sync.fcpxml"):
        target = tmp_path / name
        shutil.copy(fixture_dir / name, target)
        return target

    return copy
