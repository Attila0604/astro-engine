"""
main.py
Duenner FastAPI-Wrapper, der die Chart-Engine als HTTP-Service bereitstellt.

Eingabe-Komfort: Statt lat/lng kann man einfach `birthplace` (Ortsname) angeben
-- der wird per Geocoding aufgeloest. lat/lng gehen weiterhin direkt.

Endpoints:
    GET  /          -> Health-Check
    GET  /demo      -> Beispiel-Ausgabe (im Browser aufrufbar, ohne POST)
    POST /chart     -> Geburtshoroskop
    POST /transits  -> Transite gegen das Chart
    POST /synastry  -> Beziehungs-Dynamik zweier Charts
    POST /analysis  -> Komplette Tiefen-Analyse (Multi-Agent, Claude)
"""

from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

import chart_engine as ce
from analysis import generate_full_analysis
from geocode import geocode_place

app = FastAPI(title="RGYM Astro Engine", version="1.2")


class PersonIn(BaseModel):
    name: str
    year: int
    month: int
    day: int
    hour: Optional[int] = None        # None = Geburtszeit unbekannt
    minute: Optional[int] = None
    # Geburtsort: ENTWEDER birthplace (Ortsname) ODER lat/lng angeben.
    birthplace: Optional[str] = None  # z.B. "Woergl, Oesterreich"
    lat: Optional[float] = None
    lng: Optional[float] = None
    tz_str: Optional[str] = None      # optional; sonst aus lat/lng abgeleitet


class TransitIn(BaseModel):
    person: PersonIn
    at: Optional[str] = None          # ISO-Datum/Zeit; None = jetzt


class SynastryIn(BaseModel):
    person_a: PersonIn
    person_b: PersonIn


def _resolve_person(p: PersonIn) -> dict:
    """Fuellt lat/lng auf -- per Geocoding, falls nur birthplace angegeben ist.
    Gibt Result {ok, data:person_dict} | {ok:false, error} zurueck."""
    d = p.model_dump()
    if d.get("lat") is None or d.get("lng") is None:
        place = d.get("birthplace")
        if not place:
            return {"ok": False,
                    "error": "Bitte birthplace (Geburtsort) ODER lat/lng angeben."}
        geo = geocode_place(place)
        if not geo["ok"]:
            return geo
        d["lat"] = geo["data"]["lat"]
        d["lng"] = geo["data"]["lng"]
        d["resolved_place"] = geo["data"]["display_name"]
    return {"ok": True, "data": d}


def _tag_place(result: dict, person: dict) -> dict:
    """Haengt den aufgeloesten Ortsnamen zur Kontrolle an die Antwort-Meta."""
    if result.get("ok") and person.get("resolved_place"):
        result["data"]["meta"]["resolved_place"] = person["resolved_place"]
    return result


@app.get("/")
def health():
    return {
        "ok": True,
        "service": "astro-engine",
        "endpoints": ["/chart", "/transits", "/synastry", "/analysis", "/demo"],
    }


@app.post("/chart")
def chart(p: PersonIn):
    r = _resolve_person(p)
    if not r["ok"]:
        return r
    return _tag_place(ce.compute_natal(r["data"]), r["data"])


@app.post("/transits")
def transits(t: TransitIn):
    r = _resolve_person(t.person)
    if not r["ok"]:
        return r
    return ce.compute_transits(r["data"], t.at)


@app.post("/synastry")
def synastry(s: SynastryIn):
    ra = _resolve_person(s.person_a)
    if not ra["ok"]:
        return ra
    rb = _resolve_person(s.person_b)
    if not rb["ok"]:
        return rb
    return ce.compute_synastry(ra["data"], rb["data"])


@app.post("/analysis")
async def analysis(p: PersonIn):
    """Komplette Tiefen-Analyse (Multi-Agent, Claude). Braucht ANTHROPIC_API_KEY."""
    r = _resolve_person(p)
    if not r["ok"]:
        return r
    return _tag_place(await generate_full_analysis(r["data"]), r["data"])


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
