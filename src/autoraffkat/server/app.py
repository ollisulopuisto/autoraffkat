"""Paikallinen web-käyttöliittymä.

Analyysi jää palvelimen puolelle, koska se on jo Pythonia ja päätöskerros on
numpya. Selain hoitaa vain säätimet ja piirron. Verhokäyrät lasketaan
taustasäikeessä, jotta roolit voi nimetä heti eikä käyttöliittymä jää odottamaan
ffmpegiä.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import i18n, pick, probe, project, thumbs
from ..analysis import Analysis, AnalysisError, analyze, build_grid, resolve_roles
from ..audio import chain, mix
from ..audio.chain import ChainError
from ..decide import WIDE_LABEL, decide
from ..fcpxml.read import ReadError, Timeline, read_fcpxml
from ..fcpxml.write import (
    WriteError,
    build_fcpxml,
    build_multicam_fcpxml,
    write_fcpxml,
)
from ..i18n import LANGUAGES, t
from ..model import (
    DEFAULT_PROJECT_NAME,
    LONGTAKE_RULES,
    OVERLAP_RULES,
    RHYTHM_PRESETS,
    ROLE_MIC,
    ROLES,
    AudioSettings,
    Globals,
)
from ..paths import get_resource_path
from ..preview import build as build_preview

STATIC_DIR = get_resource_path("server/static")


def _plugin_params(raw) -> dict:
    """Liitännäisen säätimet selaimesta: nimi -> arvo.

    Arvot menevät suoraan ulkopuoliselle liitännäiselle, joten tästä päästää
    läpi vain skalaarit ja korkeintaan sen verran nimiä kuin
    käyttöliittymälle ylipäätään näytetään. Nimiä ei tarkisteta täällä:
    liitännäisen lataus kestää sekunteja eikä sitä tehdä säätökierroksella —
    tuntematon nimi ohitetaan vasta ``chain.apply_parameters``issa.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict = {}
    for name, value in list(raw.items())[: chain.MAX_PARAMS]:
        if isinstance(value, bool):
            out[str(name)] = value
        elif isinstance(value, (int, float)):
            out[str(name)] = float(value)
        elif isinstance(value, str):
            out[str(name)] = value[:120]
    return out


