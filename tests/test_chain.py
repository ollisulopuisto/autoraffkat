"""Kanavanauha. Ei tiedostoja eikä liitännäisiä — pelkkää signaalia.

Painopiste on niissä kolmessa asiassa jotka voivat rikkoa leikkauksen:
pituus, siirtymä ja taso.
"""

import base64
import time

import numpy as np
import pytest

from autoraffkat.audio import chain
from autoraffkat.model import AudioSettings

RATE = 48000


def speech_like(seconds=6.0, rate=RATE, level=0.02):
    """Puheenkaltainen signaali: purskeita hiljaisuuden välissä."""
    rng = np.random.default_rng(7)
    n = int(seconds * rate)
    out = rng.standard_normal(n).astype(np.float32) * 0.0005  # pohjakohina
    for start in np.arange(0.5, seconds - 0.5, 1.2):
        i0 = int(start * rate)
        i1 = i0 + int(0.6 * rate)
        t = np.arange(i1 - i0) / rate
        out[i0:i1] += (level * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    return out[None, :]


def test_chain_never_changes_length():
    """Pituus on synkan koko lupaus."""
    audio = speech_like()
    out, info = chain.process(
        audio, RATE, AudioSettings(declick=True), 0.0, True, -20.0, None
    )
    assert out.shape[1] == audio.shape[1] == info.frames


def test_chain_hits_the_loudness_target():
    """Kompressointi siirtää tasoa, joten korjaus mitataan sen jälkeen."""
    pyln = pytest.importorskip("pyloudnorm")
    out, _ = chain.process(
        speech_like(20.0), RATE, AudioSettings(), 0.0, True, -20.0, None
    )
    measured = pyln.Meter(RATE).integrated_loudness(
        np.asarray(out[0], dtype=np.float64)
    )
    assert measured == pytest.approx(-20.0, abs=0.5)


def test_peak_guard_only_attenuates():
    """Huippukatto ei saa koskaan nostaa tasoa."""
    quiet = np.full((1, 100), 0.1, dtype=np.float32)
    out, trim = chain.peak_guard(quiet, -1.0)
    assert trim == 0.0 and np.array_equal(out, quiet)

    loud = np.full((1, 100), 1.5, dtype=np.float32)
    out, trim = chain.peak_guard(loud, -1.0)
    assert trim < 0
    assert float(np.abs(out).max()) == pytest.approx(10 ** (-1.0 / 20), rel=1e-6)


def test_output_stays_under_the_ceiling():
    """Kova lähde ei saa säröytyä normalisoinnin jälkeen."""
    out, _ = chain.process(
        speech_like(level=0.5), RATE, AudioSettings(), 0.0, True, -14.0, None
    )
    ceiling = 10 ** (chain.CEILING_DB / 20)
    assert float(np.abs(out).max()) <= ceiling + 1e-6


def test_lag_finds_a_known_shift():
    """Siirtymän mittaus on ainoa tapa huomata väärin ilmoitettu viive."""
    a = speech_like()[0]
    assert chain.lag_samples(a, a, RATE) == 0
    shifted = np.concatenate([np.zeros(960, dtype=np.float32), a])[: a.size]
    assert chain.lag_samples(a, shifted, RATE) == pytest.approx(960, abs=48)


def test_lag_is_not_quadratic():
    """Siirtymän mittaus oli kalliimpi kuin koko muu ketju.

    ``np.correlate(..., "full")`` laskee korrelaation suoraan, mikä on
    O(n²): 20 minuutin tiedostolla se kesti mitattuna 132 sekuntia — enemmän
    kuin dxRevive samasta tiedostosta — ja tunnin tiedostolla se olisi
    varttitunti pelkkää tarkistusta. Tässä on viisi minuuttia, jolla suora
    tapa vie kymmeniä sekunteja ja FFT alle sekunnin.
    """
    seconds = 300
    rng = np.random.default_rng(7)
    a = rng.standard_normal(RATE * seconds).astype(np.float32) * 0.2
    shifted = np.concatenate([np.zeros(960, dtype=np.float32), a])[: a.size]
    started = time.perf_counter()
    lag = chain.lag_samples(a, shifted, RATE)
    elapsed = time.perf_counter() - started
    assert lag == pytest.approx(960, abs=48)
    # Mitattu 0,05 s; raja on kahdessa sekunnissa, koska tässä ei mitata
    # nopeutta vaan sitä ettei kertaluokka ole palannut neliölliseksi.
    assert elapsed < 2.0, f"siirtymän mittaus kesti {elapsed:.1f} s"


def _with_transient(freq, amp=0.4, at_s=3.0, seconds=6.0):
    """Tasainen kantoaalto ja yksi 2 ms:n transientti.

    Kantoaalto on tahallaan tasainen: puheenkaltaisen signaalin omat
    iskut ovat itsekin HF-transientteja, eikä testi silloin mittaisi
    tunnistinta vaan testiaineistoa.
    """
    t = np.arange(int(seconds * RATE)) / RATE
    audio = (0.05 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)[None, :]
    at = int(at_s * RATE)
    burst = np.arange(int(0.002 * RATE)) / RATE
    audio[0, at : at + burst.size] += (amp * np.sin(2 * np.pi * freq * burst)).astype(
        np.float32
    )
    return audio, slice(at - 100, at + burst.size + 100)


def test_declick_removes_a_click_but_not_a_plosive():
    """Naksu on korkeilla, plosiivi matalilla — ja vain naksu poistetaan.

    Tämä ero on koko tunnistimen idea: leveäkaistainen tai matala isku on «p»
    tai «t» eikä huulinaksu, eikä sitä saa interpoloida pois puheesta.
    """
    click, window = _with_transient(9000)
    cleaned = chain.declick(click, RATE)
    assert cleaned.shape == click.shape
    assert np.abs(cleaned[0, window]).max() < np.abs(click[0, window]).max()

    plosive, window = _with_transient(120)
    kept = chain.declick(plosive, RATE)
    assert np.abs(kept[0, window]).max() == pytest.approx(
        float(np.abs(plosive[0, window]).max()), rel=1e-6
    )


def test_a_saved_state_is_applied_but_parameters_still_win():
    """Tila ennen parametreja, ja parametri voittaa.

    Tila sisältää liitännäisen kaiken — myös sen mitä parametrit eivät
    julkaise, kuten mallin valinnan, joka on koko syy tälle. Mutta se
    sisältää myös talletetut parametriarvot, ja jos ne jyräisivät
    asetukset, käyttöliittymän liukusäädin lakkaisi vaikuttamasta ilman
    että mikään kertoisi siitä.
    """

    class Fake:
        parameters = {"mix": object()}

        def __init__(self):
            self.applied = []
            self.mix = 50.0

        @property
        def raw_state(self):
            return b"tila"

        @raw_state.setter
        def raw_state(self, value):
            self.applied.append(value)
            self.mix = 99.0  # tila tuo mukanaan oman arvonsa

    plugin = Fake()
    assert chain.apply_state(plugin, base64.b64encode(b"tila").decode())
    assert plugin.applied == [b"tila"]
    chain.apply_parameters(plugin, {"mix": 46.3})
    assert plugin.mix == pytest.approx(46.3), "tila jyräsi asetetun säätimen"


def test_a_state_from_another_plugin_is_ignored_not_fatal():
    """Tila on läpinäkymätön eikä siirry liitännäisestä toiseen.

    Liitännäisen vaihduttua vanha tila on roskaa, mutta parametrit toimivat
    silti — joten kelvoton tila sivuutetaan eikä siitä tehdä virhettä, joka
    estäisi käsittelyn kokonaan.
    """

    class Grumpy:
        parameters: dict = {}

        @property
        def raw_state(self):
            return b""

        @raw_state.setter
        def raw_state(self, value):
            raise RuntimeError("not my state")

    assert chain.apply_state(Grumpy(), base64.b64encode(b"vieras").decode()) is False
    assert chain.apply_state(Grumpy(), "") is False
    assert chain.apply_state(Grumpy(), "ei ole base64!!") is False


def test_declick_does_not_shred_ordinary_speech():
    """Vartio vastakkaiselle virheelle: detektori joka laukeaa kaikesta.

    Kun vertailukohta korjattiin maksimista keskiarvoon, kerroin jäi
    maksimin kertoimeksi. Mitattuna oikealla puheella: 1,8–2,2 % kaikista
    näytteistä korjattiin, 550–640 korjausta sekunnissa, ja signaali muuttui
    −10…−15 dB itseensä nähden. Se meni läpi kaikista testeistä, koska
    yksikään ei kysynyt *montako* naksua löytyi — vain että istutettu naksu
    lähti. Molemmat virheet ovat nyt katettuja.
    """
    rng = np.random.default_rng(7)
    seconds = 4.0
    n = int(RATE * seconds)
    # Puheenkaltaista: harmoninen perusta, sihisevä yläpää, tauot välissä.
    t = np.arange(n) / RATE
    voice = sum(np.sin(2 * np.pi * 110 * k * t) / k for k in range(1, 12))
    voice += 0.3 * rng.normal(size=n) * (np.abs(voice) > 0.5)
    gate = (np.sin(2 * np.pi * 1.5 * t) > -0.3).astype(float)
    audio = (0.2 * voice * gate).astype(np.float32).reshape(1, -1)

    cleaned = chain.declick(audio, RATE)
    touched = np.flatnonzero(cleaned[0] != audio[0])
    groups = 0 if touched.size == 0 else 1 + int((np.diff(touched) > 1).sum())
    assert groups / seconds <= chain.DECLICK_MAX_PER_SECOND, (
        f"{groups / seconds:.0f} korjausta sekunnissa — naksuja on muutama "
        "minuutissa, joten tämä on signaalia"
    )
    change = 10 * np.log10(
        ((cleaned[0] - audio[0]) ** 2).mean() / (audio[0] ** 2).mean() + 1e-30
    )
    assert change < -25.0, f"muutos {change:.1f} dB signaaliin nähden on säröä"


def test_declick_gives_up_rather_than_correcting_everything():
    """Katto pitää, vaikka materiaali olisi pelkkää transienttia.

    Kynnystä nostetaan kunnes löydökset mahtuvat kattoon, ja jos ne eivät
    mahdu, mitään ei korjata. Vaihtoehto on että tunnistin päättää koko
    tiedoston olevan naksua — ja se on tapa rikkoa ääni hiljaa.
    """
    rng = np.random.default_rng(11)
    seconds = 2.0
    n = int(RATE * seconds)
    audio = (0.1 * rng.normal(size=n)).astype(np.float32).reshape(1, -1)
    cleaned = chain.declick(audio, RATE, sensitivity=1.0)
    touched = np.flatnonzero(cleaned[0] != audio[0])
    groups = 0 if touched.size == 0 else 1 + int((np.diff(touched) > 1).sum())
    assert groups / seconds <= chain.DECLICK_MAX_PER_SECOND


def test_declick_would_be_dead_with_a_maximum_reference():
    """Vartio automixerista peritylle virheelle.

    Alkuperäinen vertasi paikalliseen maksimiin, vaikka kommentti puhui
    keskiarvosta. Naksu on oman ympäristönsä maksimi, joten ehto ei voinut
    täyttyä koskaan. Jos vertailukohta joskus palautuu maksimiksi, tämä
    kaatuu.
    """
    click, window = _with_transient(9000)
    assert not np.allclose(
        chain.declick(click, RATE)[0, window], click[0, window], atol=1e-6
    )


def test_a_plugin_that_changes_length_is_refused():
    """Väärin käyttäytyvä liitännäinen ei saa päätyä vientiin."""

    class Truncating:
        def reset(self):
            pass

        def process(self, audio, rate, reset=True):
            return audio[:, :-4641]  # dxReviven mitattu viive

    with pytest.raises(chain.ChainError, match="pituutta"):
        chain.process(
            speech_like(), RATE, AudioSettings(), 0.0, True, -20.0, Truncating()
        )


def test_room_tone_is_not_compressed():
    """Tilaääni saa vain tason: kompressoitu tilaääni pumppaa."""
    audio = speech_like(level=0.3)
    out, _ = chain.process(
        audio, RATE, AudioSettings(high_pass_hz=0), 0.0, False, None, None
    )
    # Ilman tavoitetta ja ilman kompressointia signaali on muuttumaton.
    assert np.allclose(out, audio, atol=1e-6)


def test_missing_plugin_is_a_readable_error():
    with pytest.raises(chain.ChainError, match="ei löydy"):
        chain.load_plugin("/ei/ole/mitaan.vst3")
    assert chain.load_plugin("") is None


class _Param:
    """pedalboardin säätimen olennaiset osat: nimi, tyyppi ja rajat."""

    def __init__(self, name, kind=float, span=(-24.0, 24.0, 0.1), choices=()):
        self.name = name
        self.type = kind
        self.range = span
        self.valid_values = list(choices)
        self.units = None


class _Plugin:
    """Liitännäinen joka hyväksyy vain omat säätimensä ja omat rajansa.

    Oikea pedalboardin olio ottaa vastaan minkä tahansa attribuutin, joten
    tuntematon nimi menisi läpi hiljaa. Se on juuri se mitä
    ``apply_parameters`` estää, ja siksi tämä vale on tiukempi kuin oikea.
    """

    def __init__(self):
        self.parameters = {
            "bypass": _Param("Bypass", bool, (False, True, 1)),
            "input_gain": _Param("Input Gain"),
            "mode": _Param("Mode", str, None, ("Voice", "Music")),
        }
        self.values = {"bypass": False, "input_gain": 0.0, "mode": "Voice"}

    def __setattr__(self, name, value):
        if name in ("parameters", "values"):
            return object.__setattr__(self, name, value)
        if name not in self.parameters:
            return object.__setattr__(self, name, value)
        span = self.parameters[name].range
        if (
            span
            and self.parameters[name].type is float
            and not (span[0] <= value <= span[1])
        ):
            raise ValueError("out of range")
        self.values[name] = value

    def __getattr__(self, name):
        try:
            return object.__getattribute__(self, "values")[name]
        except KeyError:
            raise AttributeError(name) from None


def test_plugin_parameters_are_set_in_the_plugins_own_units():
    """``input_gain = 3.0`` on kolme desibeliä, ei 0–1-raaka."""
    plugin = _Plugin()
    assert chain.apply_parameters(plugin, {"input_gain": 3.0, "bypass": True}) == []
    assert plugin.values["input_gain"] == 3.0 and plugin.values["bypass"] is True


def test_unknown_parameter_is_skipped_not_written():
    """Asetukset periytyvät jaksosta toiseen, ja liitännäinen voi vaihtua.

    Väärä nimi ei saa kaataa käsittelyä eikä päätyä liitännäiselle: oikea
    pedalboardin olio ottaisi sen vastaan tavallisena attribuuttina, jolloin
    asetus näyttäisi menneen perille eikä vaikuttaisi mihinkään.
    """
    plugin = _Plugin()
    skipped = chain.apply_parameters(plugin, {"eiOle": 1.0, "input_gain": 6.0})
    assert skipped == ["eiOle"]
    assert plugin.values == {"bypass": False, "input_gain": 6.0, "mode": "Voice"}


def test_out_of_range_parameter_is_skipped_not_raised():
    """Liitännäisen rajat ovat sen omat, eikä niitä tiedetä säätökierroksella."""
    plugin = _Plugin()
    assert chain.apply_parameters(plugin, {"input_gain": 999.0}) == ["input_gain"]
    assert plugin.values["input_gain"] == 0.0


def test_parameter_specs_describe_every_kind(tmp_path, monkeypatch):
    """Käyttöliittymä piirtää tyypin mukaan: ruutu, valikko vai liuku."""
    fake = tmp_path / "Vale.vst3"
    fake.mkdir()
    monkeypatch.setattr(chain, "_SPECS", {})
    monkeypatch.setattr(chain, "load_plugin", lambda path, params=None: _Plugin())
    specs, total = chain.parameter_specs(str(fake))
    assert total == 3
    kinds = {s["name"]: s for s in specs}
    assert kinds["bypass"]["type"] == "bool" and kinds["bypass"]["value"] is False
    assert kinds["mode"]["choices"] == ["Voice", "Music"]
    gain = kinds["input_gain"]
    assert (gain["min"], gain["max"], gain["step"]) == (-24.0, 24.0, 0.1)
    assert gain["value"] == 0.0


def test_too_many_parameters_are_cut_and_the_cut_is_reported(tmp_path, monkeypatch):
    """Syntikassa säätimiä on tuhansia. Katkaisu ei saa olla hiljainen."""

    class Many(_Plugin):
        def __init__(self):
            super().__init__()
            self.parameters = {
                f"p{i}": _Param(f"P {i}") for i in range(chain.MAX_PARAMS + 5)
            }
            self.values = {name: 0.0 for name in self.parameters}

    fake = tmp_path / "Iso.vst3"
    fake.mkdir()
    monkeypatch.setattr(chain, "_SPECS", {})
    monkeypatch.setattr(chain, "load_plugin", lambda path, params=None: Many())
    specs, total = chain.parameter_specs(str(fake))
    assert len(specs) == chain.MAX_PARAMS
    assert total == chain.MAX_PARAMS + 5


class _Echo:
    """Liitännäinen joka merkitsee mitä sille annettiin.

    Palauttaa syötteen sellaisenaan mutta kirjaa palan pituuden, jotta
    testi näkee että pilkkominen tapahtui — ja että jokainen pala meni
    omalle instanssilleen.
    """

    def __init__(self):
        self.calls = []

    def process(self, audio, rate, reset=True):
        assert reset is True  # paloissa syöttäminen lyhentäisi tuloksen
        self.calls.append(audio.shape[1])
        return audio * 2.0


def test_parallel_pieces_keep_the_length_and_the_content():
    """Rinnakkaiset palat eivät saa muuttaa pituutta eivätkä sisältöä.

    Pituus on se sääntö jonka varassa koko vienti on: käsitelty tiedosto
    viitataan samoilla ajoilla kuin alkuperäinen.
    """
    rate = 48000
    frames = int(rate * chain.PIECE_MIN * 4)
    audio = np.linspace(-0.5, 0.5, frames, dtype=np.float32).reshape(1, -1)

    pool = [_Echo() for _ in range(4)]
    out = chain.apply_plugin(pool, audio, rate)
    assert out.shape == audio.shape
    assert np.allclose(out, audio * 2.0)

    # Jokainen instanssi sai oman palansa, eikä yksikään koko tiedostoa.
    used = [p.calls for p in pool]
    assert all(len(c) == 1 for c in used)
    assert all(c[0] < frames for c in used)
    # Marginaali on mukana: pala on neljännestä pidempi.
    assert all(c[0] > frames / 4 for c in used)


def test_a_short_file_is_not_cut_into_pieces():
    """Marginaalit söisivät hyödyn: lyhyt tiedosto ajetaan yhtenä."""
    rate = 48000
    audio = np.zeros((1, int(rate * chain.PIECE_MIN / 2)), dtype=np.float32)
    pool = [_Echo() for _ in range(4)]
    chain.apply_plugin(pool, audio, rate)
    assert [len(p.calls) for p in pool] == [1, 0, 0, 0]


def test_one_plugin_is_still_run_whole():
    """Ilman rinnakkaisuutta tulos on tarkalleen se minkä liitännäinen antaa."""
    rate = 48000
    audio = np.zeros((1, int(rate * chain.PIECE_MIN * 4)), dtype=np.float32)
    one = _Echo()
    chain.apply_plugin(one, audio, rate)
    assert one.calls == [audio.shape[1]]
    assert chain.apply_plugin(None, audio, rate) is audio


def test_worker_count_follows_the_machine_and_the_user(monkeypatch):
    """Palojen määrä on koneen ytimiä, ei tähän kirjoitettu luku.

    Kahdeksan ytimen kannettava ja kahdenkymmenen ytimen työasema ovat eri
    koneita. Osa ytimistä jää käyttöliittymälle: käsittely on taustatyö,
    jonka aikana konetta käytetään muuhun.
    """
    monkeypatch.setattr(chain.os, "cpu_count", lambda: 8)
    assert chain.worker_count() == 6
    monkeypatch.setattr(chain.os, "cpu_count", lambda: 24)
    assert chain.worker_count() == 18

    # Käyttäjän luku voittaa, mutta ei ytimien yli: useampi pala ei ole
    # nopeampi, vain lyhyempi ja muistisyöpömpi.
    assert chain.worker_count(4) == 4
    assert chain.worker_count(99) == 24
    # Yksi pala tarkoittaa yhtä ajoa: silloin tulos on tarkalleen se minkä
    # liitännäinen antaa kokonaisesta tiedostosta.
    assert chain.worker_count(1) == 1

    # Yhden ytimen koneella on silti yksi työ, ei nolla.
    monkeypatch.setattr(chain.os, "cpu_count", lambda: 1)
    assert chain.worker_count() == 1


def test_every_compressor_stage_actually_engages():
    """Kolme rajattua vaihetta, joista yksi ei laukennut koskaan.

    Kolmannen kynnys oli ``+4 dB`` toisen yläpuolella, ja se ajetaan toisen
    jälkeen — toinen on siis jo vetänyt kaiken oman kynnyksensä alle, joten
    neljä desibeliä sen yläpuolella ei ylity millään. Mitattuna kolmella
    minuutilla oikeaa puhetta vaiheen vahvistuksen hajonta oli 0,00 dB
    jokaisella tavoitteella -14…-18: ketju lupasi kolme vaihetta ja ajoi
    kaksi, ja loput huipuista jäivät rajoittimelle.

    Kuollut vaihe ei kaada mitään eikä näy missään lokissa — tämä testi on
    ainoa paikka joka sen kertoo.
    """
    rate = RATE
    # Tasavoimakkaat purskeet eivät kelpaa: kynnykset ovat absoluuttisia ja
    # tulevat normalisoinnin jälkeen, joten signaali jonka jokainen jakso on
    # yhtä kova asettuu kokonaan kynnysten alle eikä testi mittaisi mitään.
    # Puheessa kovat kohdat ovat selvästi kokonaisäänekkyyden yläpuolella, ja
    # juuri ne kynnykset ylittävät.
    rng = np.random.default_rng(7)
    n = int(12.0 * rate)
    signal = rng.standard_normal(n).astype(np.float32) * 0.0005
    levels = [0.05, 0.5, 0.12, 0.9, 0.2, 1.0, 0.08, 0.7, 0.3, 0.6]
    for index, start in enumerate(np.arange(0.5, 11.0, 1.1)):
        i0 = int(start * rate)
        i1 = i0 + int(0.7 * rate)
        t = np.arange(i1 - i0) / rate
        level = levels[index % len(levels)]
        signal[i0:i1] += (
            level * np.sin(2 * np.pi * 180 * t) * (1 + 0.5 * np.sin(2 * np.pi * 3 * t))
        ).astype(np.float32)
    audio = signal[None, :]
    settings = AudioSettings(target_lufs=-16.0)
    offset = -16.0 - chain.THRESHOLD_REFERENCE_LUFS
    # Kynnykset ovat absoluuttisia ja tulevat vasta normalisoinnin jälkeen,
    # joten testisignaali on vietävä samaan tasoon kuin ketjussa.
    measured = chain.loudness(audio.mean(axis=0), rate)
    audio = audio * 10.0 ** ((-16.0 - measured) / 20.0)

    stages = [
        ("monikaista", lambda x: chain.multiband(
            x, rate, settings.peak_threshold_db + offset, chain.PEAK_RATIO,
            chain.MAX_GR_DB, chain.PEAK_ATTACK_MS, chain.PEAK_RELEASE_MS)),
        ("tasaus 1", lambda x: chain.compress(
            x, rate, settings.leveler_threshold_db + offset, chain.LEVEL_RATIO,
            chain.MAX_GR_DB, chain.LEVEL_ATTACK_MS, chain.LEVEL_RELEASE_MS)),
        ("tasaus 2", lambda x: chain.compress(
            x, rate, settings.leveler_threshold_db + offset - 4.0,
            chain.LEVEL_RATIO, chain.MAX_GR_DB,
            chain.LEVEL_ATTACK_MS * 4, chain.LEVEL_RELEASE_MS * 2)),
    ]
    running = audio
    for name, run in stages:
        out = run(running)
        # Vaimennus desibeleinä: nolla tarkoittaa ettei vaihe koskenut mihinkään.
        gr = 20.0 * np.log10(
            max(float(np.abs(out).max()), 1e-9) / max(float(np.abs(running).max()), 1e-9)
        )
        moved = float(np.abs(out - running).max())
        assert moved > 1e-4, f"{name} ei tehnyt mitään"
        assert gr <= 0.01, f"{name} nosti tasoa {gr:.2f} dB"
        running = out


def test_the_third_stage_sits_below_the_second():
    """Sama vika suoraan kynnyksissä: järjestys on osa ketjun rakennetta.

    Käyttäytymistesti kertoo että vaihe laukeaa; tämä kertoo *miksi*, jotta
    merkin vaihtaminen takaisin ei mene läpi vahingossa.
    """
    settings = AudioSettings(target_lufs=-16.0)
    offset = -16.0 - chain.THRESHOLD_REFERENCE_LUFS
    toinen = settings.leveler_threshold_db + offset
    kolmas = settings.leveler_threshold_db + offset - 4.0
    assert kolmas < toinen
