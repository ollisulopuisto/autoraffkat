"""Syntetisoitu testiaineisto.

Tekee ffmpegillä kaksi mikkiraitaa ja kolme mustaa videota sekä niitä vastaavan
FCPXML:n, jotta koko putki on ajettavissa ilman oikeaa kuvausmateriaalia.
Puhe on siniaaltopurskeita tunnetuissa kohdissa, joten päätöksen oikeellisuus
on tarkistettavissa.
"""

from __future__ import annotations

import os
import subprocess
import sys
from xml.sax.saxutils import quoteattr

FPS = 25
FRAME = "1/25s"

# (alku, loppu) sekunteina — kumpi puhuu milloin
SPEECH_A = [(1.0, 6.0), (12.0, 14.0), (20.0, 26.0), (30.0, 33.0)]
SPEECH_B = [(7.0, 11.0), (13.5, 19.0), (25.0, 29.0)]
DURATION = 36.0


def _bursts(path: str, spans, freq: int, level: float) -> None:
    """Kohina pohjalla, siniaaltopurskeet puheena."""
    parts = [f"anoisesrc=d={DURATION}:c=pink:r=48000:a=0.004[bg]"]
    labels = ["[bg]"]
    for i, (start, end) in enumerate(spans):
        parts.append(
            f"sine=f={freq}:d={end - start}:r=48000,"
            f"volume={level},adelay={int(start * 1000)}|{int(start * 1000)},"
            f"apad=whole_dur={DURATION}[s{i}]"
        )
        labels.append(f"[s{i}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0[out]")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-filter_complex", ";".join(parts),
         "-map", "[out]", "-ac", "1", "-ar", "48000", "-t", str(DURATION), path],
        check=True)


def _video(path: str, color: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", f"color=c={color}:s=1920x1080:r={FPS}",
         "-t", str(DURATION), "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        check=True)


def _asset(rid: str, path: str, has_video: bool, has_audio: bool) -> str:
    attrs = [f'id="{rid}"', f"name={quoteattr(os.path.basename(path))}",
             'start="0s"', f'duration="{int(DURATION * FPS)}/25s"']
    if has_audio:
        attrs += ['hasAudio="1"', 'audioSources="1"', 'audioChannels="1"',
                  'audioRate="48000"']
    if has_video:
        attrs += ['hasVideo="1"', 'videoSources="1"', 'format="r1"']
    return (f"    <asset {' '.join(attrs)}>\n"
            f'      <media-rep kind="original-media" src={quoteattr("file://" + path)}/>\n'
            f"    </asset>")


def write_sync_clip_xml(path: str, media: dict) -> None:
    """Synkkaklippi: kamerat ja mikit laneilla."""
    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
        '<fcpxml version="1.10">',
        "  <resources>",
        f'    <format id="r1" name="FFVideoFormat1080p25" frameDuration="{FRAME}" '
        'width="1920" height="1080"/>',
        _asset("r2", media["wide"], True, False),
        _asset("r3", media["close_a"], True, False),
        _asset("r4", media["close_b"], True, False),
        _asset("r5", media["mic_a"], False, True),
        _asset("r6", media["mic_b"], False, True),
        "  </resources>",
        "  <library>",
        '    <event name="Testi">',
        f'      <sync-clip name="Synkka" format="r1" offset="0s" start="0s" '
        f'duration="{int(DURATION * FPS)}/25s" tcFormat="NDF">',
        f'        <asset-clip ref="r2" offset="0s" name="WIDE" start="0s" '
        f'duration="{int(DURATION * FPS)}/25s"/>',
        f'        <asset-clip ref="r3" lane="1" offset="0s" name="CLOSE_A" start="0s" '
        f'duration="{int(DURATION * FPS)}/25s"/>',
        f'        <asset-clip ref="r4" lane="2" offset="0s" name="CLOSE_B" start="0s" '
        f'duration="{int(DURATION * FPS)}/25s"/>',
        f'        <asset-clip ref="r5" lane="-1" offset="0s" name="MIC_A" start="0s" '
        f'duration="{int(DURATION * FPS)}/25s" audioRole="dialogue.A"/>',
        f'        <asset-clip ref="r6" lane="-2" offset="0s" name="MIC_B" start="0s" '
        f'duration="{int(DURATION * FPS)}/25s" audioRole="dialogue.B"/>',
        "      </sync-clip>",
        "    </event>",
        "  </library>",
        "</fcpxml>",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body) + "\n")