@dataclass
class AppState:
    """Palvelimen tila. Yksi XML kerrallaan.

    ``lock`` suojaa asetukset ja analyysin, koska verhokäyriä lasketaan
    taustasäikeessä samaan aikaan kun käyttöliittymä lähettää säätöjä.
    """

    xml_path: str
    timeline: Timeline | None = None
    analysis: Analysis | None = None
    settings: project.ProjectSettings = field(default_factory=project.ProjectSettings)
    progress: dict = field(
        default_factory=lambda: {"done": 0, "total": 0, "current": "", "ready": False}
    )
    load_error: str = ""
    language: str = field(default_factory=i18n.detect)
    inherited_from: str = ""  # mistä roolit perittiin, "" jos ei mistään
    mix_result: mix.MixResult = field(default_factory=mix.MixResult)
    mix_progress: dict = field(
        default_factory=lambda: {
            "done": 0,
            "total": 0,
            "current": "",
            "stage": "",
            "fraction": 0.0,
            "eta": 0,
            "running": False,
        }
    )
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ---------------------------------------------------------- lataus

    def load(self) -> None:
        """Lukee XML:n ja käynnistää verhokäyrien laskennan taustalle.

        Lukuvirhe ei kaada palvelinta vaan jää ``load_error``iin, jotta
        käyttöliittymä voi näyttää sen ja käyttäjä voi korjata viennin.
        """
        self.load_error = ""
        self.inherited_from = ""
        self.mix_result = mix.MixResult()
        self.progress = {"done": 0, "total": 0, "current": "", "ready": False}
        try:
            timeline = read_fcpxml(self.xml_path)
        except (ReadError, OSError) as exc:
            self.load_error = str(exc)
            self.progress["ready"] = True
            return
        with self.lock:
            self.timeline = timeline
            self.analysis = Analysis(timeline=timeline)
            self.settings = project.load(self.xml_path)
            self._seed_defaults()
            # Kieli vasta perinnän jälkeen: uudella jaksolla ei ole omia
            # asetuksia, ja kieli tulee edellisestä kuten muutkin.
            if self.settings.language:
                self.language = i18n.normalise(self.settings.language)
            self.adopt_mix()
        threading.Thread(target=self._analyze, daemon=True).start()

    def _inherit(self) -> set[str]:
        """Roolit edellisestä jaksosta. Palauttaa täsmänneet raita-avaimet.

        Kamera ei kerro itsestään kumpaa puhujaa se kuvaa, eikä sitä voi
        päätellä XML:stä — mutta edellinen jakso samasta sarjasta kertoo, ja
        raita-avaimet on johdettu tiedostonimistä juuri siksi että ne kestävät
        jaksosta toiseen. Tyhjä lomake on huonompi oletus kuin viime kerran
        kokoonpano.
        """
        assert self.timeline is not None
        source = project.find_previous(self.xml_path)
        previous = project.read(source) if source else None
        if previous is None:
            return set()
        matched = {t.key for t in self.timeline.tracks if t.key in previous.tracks}
        if not matched:
            return set()
        for key in matched:
            self.settings.tracks[key] = previous.tracks[key]
        # Säätimet ovat leikkaajan makua, eivät jakson ominaisuus. Tämä
        # koskee myös ääntä: kanavanauha, liitännäinen ja vaimennus ovat
        # samat viikosta toiseen samalla kokoonpanolla.
        self.settings.globals = previous.globals
        self.settings.audio = previous.audio
        self.settings.language = previous.language
        self.inherited_from = source or ""
        return matched

    def _seed_defaults(self) -> None:
        """Ensimmäisellä avauksella täytetään roolit niin pitkälle kuin voi.

        Järjestys on paras ensin: edellisen jakson roolit, sitten nimistä
        arvaaminen. Puhujaehdotus tulee mikkitiedoston ensimmäisestä sanasta,
        koska äänitteet nimetään käytännössä aina puhujan mukaan. Kameroita ei
        arvata: monikamerassa kulmat ovat ``1``, ``2``, ``3``.
        """
        assert self.timeline is not None
        if self.settings.tracks:
            for track in self.timeline.tracks:
                self.settings.config_for(track.key)
            return
        inherited = self._inherit()
        for track in self.timeline.tracks:
            cfg = self.settings.config_for(track.key)
            if track.key in inherited:
                continue
            lowered = track.name.lower()
            if track.has_audio and not track.has_video:
                cfg.role = ROLE_MIC
                first = track.name.split()[0] if track.name.split() else ""
                if first.isalpha():
                    cfg.speaker = first.capitalize()
            elif track.has_video and any(
                w in lowered for w in ("wide", "laaja", "master")
            ):
                cfg.role = "wide"

    def _analyze(self) -> None:
        """Taustasäie: purkaa äänet ja laskee verhokäyrät. Kerran per lataus."""
        assert self.timeline is not None
        targets = [m for m in self.timeline.media if m.has_audio]
        self.progress.update({"total": len(targets), "done": 0, "ready": False})

        def report(done: int, total: int, current: str) -> None:
            self.progress.update({"done": done, "total": total, "current": current})

        try:
            result = analyze(self.timeline, progress=report)
            with self.lock:
                self.analysis = result
        except Exception as exc:  # taustasäie ei saa kaatua hiljaa
            self.load_error = t("audio.envelope_failed", error=exc)
            traceback.print_exc()
        finally:
            self.progress["ready"] = True

    # ---------------------------------------------------------- päätös

    def apply(self, payload: dict) -> None:
        """Ottaa käyttöliittymän arvot vastaan ja tallentaa ne."""
        tracks = payload.get("tracks") or {}
        for key, values in tracks.items():
            cfg = self.settings.config_for(key)
            role = values.get("role")
            if role in ROLES:
                cfg.role = role
            if "speaker" in values:
                cfg.speaker = str(values["speaker"])[:60]
            if "sensitivity_db" in values:
                cfg.sensitivity_db = float(values["sensitivity_db"])
            if "gain_db" in values:
                cfg.gain_db = float(values["gain_db"])
        raw = payload.get("globals") or {}
        g = self.settings.globals
        for name in (
            "min_shot",
            "lead",
            "hang",
            "confirm",
            "dominance_db",
            "min_overlap",
            "wide_every",
            "wide_hold",
        ):
            if name in raw:
                setattr(g, name, max(0.0, float(raw[name])))
        if raw.get("overlap_rule") in OVERLAP_RULES:
            g.overlap_rule = raw["overlap_rule"]
        if raw.get("long_take_rule") in LONGTAKE_RULES:
            g.long_take_rule = raw["long_take_rule"]
        if raw.get("rhythm") in RHYTHM_PRESETS:
            g.rhythm = raw["rhythm"]
        if "name_tags" in raw:
            g.name_tags = bool(raw["name_tags"])
        self._apply_audio(payload.get("audio") or {})
        if "project_name" in raw:
            g.project_name = str(raw["project_name"])[:120] or DEFAULT_PROJECT_NAME

    def _apply_audio(self, raw: dict) -> None:
        """Äänenkäsittelyn asetukset. Ei käynnistä käsittelyä — se on hidas."""
        a = self.settings.audio
        if "enabled" in raw:
            a.enabled = bool(raw["enabled"])
        if "declick" in raw:
            a.declick = bool(raw["declick"])
        if "duck" in raw:
            a.duck = bool(raw["duck"])
        for name in (
            "duck_db",
            "duck_lookahead",
            "duck_hold",
            "duck_min_open",
            "duck_fade",
            "duck_release",
            "duck_min_closed",
            "duck_dominance_db",
            "declick_sensitivity",
        ):
            if name in raw:
                a.__dict__[name] = float(raw[name])
        for name in (
            "high_pass_hz",
            "target_lufs",
            "peak_threshold_db",
            "leveler_threshold_db",
            "gain_db",
            "room_db",
        ):
            if name in raw:
                a.__dict__[name] = float(raw[name])
        if "plugin_path" in raw:
            wanted = str(raw["plugin_path"]).strip()
            # Tuntematon polku nollataan heti: käsittely kaatuisi siihen
            # vasta minuuttien päästä.
            wanted = wanted if (not wanted or os.path.exists(wanted)) else ""
            # Säätimet kuuluvat siihen liitännäiseen josta ne luettiin.
            # Toisen liitännäisen nimet eivät osu mihinkään, ja jos osuvat,
            # ne osuvat väärään säätimeen.
            if wanted != a.plugin_path:
                a.plugin_params = {}
            a.plugin_path = wanted
        if "plugin_params" in raw:
            a.plugin_params = _plugin_params(raw["plugin_params"])
        if "room_track" in raw:
            keys = {t.key for t in self.timeline.tracks} if self.timeline else set()
            wanted = str(raw["room_track"])
            a.room_track = wanted if wanted in keys else ""

    def adopt_mix(self) -> None:
        """Ottaa levyllä jo olevan käsitellyn äänen tämän istunnon käyttöön.

        Käsittelyn tulos jää lähteen viereen, mutta ``mix_result`` katoaa
        istunnon mukana. Ilman tätä sama jakso uudestaan avattuna vietäisiin
        raakana, vaikka valmis ``[mix]`` on levyllä ja ajan tasalla.

        Kutsutaan latauksessa ja viennissä, ei säätösilmukassa: kutsu tekee
        ``stat``-kutsun kutakin mikkitiedostoa kohti, ja tiedoston lukeminen
        ei kuulu siihen silmukkaan. Jo tiedettyjä ei ylikirjoiteta — oikea
        ajo tietää enemmän kuin levyn tarkastelu.

        Ei ota ``lock``ia itse: vienti pitää sitä jo, eikä ``threading.Lock``
        ole uudelleensyötettävä. Kutsuja vastaa lukosta.
        """
        if self.timeline is None or self.mix_progress.get("running"):
            return
        roles = resolve_roles(self.timeline, self.settings.tracks)
        found = mix.adopt(self.timeline, roles, self.settings.audio)
        for key, path in found.replacements.items():
            if key not in self.mix_result.replacements:
                self.mix_result.replacements[key] = path
                self.mix_result.skipped += 1
        have = {k for k, _ in self.mix_result.room}
        for key, path in found.room:
            if key not in have:
                self.mix_result.room.append((key, path))
                self.mix_result.skipped += 1

    def run_mix(self) -> None:
        """Käsittelee äänet taustalla. Kestää minuutteja, ei kuulu silmukkaan."""
        assert self.timeline is not None
        roles = resolve_roles(self.timeline, self.settings.tracks)
        self.mix_progress.update(
            {
                "done": 0,
                "total": 0,
                "current": "",
                "stage": "",
                "fraction": 0.0,
                "eta": 0,
                "running": True,
            }
        )

        # Vaimennus tarvitsee saman puheentunnistuksen kuin kuvan leikkaus.
        # Ruudukko rakennetaan tässä eikä säätösilmukassa, koska käsittely on
        # muutenkin hidas — ja jos se ei onnistu, vaimennus jää pois eikä
        # koko käsittely kaadu.
        grid, program_start = None, 0.0
        if self.settings.audio.duck and self.analysis is not None:
            try:
                grid, start, _ = build_grid(self.analysis, self.settings.tracks, roles)
                program_start = float(start)
            except AnalysisError as exc:
                self.mix_progress["running"] = False
                self.mix_result = mix.MixResult(
                    errors={"duck": t("audio.duck_failed", error=exc)}
                )
                return

        def report(info: dict) -> None:
            self.mix_progress.update(info | {"eta": round(info.get("eta", 0.0))})

        try:
            result = mix.process(
                self.timeline,
                roles,
                self.settings.audio,
                grid=grid,
                program_start=program_start,
                progress=report,
            )
            with self.lock:
                self.mix_result = result
        except Exception as exc:  # taustasäie ei saa kaatua hiljaa
            self.mix_result = mix.MixResult(errors={"mix": str(exc)})
            traceback.print_exc()
        finally:
            self.mix_progress["running"] = False
            self.mix_progress["stage"] = ""

    def compute(self) -> dict:
        """Ajaa päätöskerroksen ja kokoaa vastauksen käyttöliittymälle.

        Puutteelliset roolit palautuvat ``ok: False`` ja luettavana listana,
        eivät HTTP-virheenä: ne ovat normaali välitila silmukassa.

        Avain ``_grid`` on sisäinen: vienti tarvitsee saman päätöksen, eikä sitä
        lasketa kahdesti. Se poistetaan ennen JSONiksi kirjoittamista.
        """
        if self.timeline is None or self.analysis is None:
            raise HTTPException(409, self.load_error or t("export.not_loaded"))
        started = time.perf_counter()
        roles = resolve_roles(self.timeline, self.settings.tracks)
        problems = list(roles.problems)
        for track in self.timeline.tracks:
            cfg = self.settings.tracks.get(track.key)
            if not cfg or cfg.role != ROLE_MIC:
                continue
            problems += [
                self.analysis.errors[k]
                for k in track.media_keys
                if k in self.analysis.errors
            ]
        if problems:
            return {"ok": False, "problems": problems, "ms": 0.0}
        try:
            grid, program_start, program_end = build_grid(
                self.analysis, self.settings.tracks, roles
            )
        except AnalysisError as exc:
            return {"ok": False, "problems": [str(exc)], "ms": 0.0}

        decision = decide(grid, self.settings.globals)
        counts: dict[str, int] = {}
        for seg in decision.segments:
            counts[seg.label] = counts.get(seg.label, 0) + 1
        elapsed = (time.perf_counter() - started) * 1000.0
        return {
            "ok": True,
            "problems": [],
            "program": {
                "start": float(program_start),
                "end": float(program_end),
                "duration": float(program_end - program_start),
            },
            "segments": [
                {
                    "start": s.start,
                    "end": s.end,
                    "duration": s.duration,
                    "label": s.label,
                    "angle": s.angle,
                }
                for s in decision.segments
            ],
            "counts": counts,
            # Laajan tunnus on aineistoa, ei käyttöliittymän tekstiä: sama
            # merkkijono päätyy vientiin rooliksi. Käyttöliittymä kääntää sen
            # näytölle, ja tarvitsee siihen tiedon siitä mikä tunnus se on.
            "wide_label": WIDE_LABEL,
            "preview": build_preview(grid, decision),
            "ms": round(elapsed, 1),
            "_grid": (grid, program_start, program_end, decision),
        }


