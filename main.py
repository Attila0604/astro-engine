"""
main.py
Duenner FastAPI-Wrapper, der die Soraya Chart-Engine als HTTP-Service bereitstellt.

Eingabe-Komfort: Statt lat/lng kann man einfach `birthplace` (Ortsname) angeben
-- der wird per Geocoding aufgeloest. lat/lng gehen weiterhin direkt.

Endpoints:
    GET  /          -> Health-Check
    GET  /db/health -> Supabase-Verbindungstest
    GET  /demo      -> Beispiel-Ausgabe (im Browser aufrufbar, ohne POST)
    POST /chart     -> Geburtshoroskop
    POST /transits  -> Transite gegen das Chart
    POST /synastry  -> Beziehungs-Dynamik zweier Charts
    POST /analysis  -> Komplette Tiefen-Analyse (Multi-Agent, Claude)
"""

from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

import chart_engine as ce
from analysis import generate_full_analysis
from horoscope import generate_horoscope
from chat import chat_turn, update_memory
from geocode import geocode_place
from supabase_client import db_health

app = FastAPI(title="Soraya Astro Engine", version="1.5")


class PersonIn(BaseModel):
    name: str
    year: int
    month: int
    day: int
    hour: Optional[int] = None
    minute: Optional[int] = None
    birthplace: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    tz_str: Optional[str] = None


class TransitIn(BaseModel):
    person: PersonIn
    at: Optional[str] = None


class SynastryIn(BaseModel):
    person_a: PersonIn
    person_b: PersonIn


class HoroscopeIn(BaseModel):
    person: PersonIn
    period: str = "daily"
    at: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatIn(BaseModel):
    person: PersonIn
    people: List[PersonIn] = []
    messages: List[ChatMessage] = []
    message: str
    memory: Optional[str] = None


class MemoryIn(BaseModel):
    messages: List[ChatMessage] = []
    memory: Optional[str] = None


def _resolve_person(p: PersonIn) -> dict:
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
    if result.get("ok") and person.get("resolved_place"):
        result["data"]["meta"]["resolved_place"] = person["resolved_place"]
    return result


@app.get("/")
def health():
    return {
        "ok": True,
        "service": "soraya-astro-engine",
        "endpoints": ["/chart", "/transits", "/synastry", "/analysis",
                      "/horoscope", "/chat", "/memory/update", "/db/health", "/demo"],
    }


@app.get("/db/health")
def database_health():
    return db_health()


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
    r = _resolve_person(p)
    if not r["ok"]:
        return r
    return _tag_place(await generate_full_analysis(r["data"]), r["data"])


@app.post("/horoscope")
async def horoscope(h: HoroscopeIn):
    r = _resolve_person(h.person)
    if not r["ok"]:
        return r
    return await generate_horoscope(r["data"], h.period, h.at)


@app.post("/chat")
async def chat(c: ChatIn):
    ru = _resolve_person(c.person)
    if not ru["ok"]:
        return ru
    people = []
    for pp in c.people:
        rp = _resolve_person(pp)
        if not rp["ok"]:
            return {"ok": False, "error": f"{pp.name}: {rp['error']}"}
        people.append(rp["data"])
    history = [m.model_dump() for m in c.messages]
    return await chat_turn(ru["data"], people, history, c.message, c.memory)


@app.post("/memory/update")
async def memory_update(m: MemoryIn):
    return await update_memory([x.model_dump() for x in m.messages], m.memory)


@app.get("/demo")
def demo():
    pa = {"name": "Gyoergy", "year": 1985, "month": 7, "day": 12,
          "hour": 8, "minute": 30, "lat": 46.6713, "lng": 11.1597}
    pb = {"name": "Sarah", "year": 1990, "month": 3, "day": 22,
          "hour": 19, "minute": 15, "lat": 47.4894, "lng": 12.0658}
    return {
        "natal": ce.compute_natal(pa),
        "transits_today": ce.compute_transits(pa),
        "synastry": ce.compute_synastry(pa, pb),
    }
