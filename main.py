import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import chart_engine as ce
from analysis import generate_full_analysis
from horoscope import generate_horoscope
from synastry_reading import generate_synastry_reading, score_label, score_percent
from chat import chat_turn, update_memory
from geocode import geocode_place
from sky import sky_today
import public_content
from auth_guard import require_soraya_api_key
from auth_user import get_current_supabase_user, require_user_or_api_key
import limits
from supabase_client import (
    db_health,
    create_person,
    get_people,
    get_person,
    person_row_to_engine_person,
    get_latest_analysis,
    save_analysis,
    get_cached_horoscope,
    save_horoscope,
    save_synastry,
    get_synastry,
    update_person_chart,
    create_conversation,
    save_message,
    get_conversation_messages,
    get_profile_memory,
    update_profile_memory,
    delete_account,
)

app = FastAPI(title="Soraya Astro Engine", version="2.9")


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
# Supabase Keepalive
# ---------------------------------------------------------------------------
# Supabase pausiert Free-Projekte nach ca. 7 Tagen ohne Datenbankaktivitaet.
# Diese Hintergrundaufgabe ruft regelmaessig db_health() auf (echte Abfrage
# auf die profiles-Tabelle) und haelt die Datenbank so aktiv.
#
# Steuerung ueber die Umgebungsvariable KEEPALIVE_STUNDEN:
#   - nicht gesetzt / ungueltig -> 12 Stunden
#   - 0 (oder kleiner)          -> Keepalive abgeschaltet
KEEPALIVE_STANDARD_STUNDEN = 12.0
KEEPALIVE_ERSTE_WARTEZEIT_SEKUNDEN = 60

_keepalive_task: Optional[asyncio.Task] = None


def _keepalive_log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[keepalive] {ts} {msg}", flush=True)


def _keepalive_stunden() -> float:
    raw = os.getenv("KEEPALIVE_STUNDEN")
    if raw is None or raw.strip() == "":
        return KEEPALIVE_STANDARD_STUNDEN
    try:
        return float(raw.strip().replace(",", "."))
    except ValueError:
        _keepalive_log(
            f"Ungueltiger Wert KEEPALIVE_STUNDEN={raw!r}, "
            f"verwende Standard {KEEPALIVE_STANDARD_STUNDEN:g} Stunden."
        )
        return KEEPALIVE_STANDARD_STUNDEN