def _audio_warnings(state: AppState, roles, replacements: dict) -> list[str]:
    """Kertoo jos vienti käyttää raakaa ääntä vaikka käsittely on päällä.

    Vienti viittaa vain valmiisiin tiedostoihin, joten kesken käsittelyn
    vietäessä tulos on ehjä mutta käsittelemätön. Se on juuri sellainen ero
    jota ei huomaa Final Cutissa ennen kuin kuuntelee — ja silloin leikkaus on
    jo tehty, eikä uusi vienti tuo tehtyjä muokkauksia mukanaan.
    """
    if state.timeline is None or not state.settings.audio.enabled:
        return []
    expected = {
        item.key
        for keys in roles.mics.values()
        for key in keys
        for item in state.timeline.track_media(key)
        if item.path
    }
    missing = expected - set(replacements)
    if not missing:
        return []
    if state.mix_progress.get("running"):
        return [t("export.audio_running", missing=len(missing), total=len(expected))]
    return [t("export.audio_missing", missing=len(missing), total=len(expected))]


def _track_json(state: AppState, track) -> dict:
    """Yksi raita käyttöliittymälle: rooli, säätimet ja osat.

    Monikamerassa raita on sama kulma useassa osassa, joten mitat ja
    varoitukset kootaan osista. Käyttöliittymä näyttää yhden rivin, ei kuutta.
    """
    assert state.timeline is not None
    items = state.timeline.track_media(track.key)
    span = state.timeline.track_span(track.key) or (0, 0)
    first = items[0] if items else None
    errors = (
        [state.analysis.errors.get(m.key, "") for m in items] if state.analysis else []
    )
    facts = probe.info(first.path) if first and first.path else {}
    return {
        "key": track.key,
        "name": track.name,
        "kind": "video" if track.has_video else "audio",
        "probe": facts,
        # Osien yhteiskesto ja -koko: raita on yksi asia, vaikka tiedostoja
        # olisi monta.
        "total_size": sum((probe.info(m.path).get("size") or 0) for m in items),
        "total_duration": sum((probe.info(m.path).get("duration") or 0) for m in items),
        "path": first.path if first else "",
        "missing": any(m.path and not os.path.exists(m.path) for m in items),
        "has_video": track.has_video,
        "has_audio": track.has_audio,
        "width": first.width if first else 0,
        "height": first.height if first else 0,
        "fps": (
            round(float(1 / first.frame_duration), 3)
            if first and first.frame_duration
            else None
        ),
        "audio_channels": first.audio_channels if first else 0,
        "timeline_start": float(span[0]),
        "timeline_end": float(span[1]),
        "parts": [
            {
                "name": m.name,
                "path": m.path,
                "missing": bool(m.path) and not os.path.exists(m.path),
            }
            for m in items
        ],
        "angle_name": first.angle_name if first else "",
        "thumb": bool(track.has_video and first and first.path),
        "config": state.settings.config_for(track.key).to_json(),
        "envelope_error": next((e for e in errors if e), ""),
    }


