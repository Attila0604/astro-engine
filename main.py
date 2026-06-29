from typing import List, Optional

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import chart_engine as ce
from analysis import generate_full_analysis
from horoscope import generate_horoscope
from chat import chat_turn, update_memory
from geocode import geocode_place
from auth_guard import require_soraya_api_key
from auth_user import get_current_supabase_user
from supabase_client import (
    db_health,
    create_person,
    get_people,
    get_person,
    person_row_to_engine_person,
    get_latest_analysis,
    save_analysis,
    save_horoscope,
    save_synastry,
    create_conversation,
    save_message,
    get_conversation_messages,
    get_profile_memory,
    update_profile_memory,
)

app = FastAPI(title="Soraya Astro Engine", version="2.4")


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Fuer den MVP ist "*" praktisch.
# Spaeter fuer Produktion bitte auf deine echten Domains einschraenken, z. B.:
# allow_origins=["https://deine-soraya-domain.vercel.app"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------
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
    force_new: bool = False


class SaveHoroscopeIn(BaseModel):
    owner_id: str
    person_id: str
    period: str = "daily"
    at: Optional[str] = None


class SaveSynastryIn(BaseModel):
    owner_id: str
    person_a_id: str
    person_b_id: str


class ChatSaveIn(BaseModel):
    owner_id: str
    person_id: str
    message: str
    conversation_id: Optional[str] = None
    memory: Optional[str] = None
    people_ids: List[str] = Field(default_factory=list)


# Mobile-sichere Payloads: KEINE owner_id im Body.
# Das Backend nimmt owner_id automatisch aus dem Supabase Access Token.
class MobileCreatePersonIn(BaseModel):
    person: PersonIn
    is_self: bool = False
    relation: Optional[str] = None


class MobileSaveAnalysisIn(BaseModel):
    person_id: str
    force_new: bool = False


class MobileSaveHoroscopeIn(BaseModel):
    person_id: str
    period: str = "daily"
    at: Optional[str] = None


class MobileSaveSynastryIn(BaseModel):
    person_a_id: str
    person_b_id: str


class MobileChatSaveIn(BaseModel):
    person_id: str
    message: str
    conversation_id: Optional[str] = None
    memory: Optional[str] = None
    people_ids: List[str] = Field(default_factory=list)


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
    people: List[PersonIn] = Field(default_factory=list)
    messages: List[ChatMessage] = Field(default_factory=list)
    message: str
    memory: Optional[str] = None


class MemoryIn(BaseModel):
    messages: List[ChatMessage] = Field(default_factory=list)
    memory: Optional[str] = None


class MemorySaveIn(BaseModel):
    owner_id: str
    messages: List[ChatMessage] = Field(default_factory=list)
    memory: Optional[str] = None


class MobileMemorySaveIn(BaseModel):
    messages: List[ChatMessage] = Field(default_factory=list)
    memory: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_person(p: PersonIn) -> dict:
    """Ergaenzt lat/lng ueber Geocoding, wenn nur birthplace gegeben ist."""
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
    """Schreibt den geokodierten Ort in die Chart-Metadaten."""
    if result.get("ok") and person.get("resolved_place"):
        result["data"]["meta"]["resolved_place"] = person["resolved_place"]
    return result


def _rows_to_chat_history(rows: list) -> list:
    return [
        {"role": r.get("role"), "content": r.get("content") or ""}
        for r in rows
        if r.get("role") in ("user", "assistant") and r.get("content")
    ]


def _safe_person_row(row: dict) -> dict:
    """Gibt nur Frontend-sichere Personendaten zurueck."""
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "is_self": row.get("is_self"),
        "relation": row.get("relation"),
        "birth_date": row.get("birth_date"),
        "birth_time": row.get("birth_time"),
        "time_known": row.get("time_known"),
        "birthplace": row.get("birthplace"),
        "created_at": row.get("created_at"),
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/")
def health():
    return {
        "ok": True,
        "service": "soraya-astro-engine",
        "version": "2.4",
        "security": "mobile endpoints use Authorization Bearer Supabase token; CORS enabled for web app",
        "endpoints": [
            "/auth/me",
            "/mobile/people/create",
            "/mobile/people/list",
            "/mobile/analysis/save",
            "/mobile/horoscope/save",
            "/mobile/synastry/save",
            "/mobile/chat/save",
            "/mobile/memory/save",
            "/chart",
            "/people/create",
            "/analysis/save",
            "/horoscope/save",
            "/synastry/save",
            "/chat/save",
            "/transits",
            "/synastry",
            "/analysis",
            "/horoscope",
            "/chat",
            "/memory/update",
            "/memory/save",
            "/db/health",
            "/demo",
        ],
    }


@app.get("/db/health")
def database_health():
    return db_health()


