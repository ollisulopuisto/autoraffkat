"""Käyttöliittymän kielet.

Kaksi kieltä, suomi ja englanti. Koodi, kommentit ja docstringit pysyvät
suomeksi — ne ovat tekijöille, eivät käyttäjälle. Käännetään vain se mitä
käyttäjä näkee.

Kieli on ``ContextVar``issa eikä globaalina muuttujana, koska äänenkäsittely
ajetaan taustasäikeessä samaan aikaan kun käyttöliittymä pyytää tilaa.

Palvelinpuolen viestit muotoillaan tässä valmiiksi merkkijonoiksi eikä
lähetetä avaimina selaimelle. Viestit tulevat poikkeuksista kuudesta
moduulista, ja avaimen kuljettaminen jokaisen läpi tekisi virhepolusta
monimutkaisemman kuin onnistumispolusta.
"""

from __future__ import annotations

import locale
import os
from contextvars import ContextVar

LANGUAGES = ("fi", "en")
DEFAULT = "fi"

_current: ContextVar[str] = ContextVar("language", default=DEFAULT)

# Avain -> kieli -> teksti. Nimeäminen: moduuli.asia.
CATALOG: dict[str, dict[str, str]] = {
    "read.no_project": {
        "fi": "XML:stä ei löytynyt projektia eikä synkronoitua klippiä. "
        "Vie Final Cutista joko synkkaklippi tai projekti.",
        "en": "No project or synchronised clip found in the XML. "
        "Export either a sync clip or a project from Final Cut.",
    },
    "read.bad_xml": {
        "fi": "XML ei jäsenny: {error}",
        "en": "The XML does not parse: {error}",
    },
    "read.bad_root": {
        "fi": "Juurielementti on <{tag}>, odotettiin <fcpxml>.",
        "en": "Root element is <{tag}>, expected <fcpxml>.",
    },
    "read.no_media": {
        "fi": "Aikajanalta ei löytynyt yhtään mediaa.",
        "en": "No media found on the timeline.",
    },
    "roles.mic_without_speaker": {
        "fi": "Mikille «{name}» ei ole puhujaa.",
        "en": "Microphone “{name}” has no speaker.",
    },
    "roles.close_without_speaker": {
        "fi": "Lähikuvalle «{name}» ei ole puhujaa.",
        "en": "Close-up “{name}” has no speaker.",
    },
    "roles.no_wide": {
        "fi": "Valitse yksi media laajaksi kuvaksi.",
        "en": "Choose one track as the wide shot.",
    },
    "roles.no_mic": {
        "fi": "Valitse ainakin yksi mikki ja anna sille puhuja.",
        "en": "Choose at least one microphone and name its speaker.",
    },
    "roles.speaker_without_mic": {
        "fi": "Puhujalta «{name}» puuttuu mikki.",
        "en": "Speaker “{name}” has no microphone.",
    },
    "roles.no_closeups": {
        "fi": "Yhdelläkään puhujalla ei ole lähikuvaa, joten koko leikkaus "
        "olisi laajaa. Anna vähintään yhdelle kameralle rooli "
        "«Lähikuva» ja puhujan nimi.",
        "en": "No speaker has a close-up, so the whole edit would be the "
        "wide shot. Give at least one camera the role “Close-up” and "
        "a speaker name.",
    },
    "analysis.no_overlap": {
        "fi": "Laajalla kuvalla ja mikeillä ei ole yhteistä aikaa. "
        "Tarkista roolit ja lähde-XML:n synkkaus.",
        "en": "The wide shot and the microphones share no common time. "
        "Check the roles and the sync in the source XML.",
    },
    "write.empty_cut": {
        "fi": "Leikkauslista on tyhjä.",
        "en": "The cut list is empty.",
    },
    "write.zero_duration": {
        "fi": "Ohjelman kesto on nolla.",
        "en": "The programme duration is zero.",
    },
    "write.cuts_collapsed": {
        "fi": "Leikkauskohdat kutistuivat tyhjiksi.",
        "en": "The cut points collapsed to nothing.",
    },
    "write.media_missing": {
        "fi": "Mediaa ei löydy: {key}",
        "en": "Media not found: {key}",
    },
    "write.no_placement": {
        "fi": "Medialla {key} ei ole paikkaa aikajanalla.",
        "en": "Media {key} has no position on the timeline.",
    },
    "write.no_resources": {
        "fi": "Lähde-XML:stä ei löydy <resources>-lohkoa.",
        "en": "No <resources> block found in the source XML.",
    },
    "write.not_multicam": {
        "fi": "Aikajanalla ei ole monikameraklippejä.",
        "en": "The timeline has no multicam clips.",
    },
    "export.not_loaded": {
        "fi": "XML:ää ei ole luettu.",
        "en": "No XML has been read.",
    },
    "export.would_overwrite": {
        "fi": "Vienti osuisi lähde-XML:n päälle.",
        "en": "The export would overwrite the source XML.",
    },
    "export.inside_bundle": {
        "fi": "Vienti osuisi Final Cutin .fcpxmld-paketin sisään.",
        "en": "The export would land inside Final Cut's .fcpxmld bundle.",
    },
    "export.file_missing": {
        "fi": "Tiedostoa ei ole.",
        "en": "The file does not exist.",
    },
    "export.settings_failed": {
        "fi": "Asetuksia ei voitu tallentaa: {error}",
        "en": "Could not save the settings: {error}",
    },
    "export.audio_running": {
        "fi": "Äänen käsittely on kesken, joten {missing}/{total} "
        "mikkitiedostoa viedään käsittelemättömänä. Vie uudestaan kun "
        "käsittely on valmis — ennen kuin leikkaat Final Cutissa.",
        "en": "Audio processing is still running, so {missing}/{total} "
        "microphone files are exported unprocessed. Export again once "
        "it finishes — before you start editing in Final Cut.",
    },
    "export.audio_missing": {
        "fi": "{missing}/{total} mikkitiedostoa viedään käsittelemättömänä: "
        "käsittelyä ei ole ajettu tai se epäonnistui.",
        "en": "{missing}/{total} microphone files are exported unprocessed: "
        "processing has not been run, or it failed.",
    },
    # Viennin muistiinpano. Tämä menee <sequence>-elementin <note>-kenttään ja
    # näkyy Final Cutissa projektin muistiinpanona, joten se on käyttäjälle
    # näkyvää tekstiä siinä missä käyttöliittymän omat viestit. Säätimien
    # nimet ovat samat kuin selaimen puolella, jotta muistiinpanon voi lukea
    # rinnakkain sen ruudun kanssa jolla arvot asetettiin.
    "export.note": {
        "fi": "autoraffkat {version} · rytmi: {rhythm} · lyhin kuva {min_shot} s · "
        "ennakko {lead} s · häntä {hang} s · päällekkäispuhe: {overlap} · "
        "pitkä puheenvuoro: {longtake} · mikit: {audio} · lähde: {source}",
        "en": "autoraffkat {version} · rhythm: {rhythm} · shortest shot {min_shot} s · "
        "lead {lead} s · hang {hang} s · overlapping speech: {overlap} · "
        "long turn: {longtake} · microphones: {audio} · source: {source}",
    },
    "export.audio_on": {
        "fi": "käsitelty",
        "en": "processed",
    },
    "export.audio_off": {
        "fi": "käsittelemätön",
        "en": "unprocessed",
    },
    # Säätimien arvot muistiinpanossa. Sanamuodot ovat samat kuin
    # `static/i18n.js`:ssä; kaksi luetteloa siksi, että selain ei lue
    # palvelimen katalogia eikä palvelin selaimen.
    "rhythm.broadcast": {"fi": "Tv ja podcast", "en": "Broadcast & Podcast"},
    "rhythm.mellow": {"fi": "Rauhallinen", "en": "Mellow"},
    "rhythm.hectic": {"fi": "Korkeatempoinen", "en": "Hectic"},
    "rhythm.custom": {"fi": "Mukautettu", "en": "Custom"},
    "overlap.wide": {"fi": "Laaja", "en": "Wide"},
    "overlap.hold": {"fi": "Pidä nykyinen", "en": "Hold current"},
    "overlap.louder": {"fi": "Vahvempi voittaa", "en": "Louder wins"},
    "longtake.return": {"fi": "Palaa puhujaan", "en": "Return to speaker"},
    "longtake.stay": {"fi": "Jää laajaan", "en": "Stay wide"},
    "longtake.reaction": {"fi": "Reaktiokuva", "en": "Reaction shot"},
    "audio.envelope_failed": {
        "fi": "Verhokäyrien laskenta epäonnistui: {error}",
        "en": "Computing the envelopes failed: {error}",
    },
    "audio.duck_failed": {
        "fi": "Vaimennusta ei voi ohjata: {error}",
        "en": "Cannot drive the ducking: {error}",
    },
    "audio.plugin_missing": {
        "fi": "Liitännäistä ei löydy: {path}",
        "en": "Plug-in not found: {path}",
    },
    "audio.plugin_failed": {
        "fi": "Liitännäistä ei voitu ladata: {name} — {error}",
        "en": "Could not load the plug-in: {name} — {error}",
    },
    "audio.plugin_length": {
        "fi": "Liitännäinen muutti pituutta ({before} → {after}).",
        "en": "The plug-in changed the length ({before} → {after}).",
    },
    "audio.chain_length": {
        "fi": "Käsittely muutti pituutta ({before} → {after}).",
        "en": "Processing changed the length ({before} → {after}).",
    },
    "audio.extract_failed": {
        "fi": "Äänen purku epäonnistui: {name}",
        "en": "Extracting the audio failed: {name}",
    },
    "audio.empty_file": {
        "fi": "Tyhjä äänitiedosto: {name}",
        "en": "Empty audio file: {name}",
    },
    "audio.source_missing": {
        "fi": "Lähdetiedostoa ei löydy: {path}",
        "en": "Source file not found: {path}",
    },
    "audio.plugin_shifted": {
        "fi": "Liitännäinen siirsi ääntä {samples} näytettä ({ms:.0f} ms): "
        "{name}. Kuva ja ääni erkanisivat, joten tulosta ei käytetä.",
        "en": "The plug-in shifted the audio by {samples} samples "
        "({ms:.0f} ms): {name}. Picture and sound would drift apart, "
        "so the result is not used.",
    },
    "audio.written_length": {
        "fi": "Kirjoitettu tiedosto on eri pituinen ({before} → {after}): {name}.",
        "en": "The written file has a different length ({before} → {after}): {name}.",
    },
}