async def _supabase_keepalive_loop(intervall_sekunden: float) -> None:
    # Erster Lauf verzoegert, damit der App-Start nicht aufgehalten wird.
    await asyncio.sleep(KEEPALIVE_ERSTE_WARTEZEIT_SEKUNDEN)

    while True:
        try:
            # db_health() ist synchron -> im Thread ausfuehren, damit der
            # Event-Loop (und damit alle Requests) nicht blockiert wird.
            result = await asyncio.to_thread(db_health)
            if result.get("ok"):
                _keepalive_log(f"OK - Supabase erreichbar: {result.get('data')}")
            else:
                _keepalive_log(f"FEHLER - Supabase-Abfrage fehlgeschlagen: {result.get('error')}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _keepalive_log(f"FEHLER - Unerwartete Exception: {type(e).__name__}: {e}")

        try:
            await asyncio.sleep(intervall_sekunden)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # Sollte nie passieren; trotzdem nicht in eine Endlosschleife ohne Pause laufen.
            _keepalive_log(f"FEHLER - sleep fehlgeschlagen: {type(e).__name__}: {e}")
            await asyncio.sleep(KEEPALIVE_STANDARD_STUNDEN * 3600)


@app.on_event("startup")
async def start_supabase_keepalive():
    global _keepalive_task
    try:
        stunden = _keepalive_stunden()
        if stunden <= 0:
            _keepalive_log("Abgeschaltet (KEEPALIVE_STUNDEN=0).")
            return

        _keepalive_log(
            f"Gestartet - erster Lauf in {KEEPALIVE_ERSTE_WARTEZEIT_SEKUNDEN} s, "
            f"danach alle {stunden:g} Stunden."
        )
        _keepalive_task = asyncio.create_task(_supabase_keepalive_loop(stunden * 3600))
    except Exception as e:
        # Ein Fehler beim Keepalive darf den App-Start niemals verhindern.
        _keepalive_log(f"FEHLER - Keepalive konnte nicht gestartet werden: {type(e).__name__}: {e}")


@app.on_event("shutdown")
async def stop_supabase_keepalive():
    try:
        if _keepalive_task is not None and not _keepalive_task.done():
            _keepalive_task.cancel()
    except Exception:
        pass


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
    only_cached: bool = False


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
    # Nur eine gespeicherte Analyse liefern, nie eine neue erzeugen
    # (fuer automatisches Oeffnen in der App).
    only_cached: bool = False


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


class MobilePersonRefIn(BaseModel):
    person_id: str


class MobileTransitsIn(BaseModel):
    person_id: str
    at: Optional[str] = None


class PublicPreviewIn(BaseModel):
    year: int
    month: int
    day: int
    hour: Optional[int] = None
    minute: Optional[int] = None


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
# Kostenbremse: Groessenlimits fuer Eingaben, die an Claude gehen
# ---------------------------------------------------------------------------
MAX_CHAT_ZEICHEN = 2000
MAX_MEMORY_ZEICHEN = 4000
MAX_PEOPLE_IDS = 10


def _check_chat_message(message: str) -> Optional[dict]:
    text = (message or "").strip()
    if not text:
        return {"ok": False, "error": "Bitte schreib eine Nachricht."}
    if len(text) > MAX_CHAT_ZEICHEN:
        return {
            "ok": False,
            "error": f"Deine Nachricht ist zu lang (maximal {MAX_CHAT_ZEICHEN} Zeichen).",
        }
    return None


def _cap_memory(memory: Optional[str]) -> Optional[str]:
    if memory is None:
        return None
    return str(memory)[:MAX_MEMORY_ZEICHEN]


def _cap_people_ids(ids: List[str]) -> List[str]:
    seen = []
    for pid in ids or []:
        if pid and pid not in seen:
            seen.append(pid)
    return seen[:MAX_PEOPLE_IDS]


def _horoscope_cache_since(period: str, today: date) -> date:
    """Ab welchem Datum ein gespeichertes Horoskop noch gilt."""
    if period == "weekly":
        return today - timedelta(days=today.weekday())
    if period == "monthly":
        return today.replace(day=1)
    return today


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
        "version": "2.9",
        "security": "mobile endpoints and /chart, /transits, /synastry use Authorization Bearer Supabase token; daily limits per user",
        "endpoints": [
            "/auth/me",
            "/mobile/people/create",
            "/mobile/people/list",
            "/mobile/analysis/save",
            "/mobile/horoscope/save",
            "/mobile/synastry/save",
            "/mobile/chat/save",
            "/mobile/chat/history",
            "/mobile/memory/save",
            "/mobile/chart",
            "/mobile/transits",
            "/sky",
            "/public/preview",
            "/public/sign-horoscope",
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


@app.post("/mobile/account/delete")
async def mobile_account_delete(user: dict = Depends(get_current_supabase_user)):
    """
    Loescht das Konto des eingeloggten Users unwiderruflich.

    Sicherheit:
    - owner_id kommt AUSSCHLIESSLICH aus dem verifizierten Supabase-Token,
      niemals aus dem Request-Body. Ein User kann damit nur sich selbst loeschen.
    - Loescht alle User-Daten und den Auth-Account (Store-Pflicht:
      Apple App Store + Google Play verlangen In-App-Kontoloeschung).
    """
    return delete_account(user["id"])


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
            only_cached=payload.only_cached,
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
async def mobile_synastry_save(
    payload: MobileSaveSynastryIn,
    user: dict = Depends(get_current_supabase_user),
):
    return await synastry_save(
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


@app.get("/mobile/chat/history")
def mobile_chat_history(
    conversation_id: str,
    limit: int = 30,
    user: dict = Depends(get_current_supabase_user),
):
    """
    Letzte Nachrichten einer EIGENEN Unterhaltung (owner_id aus dem Token),
    damit die App den Chat nach einem Neustart wieder anzeigen kann.
    """
    rows = get_conversation_messages(user["id"], conversation_id, limit=max(1, min(int(limit or 30), 50)))
    if not rows["ok"]:
        return rows
    messages = [
        {"role": r.get("role"), "content": r.get("content") or "", "created_at": r.get("created_at")}
        for r in rows["data"]
        if r.get("role") in ("user", "assistant") and r.get("content")
    ]
    return {"ok": True, "data": {"conversation_id": conversation_id, "messages": messages}}


@app.post("/mobile/memory/save")
async def mobile_memory_save(
    m: MobileMemorySaveIn,
    user: dict = Depends(get_current_supabase_user),
):
    limits.reserve(user["id"], "gedaechtnis")
    gen = await update_memory(
        [x.model_dump() for x in m.messages[-40:]], _cap_memory(m.memory)
    )
    if not gen["ok"]:
        limits.release(user["id"], "gedaechtnis")
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


# Alte Charts in der Datenbank wurden mit ASCII-Umlauten gespeichert.
_OLD_SIGN_NAMES = {"Loewe": "Löwe", "Schuetze": "Schütze", "veraenderlich": "veränderlich"}


def _fix_old_labels(value):
    if isinstance(value, dict):
        return {k: _fix_old_labels(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_fix_old_labels(v) for v in value]
    if isinstance(value, str):
        return _OLD_SIGN_NAMES.get(value, value)
    return value


def _chart_is_complete(chart) -> bool:
    return (
        isinstance(chart, dict)
        and isinstance(chart.get("points"), list) and chart["points"]
        and isinstance(chart.get("houses"), list)
        and isinstance(chart.get("big_three"), dict)
    )


def _engine_person_from_row(row: dict) -> dict:
    """DB-Person -> Engine-Person. Fehlen Koordinaten, wird einmal geocodet."""
    person = person_row_to_engine_person(row)
    if person.get("lat") is None or person.get("lng") is None:
        geo = geocode_place(person.get("birthplace") or "")
        if geo.get("ok"):
            person["lat"] = geo["data"]["lat"]
            person["lng"] = geo["data"]["lng"]
    return person


@app.post("/mobile/chart")
def mobile_chart(
    payload: MobilePersonRefIn,
    user: dict = Depends(get_current_supabase_user),
):
    """
    Radix einer gespeicherten Person. Nutzt das beim Anlegen gespeicherte
    chart_json (schnell, kein Geocoding); fehlt es, wird es einmal berechnet
    und gespeichert.
    """
    row = get_person(user["id"], payload.person_id)
    if not row["ok"]:
        return row

    stored = row["data"].get("chart_json")
    if _chart_is_complete(stored):
        return {"ok": True, "source": "stored", "data": _fix_old_labels(stored)}

    limits.reserve(user["id"], "berechnung")
    natal = ce.compute_natal(_engine_person_from_row(row["data"]))
    if not natal["ok"]:
        return natal
    update_person_chart(user["id"], payload.person_id, natal["data"])
    return {"ok": True, "source": "computed", "data": natal["data"]}


@app.post("/mobile/transits")
def mobile_transits(
    payload: MobileTransitsIn,
    user: dict = Depends(get_current_supabase_user),
):
    """Aktuelle Transite einer gespeicherten Person (Koordinaten aus der DB)."""
    row = get_person(user["id"], payload.person_id)
    if not row["ok"]:
        return row

    limits.reserve(user["id"], "berechnung")
    return ce.compute_transits(_engine_person_from_row(row["data"]), payload.at)


# ---------------------------------------------------------------------------
# Schnellstart OHNE Login (Inhalte vor der Registrierung)
# ---------------------------------------------------------------------------
def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unbekannt"


@app.post("/public/preview")
def public_preview(payload: PublicPreviewIn, request: Request):
    """Sonne + Mond aus dem Geburtsdatum. Es wird nichts gespeichert."""
    limits.reserve("ip:" + _client_ip(request), "oeffentlich")
    return public_content.preview(payload.year, payload.month, payload.day, payload.hour, payload.minute)


@app.get("/public/sign-horoscope")
async def public_sign_horoscope(sign: str, request: Request):
    """Allgemeines Tageshoroskop fuer ein Sonnenzeichen (1x pro Tag und Zeichen erzeugt)."""
    limits.reserve("ip:" + _client_ip(request), "oeffentlich")
    return await public_content.sign_horoscope(sign)


@app.get("/sky")
def sky():
    """Himmel heute (fuer alle gleich, stuendlich gecacht, kein Login noetig)."""
    return sky_today()


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

    if payload.only_cached and not payload.force_new:
        return {"ok": True, "data": {"source": "none", "analysis": None}}

    person_row = get_person(payload.owner_id, payload.person_id)
    if not person_row["ok"]:
        return person_row

    # Kostenbremse: nur echte Neu-Erzeugungen zaehlen, nicht der Cache oben.
    limits.reserve(payload.owner_id, "analyse")
    person = person_row_to_engine_person(person_row["data"])
    reading = await generate_full_analysis(person)
    if not reading["ok"]:
        limits.release(payload.owner_id, "analyse")
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
    period = (payload.period or "daily").lower()

    # Kostenbremse: dasselbe Horoskop (heute / diese Woche / dieser Monat)
    # wird aus der Datenbank geliefert statt neu bei Claude bestellt.
    today = datetime.now(timezone.utc).date()
    if not payload.at or str(payload.at)[:10] == today.isoformat():
        since = _horoscope_cache_since(period, today).isoformat()
        cached = get_cached_horoscope(payload.owner_id, payload.person_id, period, since)
        if cached["ok"] and cached["data"]:
            row = cached["data"]
            details = row.get("details") or {}
            return {
                "ok": True,
                "data": {
                    "source": "cached",
                    "horoscope": row,
                    "person": {
                        "id": payload.person_id,
                        "name": person.get("name"),
                    },
                    "period": row.get("period"),
                    "stimmung": row.get("stimmung"),
                    "text": row.get("body"),
                    "tipp": row.get("tipp"),
                    "fokus": details.get("fokus"),
                    "liebe": details.get("liebe"),
                    "beruf": details.get("beruf"),
                    "ritual": details.get("ritual"),
                    "affirmation": details.get("affirmation"),
                    "model": row.get("model"),
                    "transits_used": row.get("transits_used"),
                },
            }

    limits.reserve(payload.owner_id, "horoskop")
    horoscope_result = await generate_horoscope(person, period, payload.at)
    if not horoscope_result["ok"]:
        limits.release(payload.owner_id, "horoskop")
        return horoscope_result

    saved = save_horoscope(payload.owner_id, payload.person_id, horoscope_result)
    if not saved["ok"]:
        return saved

    return {
        "ok": True,
        "data": {
            "source": "created",
            "horoscope": saved["data"],
            "person": {
                "id": payload.person_id,
                "name": person.get("name"),
            },
            "period": horoscope_result["data"].get("period"),
            "stimmung": horoscope_result["data"].get("stimmung"),
            "text": horoscope_result["data"].get("text"),
            "tipp": horoscope_result["data"].get("tipp"),
            "fokus": horoscope_result["data"].get("fokus"),
            "liebe": horoscope_result["data"].get("liebe"),
            "beruf": horoscope_result["data"].get("beruf"),
            "ritual": horoscope_result["data"].get("ritual"),
            "affirmation": horoscope_result["data"].get("affirmation"),
            "model": horoscope_result["data"].get("model"),
            "transits_used": horoscope_result["data"].get("transits_used"),
        },
    }


@app.post("/synastry/save")
async def synastry_save(
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
    fingerprint = _synastry_fingerprint(row_a["data"], row_b["data"])

    # Kostenbremse: Gleiches Paar mit unveraenderten Geburtsdaten -> gespeicherte
    # Deutung wiederverwenden statt Claude erneut zu bezahlen.
    existing = get_synastry(payload.owner_id, payload.person_a_id, payload.person_b_id)
    if existing["ok"] and existing["data"]:
        cached_reading = _parse_synastry_reading(existing["data"].get("reading"))
        if cached_reading and cached_reading.get("fp") == fingerprint and cached_reading.get("text"):
            row = existing["data"]
            return _synastry_response(
                payload, person_a, person_b, row,
                {"score": row.get("score"), "summary": row.get("summary"), "aspects": row.get("aspects")},
                cached_reading, source="cached",
            )

    limits.reserve(payload.owner_id, "synastrie")
    syn = ce.compute_synastry(person_a, person_b)
    if not syn["ok"]:
        limits.release(payload.owner_id, "synastrie")
        return syn

    # Claude-Deutung (best-effort: faellt sie aus, kommen trotzdem die Aspekte)
    reading = await generate_synastry_reading(
        person_a.get("name"), person_b.get("name"), syn["data"]
    )
    reading_data = reading["data"] if reading.get("ok") else {}
    if not reading.get("ok"):
        limits.release(payload.owner_id, "synastrie")

    stored_reading = None
    if reading_data.get("text"):
        stored_reading = json.dumps(
            {"fp": fingerprint, **{k: reading_data.get(k) for k in SYNASTRY_READING_FIELDS}},
            ensure_ascii=False,
        )

    saved = save_synastry(
        payload.owner_id,
        payload.person_a_id,
        payload.person_b_id,
        syn,
        reading=stored_reading,
    )
    if not saved["ok"]:
        return saved

    return _synastry_response(
        payload, person_a, person_b, saved["data"], syn["data"], reading_data, source="created",
    )


SYNASTRY_READING_FIELDS = ("text", "harmonie", "spannung", "anziehung", "kommunikation")


def _synastry_fingerprint(row_a: dict, row_b: dict) -> str:
    """Aendern sich Geburtsdaten einer Person, wird die Deutung neu erstellt."""
    parts = []
    for row in (row_a, row_b):
        parts.append("|".join(str(row.get(k) or "") for k in (
            "birth_date", "birth_time", "time_known", "lat", "lng", "name")))
    return "#".join(parts)


def _parse_synastry_reading(raw) -> Optional[dict]:
    if not raw:
        return None
    try:
        obj = json.loads(raw) if isinstance(raw, str) else raw
        return obj if isinstance(obj, dict) else None
    except (TypeError, ValueError):
        return None


def _synastry_response(payload, person_a, person_b, row, syn_data, reading_data, *, source):
    score = syn_data.get("score") or {}
    value = score.get("value") if isinstance(score, dict) else score
    description = score.get("description") if isinstance(score, dict) else None
    return {
        "ok": True,
        "data": {
            "source": source,
            "synastry": row,
            "person_a": {"id": payload.person_a_id, "name": person_a.get("name")},
            "person_b": {"id": payload.person_b_id, "name": person_b.get("name")},
            "score": score,
            "score_percent": score_percent(value),
            "score_label": score_label(description),
            "summary": syn_data.get("summary"),
            "aspects": syn_data.get("aspects"),
            **{k: (reading_data or {}).get(k) for k in SYNASTRY_READING_FIELDS},
        },
    }


@app.post("/chat/save")
async def chat_save(
    payload: ChatSaveIn,
    _: bool = Depends(require_soraya_api_key),
):
    invalid = _check_chat_message(payload.message)
    if invalid:
        return invalid

    person_row = get_person(payload.owner_id, payload.person_id)
    if not person_row["ok"]:
        return person_row

    user_person = person_row_to_engine_person(person_row["data"])

    people = []
    for pid in _cap_people_ids(payload.people_ids):
        if pid == payload.person_id:
            continue
        other_row = get_person(payload.owner_id, pid)
        if not other_row["ok"]:
            return {
                "ok": False,
                "error": f"Person {pid} konnte nicht geladen werden: {other_row.get('error')}",
            }
        people.append(person_row_to_engine_person(other_row["data"]))

    # Kostenbremse: vor dem Anlegen einer Unterhaltung pruefen.
    limits.reserve(payload.owner_id, "chat")

    conversation_id = payload.conversation_id
    if not conversation_id:
        title = payload.message.strip()[:80] or "Neue Soraya-Unterhaltung"
        conv = create_conversation(payload.owner_id, title=title)
        if not conv["ok"]:
            limits.release(payload.owner_id, "chat")
            return conv
        conversation_id = conv["data"]["id"]

    previous = get_conversation_messages(
        payload.owner_id,
        conversation_id,
        limit=50,
    )
    if not previous["ok"]:
        limits.release(payload.owner_id, "chat")
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
        _cap_memory(memory),
    )
    if not reply_result["ok"]:
        limits.release(payload.owner_id, "chat")
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
def _count_engine_call(owner_id: str) -> None:
    """Chart-Berechnungen: nur eingeloggte User werden gezaehlt (API-Key nicht)."""
    if owner_id:
        limits.reserve(owner_id, "berechnung")


@app.post("/chart")
def chart(p: PersonIn, owner_id: str = Depends(require_user_or_api_key)):
    _count_engine_call(owner_id)
    r = _resolve_person(p)
    if not r["ok"]:
        return r

    return _tag_place(ce.compute_natal(r["data"]), r["data"])


@app.post("/transits")
def transits(t: TransitIn, owner_id: str = Depends(require_user_or_api_key)):
    _count_engine_call(owner_id)
    r = _resolve_person(t.person)
    if not r["ok"]:
        return r

    return ce.compute_transits(r["data"], t.at)


@app.post("/synastry")
def synastry(s: SynastryIn, owner_id: str = Depends(require_user_or_api_key)):
    _count_engine_call(owner_id)
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
    limits.reserve(m.owner_id, "gedaechtnis")
    gen = await update_memory(
        [x.model_dump() for x in m.messages[-40:]], _cap_memory(m.memory)
    )
    if not gen["ok"]:
        limits.release(m.owner_id, "gedaechtnis")
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