def _state_json(state: AppState) -> dict:
    """Koko tila käyttöliittymälle: raidat, roolit, säätimet ja edistyminen."""
    timeline = state.timeline
    tracks = (
        [_track_json(state, t) for t in timeline.tracks] if timeline is not None else []
    )
    return {
        "xml_path": state.xml_path,
        "settings_path": project.settings_path(state.xml_path),
        "output_path": project.next_output_path(
            state.xml_path, project.name_tag(state.settings)
        ),
        "name": timeline.name if timeline else "",
        "kind": timeline.kind if timeline else "",
        "parts": len(timeline.multicams) if timeline else 0,
        "fps": (round(float(1 / timeline.frame_duration), 3) if timeline else None),
        "tracks": tracks,
        "globals": state.settings.globals.to_json(),
        "progress": state.progress,
        "inherited_from": state.inherited_from,
        "language": state.language,
        "languages": list(LANGUAGES),
        "audio": state.settings.audio.to_json(),
        "mix": {
            "progress": state.mix_progress,
            "ready": len(state.mix_result.replacements),
            "room": len(state.mix_result.room),
            "skipped": state.mix_result.skipped,
            "gains": state.mix_result.gains,
            "errors": list(state.mix_result.errors.values()),
        },
        "error": state.load_error,
    }


