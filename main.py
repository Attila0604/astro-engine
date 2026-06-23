"""
main.py
Duenner FastAPI-Wrapper, der die Chart-Engine als HTTP-Service bereitstellt.
Deploy auf Railway -> die Agenten koennen die Endpoints spaeter als Tools callen.

Endpoints:
    GET  /          -> Health-Check
    GET  /demo      -> Beispiel-Ausgabe (im Browser aufrufbar, ohne POST)
    POST /chart     -> Geburtshoroskop
    POST /transits  -> Transite gegen das Chart
    POST /synastry  -> Beziehungs-Dynamik zweier Charts
"""

from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

import chart_engine as ce

app = FastAPI(title="RGYM Astro Engine", version="1.0")


class PersonIn(BaseModel):
    name: str
    year: int
    month: int
    day: int
    hour: Optional[int] = None      # None = Geburtszeit unbekannt
    minute: Optional[int] = None
    lat: float
    lng: float
    tz_str: Optional[str] = None    # optional; sonst aus lat/lng abgeleitet


class TransitIn(BaseModel):
    person: PersonIn
    at: Optional[str] = None        # ISO-Datum/Zeit; None = jetzt


class SynastryIn(BaseModel):
    person_a: PersonIn
    person_b: PersonIn


@app.get("/")
def health():
    return {
        "ok": True,
        "service": "astro-engine",
        "endpoints": ["/chart", "/transits", "/synastry", "/demo"],
    }


@app.post("/chart")
def chart(p: PersonIn):
    return ce.compute_natal(p.model_dump())


@app.post("/transits")
def transits(t: TransitIn):
    return ce.compute_transits(t.person.model_dump(), t.at)


@app.post("/synastry")
def synastry(s: SynastryIn):
    return ce.compute_synastry(s.person_a.model_dump(), s.person_b.model_dump())


@app.get("/demo")
def demo():
    """Im Browser aufrufbar -- liefert sofort ein Beispiel ohne POST-Body."""
    pa = {"name": "Gyoergy", "year": 1985, "month": 7, "day": 12,
          "hour": 8, "minute": 30, "lat": 46.6713, "lng": 11.1597}
    pb = {"name": "Sarah", "year": 1990, "month": 3, "day": 22,
          "hour": 19, "minute": 15, "lat": 47.4894, "lng": 12.0658}
    return {
        "natal": ce.compute_natal(pa),
        "transits_today": ce.compute_transits(pa),
        "synastry": ce.compute_synastry(pa, pb),
    }