# ---------------------------------------------------------------------------
# Mobile-sichere Endpoints: Authorization: Bearer <Supabase Access Token>
# ---------------------------------------------------------------------------
@app.get("/auth/me")
async def auth_me(user: dict = Depends(get_current_supabase_user)):
    return {
        "ok": True,
        "data": {
            "id": user.get("id"),
            "email": user.get("email"),
            "aud": user.get("aud"),
            "role": user.get("role"),
        },
    }


@app.post("/mobile/people/create")
def mobile_people_create(
    payload: MobileCreatePersonIn,
    user: dict = Depends(get_current_supabase_user),
):
    return people_create(
        CreatePersonIn(
            owner_id=user["id"],
            person=payload.person,
            is_self=payload.is_self,
            relation=payload.relation,
        ),
        True,
    )


@app.get("/mobile/people/list")
def mobile_people_list(user: dict = Depends(get_current_supabase_user)):
    """
    B.4 Endpoint:
    Laedt alle gespeicherten Personen des eingeloggten Users aus Supabase.

    Wichtig:
    - owner_id kommt NICHT aus dem Browser.
    - owner_id kommt aus dem Supabase Access Token.
    - Es werden nur sichere Felder ans Frontend gesendet.
    """
    rows = get_people(user["id"])
    if not rows["ok"]:
        return rows

    people = [_safe_person_row(r) for r in rows["data"]]
    return {"ok": True, "data": {"people": people}}


@app.post("/mobile/analysis/save")
async def mobile_analysis_save(
    payload: MobileSaveAnalysisIn,
    user: dict = Depends(get_current_supabase_user),
):
    return await analysis_save(
        SaveAnalysisIn(
            owner_id=user["id"],
            person_id=payload.person_id,
            force_new=payload.force_new,
        ),
        True,
    )


@app.post("/mobile/horoscope/save")
async def mobile_horoscope_save(
    payload: MobileSaveHoroscopeIn,
    user: dict = Depends(get_current_supabase_user),
):
    return await horoscope_save(
        SaveHoroscopeIn(
            owner_id=user["id"],
            person_id=payload.person_id,
            period=payload.period,
            at=payload.at,
        ),
        True,
    )


@app.post("/mobile/synastry/save")
def mobile_synastry_save(
    payload: MobileSaveSynastryIn,
    user: dict = Depends(get_current_supabase_user),
):
    return synastry_save(
        SaveSynastryIn(
            owner_id=user["id"],
            person_a_id=payload.person_a_id,
            person_b_id=payload.person_b_id,
        ),
        True,
    )


@app.post("/mobile/chat/save")
async def mobile_chat_save(
    payload: MobileChatSaveIn,
    user: dict = Depends(get_current_supabase_user),
):
    return await chat_save(
        ChatSaveIn(
            owner_id=user["id"],
            person_id=payload.person_id,
            message=payload.message,
            conversation_id=payload.conversation_id,
            memory=payload.memory,
            people_ids=payload.people_ids,
        ),
        True,
    )


@app.post("/mobile/memory/save")
async def mobile_memory_save(
    m: MobileMemorySaveIn,
    user: dict = Depends(get_current_supabase_user),
):
    gen = await update_memory([x.model_dump() for x in m.messages], m.memory)
    if not gen["ok"]:
        return gen

    saved = update_profile_memory(user["id"], gen["data"]["memory"])
    if not saved["ok"]:
        return saved

    return {
        "ok": True,
        "data": {
            "memory": gen["data"]["memory"],
            "profile": saved["data"],
        },
    }


# ---------------------------------------------------------------------------
# Alte/testbare API-Key Endpoints bleiben erhalten
# ---------------------------------------------------------------------------
@app.post("/people/create")
def people_create(
    payload: CreatePersonIn,
    _: bool = Depends(require_soraya_api_key),
):
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
async def analysis_save(
    payload: SaveAnalysisIn,
    _: bool = Depends(require_soraya_api_key),
):
    if not payload.force_new:
        existing = get_latest_analysis(payload.owner_id, payload.person_id)
        if not existing["ok"]:
            return existing

        if existing["data"]:
            return {
                "ok": True,
                "data": {
                    "source": "cached",
                    "analysis": existing["data"],
                    "note": "Vorhandene Analyse wiederverwendet. Fuer neue Analyse force_new=true senden.",
                },
            }

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
            "source": "created",
            "analysis": saved["data"],
            "person": {
                "id": payload.person_id,
                "name": person.get("name"),
            },
            "big_three": reading["data"].get("big_three"),
            "model": reading["data"].get("model"),
            "reading": reading["data"].get("reading"),
        },
    }