def create_app(state: AppState) -> FastAPI:
    """Rakentaa sovelluksen annetun tilan ympärille.

    Tila annetaan ulkoa, jotta testit voivat ajaa saman rajapinnan ilman
    palvelinprosessia.
    """
    app = FastAPI(title="autoraffkat", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def use_language(request, call_next):
        """Kieli pyynnön kontekstiin ennen kuin mitään viestejä syntyy."""
        i18n.set_language(state.language)
        return await call_next(request)

    @app.post("/api/language")
    def set_language(payload: dict):
        """Käyttöliittymän kieli. Tallentuu asetuksiin ja periytyy jaksosta
        toiseen, kuten muutkin asetukset."""
        state.language = i18n.normalise(payload.get("language"))
        i18n.set_language(state.language)
        with state.lock:
            state.settings.language = state.language
            try:
                project.save(state.xml_path, state.settings)
            except OSError:
                pass  # kieli ei ole tallentamisen arvoinen virhe
        return {"language": state.language, "languages": list(LANGUAGES)}

    @app.get("/")
    def index():
        """Käyttöliittymän sivu, tyyli ja skripti muokkausajalla versioituna.

        Ilman versiota selain tarjoilee vanhaa tyyliä uuden skriptin kanssa, ja
        tulos on rikkinäinen tavalla jota kukaan ei osaa yhdistää välimuistiin.
        """
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        for name in (
            "app.js",
            "i18n.js",
            "style.css",
            "favicon.ico",
            "favicon.png",
            "favicon.svg",
            "apple-touch-icon.png",
            "icon.png",
        ):
            static_file = STATIC_DIR / name
            if static_file.exists():
                stamp = int(static_file.stat().st_mtime)
                html = html.replace(f"/static/{name}", f"/static/{name}?v={stamp}")
        return Response(html, media_type="text/html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        """Selainten vakiokuvake juuripolusta."""
        fav = STATIC_DIR / "favicon.ico"
        if fav.is_file():
            return FileResponse(fav, media_type="image/x-icon")
        return Response(status_code=404)

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    @app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
    def apple_touch_icon():
        """Kuvake iOS/macOS-selainpikakuvakkeille."""
        icon = STATIC_DIR / "apple-touch-icon.png"
        if icon.is_file():
            return FileResponse(icon, media_type="image/png")
        return Response(status_code=404)

    @app.get("/api/thumb")
    def thumb(track: str):
        """Ruutu raidan kuvasta. Puretaan vasta pyydettäessä.

        Kulmien nimet ovat ``1``, ``2`` ja ``3``, joten roolituksen tekeminen
        ilman kuvaa on arvailua. Purku on ffmpegiä, joten se ei kuulu
        latausvaiheeseen — selain pyytää nämä omaan tahtiinsa.
        """
        if state.timeline is None:
            raise HTTPException(404, t("export.not_loaded"))
        for item in state.timeline.track_media(track):
            path = thumbs.for_item(item)
            if path:
                # Välimuistin avain sisältää muokkausajan, joten sisältö ei
                # muutu saman URLin alla.
                return FileResponse(
                    path,
                    media_type="image/jpeg",
                    headers={"Cache-Control": "max-age=86400"},
                )
        return Response(status_code=404)

    @app.get("/api/defaults")
    def defaults():
        """Tehdasasetukset. Säätimiä on paljon, ja perintä vie huonon arvon
        seuraavaan jaksoon — ilman paluuta siitä ei pääse takaisin."""
        return {"globals": Globals().to_json(), "audio": AudioSettings().to_json()}

    @app.get("/api/plugins")
    def list_plugins():
        """Asennetut VST3- ja AU-liitännäiset. Haetaan vasta pyydettäessä."""
        return {"plugins": chain.plugins()}

    @app.get("/api/plugin-params")
    def plugin_parameters(path: str = ""):
        """Yhden liitännäisen säätimet.

        Erillinen pyyntö liitännäisluettelosta, koska tämä lataa
        liitännäisen: se kestää sekunteja, eikä sitä saa tehdä 800:lle.
        """
        try:
            specs, total = chain.parameter_specs(path)
        except ChainError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"params": specs, "total": total}

    @app.get("/api/state")
    def get_state():
        """Koko tila. Käyttöliittymä kysyy tämän avatessa ja edistymistä pollatessa."""
        return _state_json(state)

    @app.post("/api/reload")
    def reload_xml():
        """Lukee lähde-XML:n uudestaan levyltä, esimerkiksi uuden viennin jälkeen."""
        state.load()
        return _state_json(state)

    @app.post("/api/open")
    def open_xml(payload: dict):
        """Avaa toisen XML-tiedoston tai paketin."""
        path = str((payload or {}).get("path") or "").strip()
        if not path or not os.path.exists(path):
            raise HTTPException(
                400, t("read.file_missing", path=path or "(polku puuttuu)")
            )
        # Lukko on load():n sisällä, eikä threading.Lock ole rekursiivinen:
        # sen ottaminen tässä jumitti avauksen ikuisesti. Pyyntö ei palannut,
        # ja koska load() nollaa edistymisen ennen lukkoa, käyttöliittymä jäi
        # lukemaan «verhokäyrät 0/0» loputtomiin.
        state.xml_path = os.path.abspath(path)
        state.load()
        return _state_json(state)

    @app.post("/api/pick")
    def pick_xml():
        """Finderin valintaikkuna palvelimen puolelta.

        Selaimessa ei ole tiedostovalitsinta joka antaisi polun: <input type=
        "file"> antaa sisällön muttei sijaintia, ja koko työkalu toimii
        poluilla. Natiivi-ikkunassa tämän hoitaa pywebview, selaimessa ei
        mikään — siksi ikkuna avataan täällä. Palvelin on aina samalla
        koneella kuin selain, joten ikkuna aukeaa oikealle näytölle.
        """
        if sys.platform != "darwin":
            return {"path": "", "unavailable": True}
        start = os.path.dirname(state.xml_path) if state.xml_path else ""
        return {"path": pick.native(start, force=True) or ""}

    @app.post("/api/settings")
    def post_settings(payload: dict):
        """Ottaa säätimet vastaan, ajaa päätöksen ja tallentaa asetukset.

        Tämä on silmukan kuuma polku: kutsutaan jokaisesta liukusäätimen
        liikkeestä, joten tässä ei saa tehdä muuta kuin päätöskerros ja pieni
        JSON-kirjoitus.
        """
        with state.lock:
            state.apply(payload)
            result = state.compute()
            result.pop("_grid", None)
            # Nimi seuraa säätimiä, joten ruudulla näkyvä polku muuttuu niiden
            # mukana. Muuten se lupaisi tiedostoa jota vienti ei kirjoita.
            result["output_path"] = project.next_output_path(
                state.xml_path, project.name_tag(state.settings)
            )
            try:
                project.save(state.xml_path, state.settings)
            except OSError as exc:
                result.setdefault("problems", []).append(
                    t("export.settings_failed", error=exc)
                )
        return result

    @app.post("/api/export")
    def export(payload: dict | None = None):
        """Kirjoittaa leikatun FCPXML:n uutena tiedostona lähteen viereen.

        Ottaa säätimet vastaan samassa pyynnössä, jotta vienti käyttää varmasti
        sitä mitä ruudulla näkyy eikä edellistä tallennettua tilaa.
        """
        with state.lock:
            if payload:
                state.apply(payload)
            result = state.compute()
            if not result.get("ok"):
                return JSONResponse(
                    {"ok": False, "problems": result.get("problems", [])},
                    status_code=400,
                )
            _grid, program_start, program_end, decision = result["_grid"]
            assert state.timeline is not None
            roles = resolve_roles(state.timeline, state.settings.tracks)
            mic_tracks: list[tuple[str, str]] = []
            for name in roles.speakers:
                for key in roles.mics.get(name, []):
                    mic_tracks.append((key, name))
            out_path = project.next_output_path(
                state.xml_path, project.name_tag(state.settings)
            )
            if os.path.abspath(out_path) == os.path.abspath(state.xml_path):
                raise HTTPException(400, t("export.would_overwrite"))
            if os.path.dirname(out_path).endswith(project.BUNDLE_EXT):
                # Paketti kuuluu Final Cutille. Jos polku joskus laskettaisiin
                # sinne, se on virhe eikä asia jota yritetään silti.
                raise HTTPException(400, t("export.inside_bundle"))
            try:
                # Käsitelty ääni otetaan mukaan jos se on olemassa ja
                # ajan tasalla. Vanhentunutta ei käytetä hiljaa.
                # Levyllä jo oleva käsitelty ääni mukaan: rooli on voinut
                # vaihtua avaamisen jälkeen, eikä painamatta jäänyt nappi saa
                # olla syy siihen että vienti viittaa raakaan ääneen.
                state.adopt_mix()
                result = (
                    state.mix_result
                    if state.settings.audio.enabled
                    else mix.MixResult()
                )
                replacements = {
                    k: v for k, v in result.replacements.items() if os.path.exists(v)
                }
                room = [(k, v) for k, v in result.room if os.path.exists(v)]
                warnings = _audio_warnings(state, roles, replacements)
                if state.timeline.multicams:
                    # Monikamerassa ulos tulee monikameraleikkaus: kuvakulman
                    # voi vaihtaa Final Cutissa jälkikäteen.
                    xml = build_multicam_fcpxml(
                        state.timeline,
                        decision.segments,
                        mic_tracks,
                        program_start,
                        program_end,
                        state.settings.globals.project_name,
                        replacements=replacements,
                        room=room,
                        settings=state.settings,
                        source=state.xml_path,
                    )
                else:
                    xml = build_fcpxml(
                        {m.key: m for m in state.timeline.media},
                        decision.segments,
                        mic_tracks,
                        state.timeline.frame_duration,
                        program_start,
                        program_end,
                        state.settings.globals.project_name,
                        replacements=replacements,
                        room=room,
                        settings=state.settings,
                        source=state.xml_path,
                    )
                write_fcpxml(out_path, xml)
            except (WriteError, OSError) as exc:
                raise HTTPException(400, str(exc)) from exc
            project.save(state.xml_path, state.settings)
        # Seuraavan viennin nimi mukaan: ruudulla näkyvä polku on juuri
        # kirjoitettu, ja ilman tätä se jäisi lupaamaan väärää tiedostoa.
        return {
            "ok": True,
            "path": out_path,
            "cuts": len(decision.segments),
            "mixed": len(replacements),
            "room": len(room),
            "warnings": warnings,
            "next_path": project.next_output_path(
                state.xml_path, project.name_tag(state.settings)
            ),
        }

    @app.post("/api/mix")
    def run_mix(payload: dict | None = None):
        """Käynnistää äänenkäsittelyn taustalle.

        Erillinen painike eikä osa vientiä: käsittely kestää minuutteja, ja
        vienti on silmukan nopea pää. Valmiit tiedokset jäävät levylle, joten
        seuraavat viennit käyttävät niitä ilman uutta ajoa.
        """
        if state.timeline is None:
            raise HTTPException(409, state.load_error or t("export.not_loaded"))
        if state.mix_progress.get("running"):
            return {"ok": True, "running": True}
        with state.lock:
            if payload:
                state.apply(payload)
            state.settings.audio.enabled = True
            project.save(state.xml_path, state.settings)
        threading.Thread(target=state.run_mix, daemon=True).start()
        return {"ok": True, "running": True}

    @app.post("/api/reveal")
    def reveal(payload: dict):
        """Näytä tiedosto Finderissa/tiedostonhallinnassa."""
        path = str(payload.get("path", "")).strip()
        if not path or not os.path.exists(path):
            raise HTTPException(404, t("export.file_missing"))
        if sys.platform == "darwin":
            subprocess.run(["open", "-R", path], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer.exe", f"/select,{path}"], check=False)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", os.path.dirname(path) or "."], check=False)
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
