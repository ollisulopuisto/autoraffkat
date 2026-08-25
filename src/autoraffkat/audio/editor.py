"""Liitännäisen oma ikkuna omassa prosessissaan.

    python -m autoraffkat.audio.editor   # tehtävä stdinissä, tulos stdoutissa

**Miksi tämä on erillinen prosessi.** ``show_editor`` saa saman rajoituksen
kuin lataus: se on kutsuttava pääsäikeestä, ja se **estää** sen kunnes
käyttäjä sulkee ikkunan. Palvelimen pääsäie ajaa tapahtumasilmukkaa, joten
sitä ei voi varata siksi aikaa kun joku katselee liitännäistä. Lapsen
pääsäie on vapaa, ja jos liitännäisen käyttöliittymä kaatuu, se vie
mukanaan vain tämän prosessin. Sama ratkaisu kuin ``audio/worker.py``:ssä
ja samasta syystä.

**Miksi tätä tarvitaan ollenkaan.** Liitännäisen säädettävät parametrit
eivät ole koko sen tila. dxRevive julkaisee neljä parametria — ohitus,
tulo- ja lähtövahvistus, ja Mix — mutta **mallin valinta ei ole yksikään
niistä**. Malli on liitännäisen omassa tilassa, ja siihen pääsee käsiksi
vain liitännäisen omalla käyttöliittymällä. Ilman tätä ajamme aina sitä
mallia, jonka liitännäinen sattuu ottamaan oletuksena, emmekä voi edes
kertoa kummasta on kyse — ja eri malli on eri lopputulos.

Tila luetaan ``raw_state``:sta ja talletetaan asetuksiin base64:nä. Se on
läpinäkymätön tavujono, jonka vain liitännäinen itse osaa tulkita, ja
siksi se on sidottu ``plugin_path``iin: toisen liitännäisen tila ei ole
tälle mitään. Väärä tila ei kaada mitään — ``load_plugin`` sivuuttaa sen
ja jatkaa parametreilla.
"""

from __future__ import annotations

import base64
import json
import sys
import traceback


def main() -> int:
    """Avaa ikkunan ja palauttaa tilan, jonka käyttäjä siihen jätti."""
    from . import chain

    spec = json.load(sys.stdin)

    def emit(payload: dict) -> None:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()

    try:
        plugin = chain.load_plugin(
            spec["plugin_path"], spec.get("params"), spec.get("state")
        )
        if plugin is None:
            emit({"kind": "failed", "error": "no plugin"})
            return 1
        plugin.show_editor()
        emit(
            {
                "kind": "done",
                "state": base64.b64encode(bytes(plugin.raw_state)).decode("ascii"),
                # Parametrit palautetaan myös: käyttäjä on voinut kääntää
                # Mixiä samassa ikkunassa, ja liukusäätimen on seurattava.
                "params": chain.read_parameters(plugin),
            }
        )
        return 0
    except Exception as exc:  # pragma: no cover - riippuu liitännäisestä
        emit({"kind": "failed", "error": f"{exc}", "trace": traceback.format_exc()})
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
