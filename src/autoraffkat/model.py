"""Tietomallit: mediat, roolit, asetukset, leikkaukset."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from fractions import Fraction

from .timeline import ZERO

# Analyysin aika-askel sekunteina. Sama arvo verhokäyrässä ja päätöksessä.
HOP = 0.02

ROLE_WIDE = "wide"
ROLE_CLOSE = "close"
ROLE_MIC = "mic"
ROLE_UNUSED = "unused"
ROLES = (ROLE_UNUSED, ROLE_WIDE, ROLE_CLOSE, ROLE_MIC)

OVERLAP_WIDE = "wide"
OVERLAP_HOLD = "hold"
OVERLAP_LOUDER = "louder"
OVERLAP_RULES = (OVERLAP_WIDE, OVERLAP_HOLD, OVERLAP_LOUDER)

# Rytmiprofiilit ja makroasetukset.
RHYTHM_BROADCAST = "broadcast"
RHYTHM_MELLOW = "mellow"
RHYTHM_HECTIC = "hectic"
RHYTHM_CUSTOM = "custom"
RHYTHM_PRESETS = (RHYTHM_BROADCAST, RHYTHM_MELLOW, RHYTHM_HECTIC, RHYTHM_CUSTOM)

RHYTHM_PRESET_VALUES: dict[str, dict[str, float]] = {
    RHYTHM_BROADCAST: {
        "min_shot": 2.5,
        "lead": 0.30,
        "hang": 0.60,
        "wide_every": 14.0,
        "wide_hold": 3.5,
    },
    RHYTHM_MELLOW: {
        "min_shot": 4.5,
        "lead": 0.15,
        "hang": 1.00,
        "wide_every": 22.0,
        "wide_hold": 4.5,
    },
    RHYTHM_HECTIC: {
        "min_shot": 1.4,
        "lead": 0.40,
        "hang": 0.25,
        "wide_every": 8.0,
        "wide_hold": 2.0,
    },
}

# Mitä tehdään, kun yksi puhuja pitää puheenvuoroa liian kauan.
LONGTAKE_RETURN = "return"  # laaja välissä, takaisin samaan puhujaan
LONGTAKE_STAY = "stay"  # laajaan ja siihen jäädään puhujan vaihtoon asti
LONGTAKE_REACTION = "reaction"  # toisen puhujan reaktiokuva välissä, sitten takaisin
LONGTAKE_RULES = (LONGTAKE_RETURN, LONGTAKE_STAY, LONGTAKE_REACTION)

# Jakelualustojen äänekkyyslukemat. Nämä eivät ole makuasioita vaan
# alustojen normalisointitasoja: kovempi vienti vain vaimennetaan toistossa,
# hiljaisempi jää muiden alle. Vapaa säädin jää silti, koska kaikki jakelu ei
# ole näitä kahta.
LOUDNESS_TARGETS: dict[str, float] = {
    "youtube": -14.0,
    "streaming": -16.0,  # Spotify, Apple Podcasts
    "broadcast": -23.0,  # EBU R128
}


# Viedyn projektin oletusnimi. Se näkyy Final Cutin selaimessa, eli on yhtä
# lailla käyttäjälle näkyvää tekstiä kuin viennin tiedostonimi. Asetuksissa
# oleva nimi voittaa tämän ja periytyy jaksosta toiseen, joten vaihtuminen
# koskee vain uusia projekteja.
DEFAULT_PROJECT_NAME = "Rough cut"


@dataclass
class Placement:
    """Yksi esiintymä aikajanalla.

    ``offset`` on aikajanan hetki, jossa klipin ``start`` osuu. Lähdeaika t
    (asset-aikapohjassa) vastaa siis aikajanan hetkeä ``offset + (t - start)``.
    """

    offset: Fraction
    start: Fraction
    duration: Fraction
    lane: int = 0

    @property
    def end(self) -> Fraction:
        return self.offset + self.duration

    def covers(self, seconds: Fraction) -> bool:
        return self.offset <= seconds < self.end

    def source_at(self, seconds: Fraction) -> Fraction:
        return self.start + (seconds - self.offset)


@dataclass
class MediaItem:
    """Yksi XML:stä löytynyt media ja sen esiintymät aikajanalla.

    Kaikki ajat ovat Fraction-sekunteja. ``asset_start`` on lähdemateriaalin
    ensimmäinen hetki asset-aikapohjassa; tiedoston t=0 vastaa sitä. Verhokäyrä
    indeksoidaan tiedostoajalla, aikajana asset-ajalla, ja ero on tämä.
    """

    key: str  # vakaa tunniste asetusten talletusta varten
    name: str
    path: str  # dekoodattu tiedostopolku, "" jos puuttuu
    src: str  # alkuperäinen media-rep src (file://...)
    asset_start: Fraction = ZERO
    asset_duration: Fraction = ZERO
    has_video: bool = False
    has_audio: bool = False
    width: int = 0
    height: int = 0
    frame_duration: Fraction | None = None
    audio_rate: int = 48000
    audio_channels: int = 2
    audio_sources: int = 1
    video_sources: int = 1
    asset_id: str = ""  # lähde-XML:n resurssi-id
    format_id: str = ""
    placements: list[Placement] = field(default_factory=list)
    angle_ids: list[str] = field(default_factory=list)  # multicamin angleID:t
    angle_name: str = ""  # kulman nimi, "" jos ei multicam

    @property
    def timeline_start(self) -> Fraction:
        return min((p.offset for p in self.placements), default=ZERO)

    @property
    def timeline_end(self) -> Fraction:
        return max((p.end for p in self.placements), default=ZERO)

    def placement_at(self, seconds: Fraction) -> Placement | None:
        for p in self.placements:
            if p.covers(seconds):
                return p
        return None

    def file_time_at(self, seconds: Fraction) -> Fraction | None:
        """Aikajanan hetkeä vastaava aika tiedoston alusta, tai None."""
        p = self.placement_at(seconds)
        if p is None:
            return None
        return p.source_at(seconds) - self.asset_start


@dataclass
class TrackConfig:
    """Käyttäjän antama rooli yhdelle medialle."""

    role: str = ROLE_UNUSED
    speaker: str = ""  # lähikuvan ja mikin yhdistävä nimi
    sensitivity_db: float = 12.0  # dB pohjakohinan yli
    gain_db: float = 0.0  # vahvistuksen korjaus

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "TrackConfig":
        known = {
            f: data[f]
            for f in ("role", "speaker", "sensitivity_db", "gain_db")
            if f in data
        }
        return cls(**known)


@dataclass
class Globals:
    """Koko leikkausta koskevat säätimet."""

    rhythm: str = RHYTHM_BROADCAST
    min_shot: float = 2.5  # lyhin kuvan kesto, s
    lead: float = 0.30  # ennakko / J-cut: leikataan näin paljon ennen puheen alkua, s
    hang: float = (
        0.60  # häntä / L-cut: pidetään kuva näin kauan puheen loppumisen jälkeen, s
    )
    confirm: float = 0.40  # vahvistusaika: puheen on jatkuttava näin kauan, s
    overlap_rule: str = OVERLAP_WIDE
    dominance_db: float = 5.0  # vaadittu ero päällekkäispuheessa
    min_overlap: float = 0.50  # lyhin päällekkäispuhe joka laukaisee säännön, s
    # Pitkä puheenvuoro: yhtä lähikuvaa ei jakseta katsoa loputtomiin.
    wide_every: float = (
        14.0  # katkaise laajaan näin pitkän pätkän jälkeen, 0 = ei koskaan
    )
    wide_hold: float = 3.5  # laajan kesto ennen paluuta, s (vain «return»)
    long_take_rule: str = LONGTAKE_RETURN
    # --- Reaktiokuvat -------------------------------------------------
    # Spekulatiivinen kerros: kuuntelijan lähikuva kesken toisen puheen.
    # Ei osa peruleikkausta, vaan omalle lanelleen, jotta sen voi poistaa
    # yhdellä valinnalla ilman uutta vientiä. Ks. CLAUDE.md.
    reactions: bool = False
    reaction_detector: str = "vision"
    # Kynnys on z-luku jakson omasta jakaumasta, ei absoluuttinen: mikään
    # mitattavista ei tarkoita samaa kahdessa eri huoneessa.
    # Portti: pään asennon suurin sallittu poikkeama perusasennosta. Tämä on
    # se säädin joka ratkaisee — ks. reactions.TURN_MAX.
    reaction_turn_max: float = 0.057
    reaction_threshold: float = -1.0
    reaction_length: float = 1.6      # kuvan kesto, s
    reaction_spacing: float = 25.0    # lyhin väli kahden välillä, s
    # Painot. Näitä on tarkoitus säätää, ja siksi mittaukset ovat
    # välimuistissa pisteiden sijaan: säätö ei maksa uutta purkua.
    # Portin läpäisseiden järjestys. Suoruus edellä; loput pieninä, koska
    # mitattuna ne eivät erottele hyvää huonosta.
    reaction_turn: float = 1.0
    reaction_gaze: float = 0.0
    reaction_smile: float = 0.3
    reaction_eyes: float = 0.0
    reaction_motion: float = 0.2
    reaction_size: float = 0.0

    project_name: str = DEFAULT_PROJECT_NAME
    # Kirjoitetaanko säätimet viennin tiedostonimeen. Samasta jaksosta
    # syntyy monta leikkausta, ja Final Cutin selaimessa nimi on ainoa mikä
    # ne erottaa toisistaan.
    name_tags: bool = True

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "Globals":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class AudioSettings:
    """Äänenkäsittely: kanavanauha mikeille.

    Käsittely ei koske analyysiä. Verhokäyrät luetaan aina raa'asta äänestä,
    koska kompressori nostaa pohjakohinaa sanojen välissä ja tasoittaa juuri
    sen eron, johon herkkyys ja päällekkäispuheen sääntö nojaavat.
    """

    enabled: bool = False
    high_pass_hz: float = 80.0
    # Jokainen mikki nostetaan ensin samaan äänekkyyteen. Ilman tätä
    # kompressorin kynnykset ovat mielivaltaisia: käsittelemätön podcast-mikki
    # on helposti -40 LUFS, jolloin -18 dB:n kynnys ei ylity kertaakaan.
    # Tavoite koskee ohjelmaa eikä yhtä stemiä, ks. ``program_target``: kaksi
    # tavoitteeseen normalisoitua mikkiä summautuu sen yli, tällä aineistolla
    # mitattuna 1,7 dB. Lopullinen taso asetetaan silti Final Cutissa.
    # Tämä on **ohjelman** taso, ei stemin. YouTube normalisoi valmiin
    # videon -14:ään, ja `program_target` muuntaa sen stemin tavoitteeksi
    # mitatulla trimmillä: kahdella puhujalla -1,8 dB, jolloin stemit
    # asettuvat -15,8:aan ja summa -13:een. Ero on olennainen — -14 LUFS
    # suoraan mono-puhestemille jättää noin 14 dB crestiä ja kuulostaa
    # ahdetulta, kun sama ohjelmatasona jättää 17,5 dB.
    #
    # (Vertailun vuoksi: Applen podcast-standardi on -16, mutta se koskee
    # ääntä ilman kuvaa. Tämä menee YouTubeen.) Nimetyt vaihtoehdot
    # ovat ``LOUDNESS_TARGETS``; säädin jää silti vapaaksi, koska jakelu ei
    # ole aina jompikumpi näistä.
    target_lufs: float = -14.0
    peak_threshold_db: float = -12.0  # nopea, 30 ms
    leveler_threshold_db: float = -18.0  # hidas, 300 ms
    declick: bool = False  # maiskaukset ja huulinaksut pois
    # Naksujen herkkyys. Tämä riippuu puhujasta enemmän kuin mikään muu
    # ketjun arvo: toiset maiskuttavat, toiset eivät lainkaan.
    declick_sensitivity: float = 0.5
    # Ulkoinen VST3/AU-liitännäinen, ajetaan ketjun ensimmäisenä. Tässä
    # tehdään kohinanpoisto ja restaurointi: ketjussa itsessään ei ole
    # kohinanvaimennusta, koska hyvä sellainen on liitännäinen.
    plugin_path: str = ""
    # Liitännäisen omat säätimet: nimi -> arvo liitännäisen omissa
    # yksiköissä (dB, %, päällä/pois), ei 0–1-raakana. pedalboard osaa
    # muunnoksen itse (``plugin.input_gain = 3.0``), ja luettava arvo on
    # myös se mikä päätyy asetustiedostoon ja viennin metatietoon.
    #
    # Vain käyttäjän koskemat säätimet ovat täällä; muut jäävät liitännäisen
    # omiin oletuksiin. Tuntematon nimi ohitetaan äänettömästi, koska
    # asetukset periytyvät jaksosta toiseen ja liitännäinen voi olla vaihtunut.
    plugin_params: dict = field(default_factory=dict)
    # Toisen mikin vaimennus kun puhuja on hiljaa. Ohjaus tulee samasta
    # puheentunnistuksesta kuin kuvan leikkaus, mutta omilla ajoillaan: kuva
    # odottaa vahvistusaikaa ennen leikkausta, portin on avauduttava heti.
    # Ristivuodon vähennys: sama ääni toisessa mikissä muutama millisekunti
    # myöhemmin on kampasuodin, kun raidat soivat yhdessä. Vaimennus ei tätä
    # korjaa — mitattuna ääretönkin vaimennus siirsi aaltoilua 0,2 dB, koska
    # maskin aukot ovat juuri puheenvuorojen vaihdoissa. Ks. audio/debleed.py.
    # Liitännäisen oma tila base64:nä, talletettuna sen omasta ikkunasta.
    # Kaikki mikä vaikuttaa lopputulokseen ei ole parametri: dxRevivella
    # mallin valinta ei ole yksikään sen neljästä parametrista. Ks.
    # audio/editor.py.
    plugin_state: str = ""
    debleed: bool = True
    duck: bool = False
    # Kuinka paljon hiljemmalle, 0 = ei mitään. Yhdeksän desibeliä on
    # tahallisen matala: vuoto on jo valmiiksi ~13 dB puheen alla, joten
    # syvempi vaimennus muuttaa summaa mitatusti alle 0,1 dB. Kaikki hyöty
    # tulee ajoituksesta, ei syvyydestä — ja matala vaimennus tekee
    # vähemmän vahinkoa jos tunnistus joskus erehtyy. Säädettävissä siltä
    # varalta että mikit ovat lähempänä toisiaan tai huone on eläväisempi.
    duck_db: float = -9.0
    duck_lookahead: float = 0.15  # avaa näin paljon ennen puheen alkua, s
    duck_hold: float = 0.40  # pidä auki näin kauan puheen jälkeen, s
    duck_min_open: float = 0.20  # tätä lyhyempi jakso ei avaa porttia, s
    # Kuinka lähellä kovinta mikin on oltava pysyäkseen auki. Tämä on se
    # säädin joka erottaa puhujat: pelkkä kynnys ei riitä, koska molemmat
    # mikit kuulevat molemmat puhujat.
    duck_dominance_db: float = 6.0
    # Vaimennus piilotetaan toisen mikin avautumisen taakse: lasku alkaa vasta
    # kun toinen ääni on jo tullut, jolloin sitä ei kuule. Paluu on hitaampi,
    # koska se osuu hiljaisuuteen eikä siinä ole mitään mikä peittäisi sen.
    duck_fade: float = 0.25  # lasku, s — hidas, koska se on peitossa
    duck_release: float = 0.40  # paluu, s
    duck_min_closed: float = 0.60  # tätä lyhyempää vaimennusta ei tehdä, s
    gain_db: float = 0.0  # yhteinen trimmi kaikille mikeille
    # Tavoitetaso koskee ohjelmaa, ei yhtä stemiä. Kaksi -14 LUFS:n mikkiä
    # summautuu tämän yli — mitattuna -12,3 — koska puhujat menevät osittain
    # päällekkäin ja mikit kuulevat toisiaan. Päällä summa osuu tavoitteeseen
    # ja stemit jäävät sen verran hiljemmalle; pois päältä jokainen tiedosto
    # osuu tavoitteeseen yksinään. Ks. ``mix.program_trim``.
    program_target: bool = True
    # Liitännäinen käyttää yhtä ydintä, joten tiedosto pilkotaan tämän verran
    # paloiksi jotka ajetaan rinnakkain omilla instansseillaan. ``0`` on
    # automaattinen eli osuus koneen ytimistä (``chain.WORKER_SHARE``), ``1``
    # tarkoittaa yhtenä palana — silloin tulos on tarkalleen se minkä
    # liitännäinen antaa yhdellä ajolla. Marginaalit ja mitattu ero ovat
    # ``chain.apply_plugin``issa.
    plugin_workers: int = 0
    room_track: str = ""  # tilaäänen raita-avain, "" = ei tilaääntä
    room_db: float = -18.0  # tilaääni näin paljon puhetta hiljempaa

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict) -> "AudioSettings":
        fields = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclass
class Segment:
    """Yksi kuva valmiissa leikkauksessa, aikajanan sekunneissa."""

    angle: str  # median key, tai "" jos kuvaa ei löydy
    label: str  # puhujan nimi tai "laaja" — esikatselua varten
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class Speaker:
    """Puhuja: yksi mikki ja enintään yksi lähikuva."""

    name: str
    mic_keys: list[str] = field(default_factory=list)
    close_key: str | None = None
