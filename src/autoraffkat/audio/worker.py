"""Äänenkäsittely omassa prosessissaan.

Liitännäinen ladataan **pääsäikeessä**, ja se on vaatimus eikä tyylikysymys:
pedalboard kieltäytyy lataamasta VST3:a muualta virheeseen «must be reloaded
on the main thread». Palvelimen pääsäie ajaa tapahtumasilmukkaa, eikä sitä voi
varata minuuteiksi, joten käsittely ei voi tapahtua palvelimen sisällä
lainkaan. Se ajetaan lapsiprosessissa, jonka pääsäie on vapaa tekemään työtä.

Sama ratkaisu antaa toisen asian ilmaiseksi: kolmannen osapuolen koodi ei enää
kaada palvelinta. Liitännäinen, joka kaatuu tai jää jumiin, vie mukanaan vain
tämän prosessin, ja emo huomaa sen paluuarvosta.

Yhteys on rivipohjaista JSONia stdoutissa — edistyminen sitä mukaa kuin se
syntyy, ja lopuksi yksi tulosrivi. Ei jaettua muistia eikä pickleä: sanoma on
luettavissa myös terminaalista, kun jokin menee pieleen.
"""

from __future__ import annotations

import json
import sys
import traceback


def main() -> int:
    """Lukee tehtävän stdinistä, kirjoittaa edistymisen ja tuloksen stdoutiin."""
    from ..analysis import build_grid, resolve_roles
    from ..fcpxml.read import read_fcpxml
    from ..project import ProjectSettings
    from . import mix

    spec = json.load(sys.stdin)

    def emit(kind: str, payload: dict) -> None:
        sys.stdout.write(json.dumps({"kind": kind, **payload}) + "\n")
        sys.stdout.flush()

    try:
        timeline = read_fcpxml(spec["xml_path"])
        settings = ProjectSettings.from_json(spec["settings"])
        audio = settings.audio
        roles = resolve_roles(timeline, settings.tracks)

        grid, program_start = None, 0.0
        if audio.duck:
            from ..analysis import analyze

            analysis = analyze(timeline)
            grid, start, _ = build_grid(analysis, settings.tracks, roles)
            program_start = float(start)

        result = mix.process(
            timeline,
            roles,
            audio,
            grid=grid,
            program_start=program_start,
            progress=lambda info: emit("progress", info),
            force=bool(spec.get("force")),
        )
        emit(
            "done",
            {
                "processed": result.processed,
                "skipped": result.skipped,
                "gains": result.gains,
                "errors": result.errors,
                "replacements": result.replacements,
                "room": [list(pair) for pair in result.room],
                "program_trim": result.program_trim,
            },
        )
        return 0
    except Exception as exc:  # lapsiprosessi ei saa kadota sanomatta mitään
        traceback.print_exc(file=sys.stderr)
        emit("failed", {"error": f"{type(exc).__name__}: {exc}"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