@app.post("/horoscope/save")
async def horoscope_save(
    payload: SaveHoroscopeIn,
    _: bool = Depends(require_soraya_api_key),
):
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
            "person": {
                "id": payload.person_id,
                "name": person.get("name"),
            },
            "period": horoscope_result["data"].get("period"),
            "stimmung": horoscope_result["data"].get("stimmung"),
            "text": horoscope_result["data"].get("text"),
            "tipp": horoscope_result["data"].get("tipp"),
            "model": horoscope_result["data"].get("model"),
            "transits_used": horoscope_result["data"].get("transits_used"),
        },
    }


@app.post("/synastry/save")
def synastry_save(
    payload: SaveSynastryIn,
    _: bool = Depends(require_soraya_api_key),
):
    if payload.person_a_id == payload.person_b_id:
        return {
            "ok": False,
            "error": "person_a_id und person_b_id muessen verschieden sein.",
        }

    row_a = get_person(payload.owner_id, payload.person_a_id)
    if not row_a["ok"]:
        return row_a

    row_b = get_person(payload.owner_id, payload.person_b_id)
    if not row_b["ok"]:
        return row_b

    person_a = person_row_to_engine_person(row_a["data"])
    person_b = person_row_to_engine_person(row_b["data"])

    syn = ce.compute_synastry(person_a, person_b)
    if not syn["ok"]:
        return syn

    saved = save_synastry(
        payload.owner_id,
        payload.person_a_id,
        payload.person_b_id,
        syn,
    )
    if not saved["ok"]:
        return saved

    return {
        "ok": True,
        "data": {
            "synastry": saved["data"],
            "person_a": {
                "id": payload.person_a_id,
                "name": person_a.get("name"),
            },
            "person_b": {
                "id": payload.person_b_id,
                "name": person_b.get("name"),
            },
            "score": syn["data"].get("score"),
            "summary": syn["data"].get("summary"),
            "aspects": syn["data"].get("aspects"),
        },
    }


@app.post("/chat/save")
async def chat_save(
    payload: ChatSaveIn,
    _: bool = Depends(require_soraya_api_key),
):
    person_row = get_person(payload.owner_id, payload.person_id)
    if not person_row["ok"]:
        return person_row

    user_person = person_row_to_engine_person(person_row["data"])

    people = []
    for pid in payload.people_ids:
        other_row = get_person(payload.owner_id, pid)
        if not other_row["ok"]:
            return {
                "ok": False,
                "error": f"Person {pid} konnte nicht geladen werden: {other_row.get('error')}",
            }
        people.append(person_row_to_engine_person(other_row["data"]))

    conversation_id = payload.conversation_id
    if not conversation_id:
        title = payload.message.strip()[:80] or "Neue Soraya-Unterhaltung"
        conv = create_conversation(payload.owner_id, title=title)
        if not conv["ok"]:
            return conv
        conversation_id = conv["data"]["id"]

    previous = get_conversation_messages(
        payload.owner_id,
        conversation_id,
        limit=50,
    )
    if not previous["ok"]:
        return previous

    history = _rows_to_chat_history(previous["data"])

    memory = payload.memory
    if memory is None:
        mem_res = get_profile_memory(payload.owner_id)
        if mem_res["ok"]:
            memory = mem_res["data"]

    reply_result = await chat_turn(
        user_person,
        people,
        history,
        payload.message,
        memory,
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


# ---------------------------------------------------------------------------
# Direkte/testbare Engine Endpoints
# ---------------------------------------------------------------------------
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
async def analysis(
    p: PersonIn,
    _: bool = Depends(require_soraya_api_key),
):
    r = _resolve_person(p)
    if not r["ok"]:
        return r

    return _tag_place(await generate_full_analysis(r["data"]), r["data"])


@app.post("/horoscope")
async def horoscope(
    h: HoroscopeIn,
    _: bool = Depends(require_soraya_api_key),
):
    r = _resolve_person(h.person)
    if not r["ok"]:
        return r

    return await generate_horoscope(r["data"], h.period, h.at)


@app.post("/chat")
async def chat(
    c: ChatIn,
    _: bool = Depends(require_soraya_api_key),
):
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
    return await chat_turn(
        ru["data"],
        people,
        history,
        c.message,
        c.memory,
    )


@app.post("/memory/update")
async def memory_update(
    m: MemoryIn,
    _: bool = Depends(require_soraya_api_key),
):
    return await update_memory([x.model_dump() for x in m.messages], m.memory)


@app.post("/memory/save")
async def memory_save(
    m: MemorySaveIn,
    _: bool = Depends(require_soraya_api_key),
):
    gen = await update_memory([x.model_dump() for x in m.messages], m.memory)
    if not gen["ok"]:
        return gen

    saved = update_profile_memory(m.owner_id, gen["data"]["memory"])
    if not saved["ok"]:
        return saved

    return {
        "ok": True,
        "data": {
            "memory": gen["data"]["memory"],
            "profile": saved["data"],
        },
    }
