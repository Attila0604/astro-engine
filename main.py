
from typing import List, Optional

from fastapi import Depends, FastAPI
from pydantic import BaseModel

import chart_engine as ce
from analysis import generate_full_analysis
from horoscope import generate_horoscope
from chat import chat_turn, update_memory
from geocode import geocode_place
from auth_guard import require_soraya_api_key
from supabase_client import (
    db_health,
    create_person,
    get_person,
    person_row_to_engine_person,
    save_analysis,
    save_horoscope,
    create_conversation,
    save_message,
    get_conversation_messages,
)

app = FastAPI(title="Soraya Astro Engine", version="2.0")


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


class CreatePersonIn(BaseModel):
    owner_id: str
    person: PersonIn
    is_self: bool = False
    relation: Optional[str] = None


class SaveAnalysisIn(BaseModel):
    owner_id: str
    person_id: str


class SaveHoroscopeIn(BaseModel):
    owner_id: str
    person_id: str
    period: str = "daily"
    at: Optional[str] = None


class ChatSaveIn(BaseModel):
    owner_id: str
    person_id: str
    message: str
    conversation_id: Optional[str] = None
    memory: Optional[str] = None
    people_ids: List[str] = []


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
            return {"ok": False, "error": "Bitte birthplace (Geburtsort) ODER lat/lng angeben."}
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


def _rows_to_chat_history(rows: list) -> list:
    return [
        {"role": r.get("role"), "content": r.get("content") or ""}
        for r in rows
        if r.get("role") in ("user", "assistant") and r.get("content")
    ]


@app.get("/")
def health():
    return {
        "ok": True,
        "service": "soraya-astro-engine",
        "version": "2.0",
        "security": "X-Soraya-API-Key required for write/AI endpoints",
        "endpoints": [
            "/chart", "/people/create", "/analysis/save", "/horoscope/save",
            "/chat/save", "/transits", "/synastry", "/analysis", "/horoscope",
            "/chat", "/memory/update", "/db/health", "/demo",
        ],
    }


@app.get("/db/health")
def database_health():
    return db_health()


@app.post("/people/create")
def people_create(payload: CreatePersonIn, _: bool = Depends(require_soraya_api_key)):
    r = _resolve_person(payload.person)
    if not r["ok"]:
        return r

    natal = ce.compute_natal(r["data"])
    if not natal["ok"]:
        return natal

    saved = create_person(
        payload.owner_id,
        r["data"],
        chart_json=natal["data"],
        is_self=payload.is_self,
        relation=payload.relation,
    )
    if not saved["ok"]:
        return saved

    return {
        "ok": True,
        "data": {
            "person": saved["data"],
            "chart_meta": natal["data"]["meta"],
            "big_three": natal["data"]["big_three"],
        },
    }


@app.post("/analysis/save")
async def analysis_save(payload: SaveAnalysisIn, _: bool = Depends(require_soraya_api_key)):
    person_row = get_person(payload.owner_id, payload.person_id)
    if not person_row["ok"]:
        return person_row

    person = person_row_to_engine_person(person_row["data"])

    reading = await generate_full_analysis(person)
    if not reading["ok"]:
        return reading

    saved = save_analysis(payload.owner_id, payload.person_id, reading)
    if not saved["ok"]:
        return saved

    return {
        "ok": True,
        "data": {
            "analysis": saved["data"],
            "person": {"id": payload.person_id, "name": person.get("name")},
            "big_three": reading["data"].get("big_three"),
            "model": reading["data"].get("model"),
            "reading": reading["data"].get("reading"),
        },
    }


@app.post("/horoscope/save")
async def horoscope_save(payload: SaveHoroscopeIn, _: bool = Depends(require_soraya_api_key)):
    person_row = get_person(payload.owner_id, payload.person_id)
    if not person_row["ok"]:
        return person_row

    person = person_row_to_engine_person(person_row["data"])

    horoscope_result = await generate_horoscope(person, payload.period, payload.at)
    if not horoscope_result["ok"]:
        return horoscope_result

    saved = save_horoscope(payload.owner_id, payload.person_id, horoscope_result)
    if not saved["ok"]:
        return saved

    return {
        "ok": True,
        "data": {
            "horoscope": saved["data"],
            "person": {"id": payload.person_id, "name": person.get("name")},
            "period": horoscope_result["data"].get("period"),
            "stimmung": horoscope_result["data"].get("stimmung"),
            "text": horoscope_result["data"].get("text"),
            "tipp": horoscope_result["data"].get("tipp"),
            "model": horoscope_result["data"].get("model"),
            "transits_used": horoscope_result["data"].get("transits_used"),
        },
    }


@app.post("/chat/save")
async def chat_save(payload: ChatSaveIn, _: bool = Depends(require_soraya_api_key)):
    person_row = get_person(payload.owner_id, payload.person_id)
    if not person_row["ok"]:
        return person_row

    user_person = person_row_to_engine_person(person_row["data"])

    people = []
    for pid in payload.people_ids:
        other_row = get_person(payload.owner_id, pid)
        if not other_row["ok"]:
            return {"ok": False, "error": f"Person {pid} konnte nicht geladen werden: {other_row.get('error')}"}
        people.append(person_row_to_engine_person(other_row["data"]))

    conversation_id = payload.conversation_id
    if not conversation_id:
        title = payload.message.strip()[:80] or "Neue Soraya-Unterhaltung"
        conv = create_conversation(payload.owner_id, title=title)
        if not conv["ok"]:
            return conv
        conversation_id = conv["data"]["id"]

    previous = get_conversation_messages(payload.owner_id, conversation_id, limit=50)
    if not previous["ok"]:
        return previous

    history = _rows_to_chat_history(previous["data"])

    reply_result = await chat_turn(
        user_person,
        people,
        history,
        payload.message,
        payload.memory,
    )
    if not reply_result["ok"]:
        return reply_result

    user_saved = save_message(
        payload.owner_id,
        conversation_id,
        "user",
        payload.message,
    )
    if not user_saved["ok"]:
        return user_saved

    assistant_saved = save_message(
        payload.owner_id,
        conversation_id,
        "assistant",
        reply_result["data"]["reply"],
        tools_used=reply_result["data"].get("tools_used"),
    )
    if not assistant_saved["ok"]:
        return assistant_saved

    return {
        "ok": True,
        "data": {
            "conversation_id": conversation_id,
            "reply": reply_result["data"]["reply"],
            "tools_used": reply_result["data"].get("tools_used"),
            "saved": {
                "user_message": user_saved["data"],
                "assistant_message": assistant_saved["data"],
            },
        },
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
async def analysis(p: PersonIn, _: bool = Depends(require_soraya_api_key)):
    r = _resolve_person(p)
    if not r["ok"]:
        return r
    return _tag_place(await generate_full_analysis(r["data"]), r["data"])


@app.post("/horoscope")
async def horoscope(h: HoroscopeIn, _: bool = Depends(require_soraya_api_key)):
    r = _resolve_person(h.person)
    if not r["ok"]:
        return r
    return await generate_horoscope(r["data"], h.period, h.at)


@app.post("/chat")
async def chat(c: ChatIn, _: bool = Depends(require_soraya_api_key)):
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
async def memory_update(m: MemoryIn, _: bool = Depends(require_soraya_api_key)):
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
