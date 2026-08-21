"""Käynnistys: lue XML, avaa käyttöliittymä selaimeen."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser

from . import pick
from .server.app import AppState, create_app


def main(argv: list[str] | None = None) -> int:
    """Lukee XML:n, käynnistää palvelimen ja avaa selaimen.

    Ilman argumenttia lähde etsitään työhakemistosta: polun kirjoittaminen on
    kitkaa työjärjestyksessä, jossa vienti on juuri tehty samaan hakemistoon.

    XML luetaan ennen palvelimen käynnistystä, jotta virheellinen tiedosto
    näkyy heti terminaalissa. Verhokäyrät lasketaan taustasäikeessä, joten
    käyttöliittymä aukeaa odottamatta ffmpegiä.
    """
    parser = argparse.ArgumentParser(
        prog="autoraffkat",
        description="Automaattinen monikameraleikkaus: FCPXML sisään, FCPXML ulos.")
    parser.add_argument("xml", nargs="?",
                        help="Final Cutista viety FCPXML tai .fcpxmld-paketti. "
                             "Ilman tätä etsitään työhakemistosta.")
    parser.add_argument("--pick", action="store_true",
                        help="avaa Finderin valintaikkuna")
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true",
                        help="älä avaa selainta")
    args = parser.parse_args(argv)

    here = os.getcwd()
    if args.pick:
        chosen = pick.native(here, force=True)
    elif args.xml:
        chosen = pick.resolve(args.xml)
    else:
        chosen = pick.pick(here)
    if not chosen:
        print("Ei lähdettä. Vie Final Cutista FCPXML tähän hakemistoon, "
              "anna polku argumenttina tai valitse ikkunasta: "
              "autoraffkat --pick", file=sys.stderr)
        return 1

    xml_path = os.path.abspath(chosen)
    if not os.path.exists(xml_path):
        print(f"Tiedostoa ei löydy: {xml_path}", file=sys.stderr)
        return 1
    if not args.xml:
        print(f"Lähde: {pick.label(xml_path)}")

    state = AppState(xml_path=xml_path)
    state.load()
    if state.load_error:
        print(state.load_error, file=sys.stderr)
        # Käyttöliittymä avataan silti, jotta virhe näkyy ja XML:n voi vaihtaa.

    app = create_app(state)
    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    print(f"autoraffkat: {url}")

    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