def write_project_xml(path: str, media: dict) -> None:
    """Projekti: samat mediat spinellä ja liitettyinä, mikit siirrettyinä."""
    frames = int(DURATION * FPS)
    body = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<!DOCTYPE fcpxml>",
        '<fcpxml version="1.10">',
        "  <resources>",
        f'    <format id="r1" name="FFVideoFormat1080p25" frameDuration="{FRAME}" '
        'width="1920" height="1080"/>',
        _asset("r2", media["wide"], True, False),
        _asset("r3", media["close_a"], True, False),
        _asset("r4", media["close_b"], True, False),
        _asset("r5", media["mic_a"], False, True),
        _asset("r6", media["mic_b"], False, True),
        "  </resources>",
        "  <library>",
        '    <event name="Testi">',
        '      <project name="Kasin synkattu">',
        f'        <sequence format="r1" duration="{frames}/25s" tcStart="0s" '
        'tcFormat="NDF" audioLayout="stereo" audioRate="48k">',
        "          <spine>",
        f'            <asset-clip ref="r2" offset="0s" name="WIDE" start="25/25s" '
        f'duration="{frames - 25}/25s" format="r1" tcFormat="NDF">',
        f'              <asset-clip ref="r3" lane="1" offset="25/25s" name="CLOSE_A" '
        f'start="25/25s" duration="{frames - 25}/25s" format="r1"/>',
        f'              <asset-clip ref="r4" lane="2" offset="25/25s" name="CLOSE_B" '
        f'start="25/25s" duration="{frames - 25}/25s" format="r1"/>',
        f'              <asset-clip ref="r5" lane="-1" offset="25/25s" name="MIC_A" '
        f'start="25/25s" duration="{frames - 25}/25s" audioRole="dialogue.A"/>',
        f'              <asset-clip ref="r6" lane="-2" offset="25/25s" name="MIC_B" '
        f'start="25/25s" duration="{frames - 25}/25s" audioRole="dialogue.B"/>',
        "            </asset-clip>",
        "          </spine>",
        "        </sequence>",
        "      </project>",
        "    </event>",
        "  </library>",
        "</fcpxml>",
    ]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body) + "\n")


def build(target_dir: str) -> dict:
    os.makedirs(target_dir, exist_ok=True)
    media = {
        "wide": os.path.join(target_dir, "WIDE.mp4"),
        "close_a": os.path.join(target_dir, "CLOSE_A.mp4"),
        "close_b": os.path.join(target_dir, "CLOSE_B.mp4"),
        "mic_a": os.path.join(target_dir, "MIC_A.wav"),
        "mic_b": os.path.join(target_dir, "MIC_B.wav"),
    }
    if not os.path.exists(media["mic_b"]):
        _video(media["wide"], "gray")
        _video(media["close_a"], "navy")
        _video(media["close_b"], "maroon")
        _bursts(media["mic_a"], SPEECH_A, 220, 0.30)
        _bursts(media["mic_b"], SPEECH_B, 330, 0.30)
    sync_xml = os.path.join(target_dir, "sync.fcpxml")
    project_xml = os.path.join(target_dir, "project.fcpxml")
    write_sync_clip_xml(sync_xml, media)
    write_project_xml(project_xml, media)
    return {"media": media, "sync": sync_xml, "project": project_xml}


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "fixture"
    info = build(target)
    print(info["sync"])
    print(info["project"])