def detect() -> str:
    """Oletuskieli järjestelmästä. Suomi vain jos järjestelmä on suomeksi."""
    for name in ("AUTORAFFKAT_LANG", "LANGUAGE", "LC_ALL", "LANG"):
        raw = os.environ.get(name, "")
        if raw:
            return "fi" if raw.lower().startswith("fi") else "en"
    try:
        code = locale.getlocale()[0] or ""
    except (ValueError, TypeError):
        code = ""
    return "fi" if code.lower().startswith("fi") else "en"


def normalise(value: str | None) -> str:
    """Kelvollinen kielikoodi, tai oletus."""
    text = (value or "").strip().lower()[:2]
    return text if text in LANGUAGES else DEFAULT


def set_language(value: str | None) -> str:
    _current.set(normalise(value))
    return _current.get()


def language() -> str:
    return _current.get()


def t(key: str, **params) -> str:
    """Käännetty teksti. Tuntematon avain palautuu sellaisenaan.

    Puuttuva avain ei saa kaataa mitään: virheviesti on jo virhepolulla, eikä
    käännösvirhe saa peittää alkuperäistä ongelmaa.
    """
    entry = CATALOG.get(key)
    if entry is None:
        return key
    text = entry.get(language()) or entry.get(DEFAULT) or key
    try:
        return text.format(**params) if params else text
    except (KeyError, IndexError, ValueError):
        return text
