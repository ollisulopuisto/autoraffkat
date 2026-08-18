import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import make_fixture  # noqa: E402

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg puuttuu")


@pytest.fixture(scope="session")
def fixture_dir(tmp_path_factory):
    """Syntetisoitu aineisto: mediat vain jos ffmpeg löytyy."""
    target = tmp_path_factory.mktemp("fixture")
    media = {name: str(target / filename) for name, filename in (
        ("wide", "WIDE.mp4"), ("close_a", "CLOSE_A.mp4"), ("close_b", "CLOSE_B.mp4"),
        ("mic_a", "MIC_A.wav"), ("mic_b", "MIC_B.wav"))}
    if HAS_FFMPEG:
        return Path(make_fixture.build(str(target))["sync"]).parent
    # Ilman ffmpegiä kirjoitetaan pelkät XML:t; lukija ei tarvitse mediaa.
    make_fixture.write_sync_clip_xml(str(target / "sync.fcpxml"), media)
    make_fixture.write_project_xml(str(target / "project.fcpxml"), media)
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
