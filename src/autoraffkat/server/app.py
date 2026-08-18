"""Paikallinen web-käyttöliittymä.

Analyysi jää palvelimen puolelle, koska se on jo Pythonia ja päätöskerros on
numpya. Selain hoitaa vain säätimet ja piirron. Verhokäyrät lasketaan
taustasäikeessä, jotta roolit voi nimetä heti eikä käyttöliittymä jää odottamaan
ffmpegiä.
"""

from __future__ import annotations

import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import project
from ..analysis import Analysis, AnalysisError, analyze, build_grid, resolve_roles
from ..decide import decide
from ..fcpxml.read import ReadError, Timeline, read_fcpxml
from ..fcpxml.write import WriteError, build_fcpxml, write_fcpxml
from ..model import (OVERLAP_RULES, ROLES, ROLE_MIC, Globals, TrackConfig)
from ..preview import build as build_preview

STATIC_DIR = Path(__file__).parent / "static"


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
    progress: dict = field(default_factory=lambda: {"done": 0, "total": 0,
                                                    "current": "", "ready": False})
    load_error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)

    # ---------------------------------------------------------- lataus

    def load(self) -> None:
        """Lukee XML:n ja käynnistää verhokäyrien laskennan taustalle.

        Lukuvirhe ei kaada palvelinta vaan jää ``load_error``iin, jotta
        käyttöliittymä voi näyttää sen ja käyttäjä voi korjata viennin.
        """
        self.load_error = ""
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
        threading.Thread(target=self._analyze, daemon=True).start()

    def _seed_defaults(self) -> None:
        """Ensimmäisellä avauksella arvataan roolit nimien perusteella."""
        assert self.timeline is not None
        if self.settings.tracks:
            for item in self.timeline.media:
                self.settings.config_for(item.key)
            return
        for item in self.timeline.media:
            cfg = self.settings.config_for(item.key)
            lowered = item.name.lower()
            if item.has_audio and not item.has_video:
                cfg.role = ROLE_MIC
            elif item.has_video and any(w in lowered for w in ("wide", "laaja", "master")):
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
        except Exception as exc:                      # taustasäie ei saa kaatua hiljaa
            self.load_error = f"Verhokäyrien laskenta epäonnistui: {exc}"
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
        for name in ("min_shot", "lead", "confirm", "dominance_db",
                     "min_overlap", "wide_every"):
            if name in raw:
                setattr(g, name, max(0.0, float(raw[name])))
        if raw.get("overlap_rule") in OVERLAP_RULES:
            g.overlap_rule = raw["overlap_rule"]
        if "project_name" in raw:
            g.project_name = str(raw["project_name"])[:120] or "Raakaleikkaus"

    def compute(self) -> dict:
        """Ajaa päätöskerroksen ja kokoaa vastauksen käyttöliittymälle.

        Puutteelliset roolit palautuvat ``ok: False`` ja luettavana listana,
        eivät HTTP-virheenä: ne ovat normaali välitila silmukassa.

        Avain ``_grid`` on sisäinen: vienti tarvitsee saman päätöksen, eikä sitä
        lasketa kahdesti. Se poistetaan ennen JSONiksi kirjoittamista.
        """
        if self.timeline is None or self.analysis is None:
            raise HTTPException(409, self.load_error or "XML:ää ei ole luettu.")
        started = time.perf_counter()
        roles = resolve_roles(self.timeline, self.settings.tracks)
        problems = list(roles.problems)
        for key, message in self.analysis.errors.items():
            cfg = self.settings.tracks.get(key)
            if cfg and cfg.role == ROLE_MIC:
                problems.append(message)
        if problems:
            return {"ok": False, "problems": problems, "ms": 0.0}
        try:
            grid, program_start, program_end = build_grid(
                self.analysis, self.settings.tracks, roles)
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
            "program": {"start": float(program_start), "end": float(program_end),
                        "duration": float(program_end - program_start)},
            "segments": [{"start": s.start, "end": s.end, "duration": s.duration,
                          "label": s.label, "angle": s.angle}
                         for s in decision.segments],
            "counts": counts,
            "preview": build_preview(grid, decision),
            "ms": round(elapsed, 1),
            "_grid": (grid, program_start, program_end, decision),
        }


def _state_json(state: AppState) -> dict:
    """Koko tila käyttöliittymälle: mediat, roolit, säätimet ja edistyminen."""
    timeline = state.timeline
    media = []
    if timeline is not None:
        for item in timeline.media:
            entry = item.to_json()
            entry["config"] = state.settings.config_for(item.key).to_json()
            entry["envelope_error"] = (state.analysis.errors.get(item.key, "")
                                       if state.analysis else "")
            media.append(entry)
    return {
        "xml_path": state.xml_path,
        "settings_path": project.settings_path(state.xml_path),
        "output_path": project.default_output_path(state.xml_path),
        "name": timeline.name if timeline else "",
        "kind": timeline.kind if timeline else "",
        "fps": (round(float(1 / timeline.frame_duration), 3) if timeline else None),
        "media": media,
        "globals": state.settings.globals.to_json(),
        "progress": state.progress,
        "error": state.load_error,
    }


def create_app(state: AppState) -> FastAPI:
    """Rakentaa sovelluksen annetun tilan ympärille.

    Tila annetaan ulkoa, jotta testit voivat ajaa saman rajapinnan ilman
    palvelinprosessia.
    """
    app = FastAPI(title="autoraffkat", docs_url=None, redoc_url=None)

    @app.get("/")
    def index():
        """Käyttöliittymän sivu."""
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/state")
    def get_state():
        """Koko tila. Käyttöliittymä kysyy tämän avatessa ja edistymistä pollatessa."""
        return _state_json(state)

    @app.post("/api/reload")
    def reload_xml():
        """Lukee lähde-XML:n uudestaan levyltä, esimerkiksi uuden viennin jälkeen."""
        state.load()
        return _state_json(state)

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
            try:
                project.save(state.xml_path, state.settings)
            except OSError as exc:
                result.setdefault("problems", []).append(
                    f"Asetuksia ei voitu tallentaa: {exc}")
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
                return JSONResponse({"ok": False,
                                     "problems": result.get("problems", [])},
                                    status_code=400)
            grid, program_start, program_end, decision = result["_grid"]
            assert state.timeline is not None
            roles = resolve_roles(state.timeline, state.settings.tracks)
            mic_tracks: list[tuple[str, str]] = []
            for name in roles.speakers:
                for key in roles.mics.get(name, []):
                    mic_tracks.append((key, name))
            out_path = project.default_output_path(state.xml_path)
            if os.path.abspath(out_path) == os.path.abspath(state.xml_path):
                raise HTTPException(400, "Vienti osuisi lähde-XML:n päälle.")
            try:
                xml = build_fcpxml(
                    {m.key: m for m in state.timeline.media},
                    decision.segments, mic_tracks,
                    state.timeline.frame_duration, program_start, program_end,
                    state.settings.globals.project_name,
                )
                write_fcpxml(out_path, xml)
            except (WriteError, OSError) as exc:
                raise HTTPException(400, str(exc)) from exc
            project.save(state.xml_path, state.settings)
        return {"ok": True, "path": out_path, "cuts": len(decision.segments)}

    @app.post("/api/reveal")
    def reveal(payload: dict):
        """Näytä tiedosto Finderissa — pikku mukavuus vientipainikkeen viereen."""
        path = str(payload.get("path", ""))
        if not path or not os.path.exists(path):
            raise HTTPException(404, "Tiedostoa ei ole.")
        os.system(f"open -R {path!r}")
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
