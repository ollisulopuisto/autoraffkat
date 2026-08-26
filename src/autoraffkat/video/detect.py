"""Tunnistimet: se osa jonka odotetaan vaihtuvan.

Tunnistin katsoo yhtä ruutua ja palauttaa siitä joukon lukuja. Se ei tiedä
mitään aikajanasta, puhujista eikä pisteytyksestä — ja juuri siksi sen voi
vaihtaa toiseen ilman että mikään muu liikkuu.

Sopimus on kolme asiaa:

* ``name`` ja ``version`` menevät välimuistiavaimeen. Vaihdettu tunnistin tai
  muutettu laskenta mitätöi vanhat mittaukset itsestään — muuten uusi
  tunnistin lukisi edellisen jälkiä ja tulos olisi hiljaa väärä.
* ``fields`` kertoo mitä sarakkeita syntyy. Pisteytys kysyy niitä nimellä,
  joten tunnistin joka tuottaa eri sarakkeet ei riko lukijaa vaan näkyy
  puuttuvana kenttänä.
* ``measure`` palauttaa ``dict`` tai ``None``. ``None`` tarkoittaa «ei
  kasvoja», ei virhettä: se on tavallinen tulos eikä sitä saa laskea vikana.
"""

from __future__ import annotations

import ctypes
from typing import Protocol

import numpy as np


class DetectError(Exception):
    """Tunnistinta ei saatu käyttöön."""


class Detector(Protocol):
    """Yhden ruudun mittaus."""

    name: str
    version: int
    fields: tuple[str, ...]

    def measure(self, path: str) -> dict | None:
        ...


# ------------------------------------------------------------- macOS Vision

VISION_FIELDS = ("yaw", "roll", "size", "x", "y", "w", "h", "eyes", "smile",
                 "cx", "cy")


class VisionFaces:
    """macOS:n oma kasvontunnistus.

    Ei uutta riippuvuutta: pyobjc tulee pywebviewin mukana, ja kehys ladataan
    järjestelmästä. Ei mallilatausta, ei torchia. Mitattuna noin 5 ms/ruutu.

    Luokat ladataan ``objc.loadBundle``illa, koska erillistä
    ``pyobjc-framework-vision``-pakettia ei ole asennettu — kehys itse on
    aina paikalla.
    """

    name = "vision"
    version = 1
    fields = VISION_FIELDS

    def __init__(self) -> None:
        try:
            import objc
            from Foundation import NSURL
        except ImportError as exc:  # pragma: no cover - riippuu alustasta
            raise DetectError("pyobjc puuttuu, ei kasvontunnistusta") from exc
        self._ns_url = NSURL
        self._classes: dict = {}
        try:
            objc.loadBundle(
                "Vision", self._classes,
                bundle_path="/System/Library/Frameworks/Vision.framework")
            self._handler = self._classes["VNImageRequestHandler"]
            self._request = self._classes["VNDetectFaceLandmarksRequest"]
        except Exception as exc:  # pragma: no cover - riippuu alustasta
            raise DetectError(f"Vision.frameworkia ei saatu: {exc}") from exc

    @staticmethod
    def _points(region) -> np.ndarray:
        """Maamerkit (n, 2), origo kasvolaatikon vasen alanurkka.

        pyobjc palauttaa ``normalizedPoints``ista paljaan CGPoint-osoittimen,
        jota se ei osaa indeksoida: pituus tulee toisesta metodista eikä
        allekirjoituksesta. Osoite luetaan siksi suoraan.
        """
        n = region.pointCount()
        if not n:
            return np.zeros((0, 2))
        addr = region.normalizedPoints().pointerAsInteger
        raw = (ctypes.c_double * (n * 2)).from_address(addr)
        return np.frombuffer(bytes(raw), dtype=np.float64).reshape(n, 2).copy()

    def measure(self, path: str) -> dict | None:
        handler = self._handler.alloc().initWithURL_options_(
            self._ns_url.fileURLWithPath_(path), {})
        request = self._request.alloc().init()
        handler.performRequests_error_([request], None)
        faces = request.results() or []
        if not faces:
            return None
        # Suurin kasvo: lähikuvassa muut ovat taustaa.
        face = max(faces, key=lambda f: f.boundingBox().size.height)
        marks = face.landmarks()
        if marks is None:
            return None
        box = face.boundingBox()

        def aperture(region) -> float:
            pts = self._points(region) if region is not None else np.zeros((0, 2))
            if len(pts) < 3:
                return 0.0
            width = float(np.ptp(pts[:, 0])) or 1e-6
            return float(np.ptp(pts[:, 1]) / width)

        eyes = float(np.mean([aperture(marks.leftEye()), aperture(marks.rightEye())]))

        smile = 0.0
        lips = marks.outerLips()
        if lips is not None:
            pts = self._points(lips)
            if len(pts) >= 6:
                # Suupielet ovat x-suunnan ääripäät; hymyssä ne nousevat
                # huulten keskiviivan yli. Jaettuna suun leveydellä, jottei
                # kasvojen koko sekoitu mittaan.
                order = np.argsort(pts[:, 0])
                corners = float(pts[order[[0, -1]], 1].mean())
                middle = float(pts[:, 1].mean())
                width = float(np.ptp(pts[:, 0])) or 1e-6
                smile = (corners - middle) / width

        every = marks.allPoints()
        pts = self._points(every) if every is not None else np.zeros((0, 2))
        centre = pts.mean(axis=0) if len(pts) else np.zeros(2)
        yaw, roll = face.yaw(), face.roll()
        return {
            "yaw": float(yaw) if yaw is not None else 0.0,
            "roll": float(roll) if roll is not None else 0.0,
            "size": float(box.size.height),
            "x": float(box.origin.x), "y": float(box.origin.y),
            "w": float(box.size.width), "h": float(box.size.height),
            "eyes": eyes, "smile": smile,
            "cx": float(centre[0]), "cy": float(centre[1]),
        }


# ------------------------------------------------------------------ rekisteri

DETECTORS: dict[str, type] = {"vision": VisionFaces}


def load(name: str) -> Detector:
    """Tunnistin nimeltä. Tuntematon nimi on virhe eikä hiljainen ohitus."""
    kind = DETECTORS.get(name)
    if kind is None:
        known = ", ".join(sorted(DETECTORS)) or "-"
        raise DetectError(f"tuntematon tunnistin {name!r} (tunnetut: {known})")
    return kind()
