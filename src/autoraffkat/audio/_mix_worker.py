"""automixerin kanavanauha yhdelle tiedostolle kerrallaan.

Tämä ajetaan **automixerin omassa ympäristössä** eikä autoraffkatin, koska
automixer vaatii Python 3.13:n ja MLX:n eikä leikkaustyökalu saa vaatia
kumpaakaan. Yhteys on siis prosessiraja, ei import: sisään JSON stdinistä,
ulos JSON stdoutiin.

Sisään: ``{"project", "jobs": [{"source", "target", "gain_db", "speech"}], "settings"}``
Ulos:   ``{"done": [{"key", "target", "frames"}], "errors": {"key": "viesti"}}``

``project`` on automixerin juuri. Se lisätään polulle käsin, koska skripti
ajetaan omasta hakemistostaan — silloin ``sys.path[0]`` on tämä hakemisto eikä
työhakemisto, eikä ``src.automixer`` löytyisi.
"""

import json
import os
import sys


def _normalise_gain(signal, rate, target_lufs):
    """Desibelit, joilla signaali osuu tavoiteäänekkyyteen.

    Mitataan ennen kompressointia, koska kynnykset ovat absoluuttisia:
    käsittelemätön mikki on helposti -40 LUFS, eikä -12 dB:n kynnys ylity
    kertaakaan. Hiljainen tai tyhjä raita jätetään rauhaan.
    """
    import numpy as np
    import pyloudnorm as pyln

    data = np.asarray(signal, dtype=np.float64)
    if data.size < rate:                      # alle sekunti: ei mitattavaa
        return 0.0
    try:
        measured = pyln.Meter(rate).integrated_loudness(data)
    except Exception:
        return 0.0
    if not np.isfinite(measured) or measured < -70.0:
        return 0.0
    return float(target_lufs - measured)


def _chain(processor, settings, gain_db, speech):
    """Kanavanauha normalisoinnin jälkeen.

    Mikeille automixerin PIPELINE.md:n järjestys ilman summausta ja ilman
    mainoskatkoa: kumpikin muuttaisi pituutta tai veisi puhujien erottelun.
    Tilaäänelle vain ylipäästö — kompressoitu tilaääni pumppaa.
    """
    chain = []
    if settings.get("high_pass_hz", 0) > 0:
        chain.append(processor.HighPassProcessor(settings["high_pass_hz"]))
    if speech:
        chain.append(processor.CompressorProcessor(
            threshold_db=settings.get("peak_threshold_db", -12.0),
            ratio=2.5, window_sec=0.03))
        chain.append(processor.CompressorProcessor(
            threshold_db=settings.get("leveler_threshold_db", -18.0),
            ratio=1.5, window_sec=0.3))
        if settings.get("declick"):
            chain.append(processor.DeSmackProcessor())
    if gain_db:
        chain.append(processor.GainProcessor(gain_db))
    if speech:
        # Normalisoinnin jälkeen huiput ovat lähellä nollaa ja mikkejä on
        # useampi. Rajoitin on halpa vakuutus siitä ettei summa säröydy.
        chain.append(processor.LimiterProcessor(threshold_db=-1.0))
    return chain


def run(spec):
    import mlx.core as mx
    import numpy as np
    import soundfile as sf

    project = spec.get("project", "")
    if project and project not in sys.path:
        sys.path.insert(0, project)
    try:
        from automixer.domain import processor
    except ImportError:
        from src.automixer.domain import processor

    settings = spec.get("settings", {})
    done, errors = [], {}
    for job in spec.get("jobs", []):
        key, source, target = job["key"], job["source"], job["target"]
        try:
            data, rate = sf.read(source, always_2d=True, dtype="float32")
            frames = data.shape[0]
            if frames == 0:
                raise ValueError(f"Tyhjä äänitiedosto: {source}")
            if job.get("mono") and data.shape[1] > 1:
                # Tilaääni on tunnelmaa eikä stereokuvaa: monona se vie
                # murto-osan tilasta eikä kuulostaudu miltään erilaiselta.
                data = data.mean(axis=1, keepdims=True)
            # Normalisointi mitataan monosummasta ja sama vahvistus annetaan
            # kaikille kanaville, jotta stereokuva ei muutu.
            wanted = job.get("target_lufs")
            lift = (_normalise_gain(data.mean(axis=1), rate, wanted)
                    if wanted is not None else 0.0)

            out = np.empty_like(data)
            for channel in range(data.shape[1]):
                signal = mx.array(np.ascontiguousarray(data[:, channel]))
                if lift:
                    signal = processor.GainProcessor(lift).process(signal, rate)
                # Uusi ketju joka kanavalle: prosessorit voivat pitää tilaa.
                for step in _chain(processor, settings, job.get("gain_db", 0.0),
                                   job.get("speech", True)):
                    signal = step.process(signal, rate)
                result = np.asarray(signal, dtype=np.float32)
                if result.shape[0] != frames:
                    raise ValueError(
                        f"Käsittely muutti pituutta ({frames} -> "
                        f"{result.shape[0]}): {os.path.basename(source)}")
                out[:, channel] = result

            tmp = target + ".tmp.wav"
            sf.write(tmp, out, rate, subtype=job.get("subtype", "PCM_24"))
            written = sf.info(tmp).frames
            if written != frames:
                os.remove(tmp)
                raise ValueError(
                    f"Kirjoitettu tiedosto on eri pituinen ({frames} -> "
                    f"{written}): {os.path.basename(source)}")
            os.replace(tmp, target)
            done.append({"key": key, "target": target, "frames": frames,
                         "channels": out.shape[1], "gain_db": round(lift, 2)})
        except Exception as exc:                    # yksi tiedosto ei kaada muita
            errors[key] = f"{type(exc).__name__}: {exc}"
    return {"done": done, "errors": errors}


if __name__ == "__main__":
    print(json.dumps(run(json.load(sys.stdin))))
    sys.stdout.flush()
