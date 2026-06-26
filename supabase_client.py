"""
supabase_client.py
Supabase-Anbindung fuer Soraya.

Diese Datei kapselt alle direkten Datenbank-Zugriffe.
Der SERVICE_ROLE_KEY darf nur im Backend/Railway liegen, niemals im Frontend.

Erforderliche ENV-Variablen:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Optional

from supabase import Client, create_client

_SUPABASE: Optional[Client] = None


def _ok(data: Any = None) -> dict:
    return {"ok": True, "data": data}


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _response_data(resp) -> Any:
    return getattr(resp, "data", None)


def get_supabase() -> Client:
    global _SUPABASE
    if _SUPABASE is None:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url:
            raise RuntimeError("SUPABASE_URL ist nicht gesetzt.")
        if not key:
            raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY ist nicht gesetzt.")
        _SUPABASE = create_client(url, key)
    return _SUPABASE


def db_health() -> dict:
    try:
        resp = get_supabase().table("profiles").select("id").limit(1).execute()
        return _ok({
            "service": "supabase",
            "connected": True,
            "profiles_checked": len(_response_data(resp) or []),
        })
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def _birth_date_from_person(person: dict) -> str:
    return date(int(person["year"]), int(person["month"]), int(person["day"])).isoformat()


def _birth_time_from_person(person: dict) -> Optional[str]:
    if person.get("hour") is None:
        return None
    hour = int(person.get("hour") or 0)
    minute = int(person.get("minute") or 0)
    return f"{hour:02d}:{minute:02d}:00"


def _target_date_from_iso(value: Optional[str]) -> str:
    if not value:
        return datetime.now(timezone.utc).date().isoformat()
    return str(value)[:10]


def _parse_birth_date(value: str) -> tuple[int, int, int]:
    d = date.fromisoformat(str(value)[:10])
    return d.year, d.month, d.day


def _parse_birth_time(value: Optional[str], time_known: bool) -> tuple[Optional[int], Optional[int]]:
    if not time_known or not value:
        return None, None
    parts = str(value).split(":")
    return int(parts[0]), int(parts[1])


def person_row_to_engine_person(row: dict) -> dict:
    """Supabase public.people row -> Eingabeformat fuer chart_engine/analysis."""
    year, month, day = _parse_birth_date(row["birth_date"])
    hour, minute = _parse_birth_time(row.get("birth_time"), bool(row.get("time_known", True)))
    return {
        "name": row.get("name") or "Unbenannt",
        "year": year,
        "month": month,
        "day": day,
        "hour": hour,
        "minute": minute,
        "birthplace": row.get("birthplace"),
        "lat": row.get("lat"),
        "lng": row.get("lng"),
        "tz_str": row.get("tz_str"),
    }


# ---------------------------------------------------------------------------
# people
# ---------------------------------------------------------------------------
def create_person(owner_id: str, person: dict, *, chart_json: Optional[dict] = None,
                  is_self: bool = False, relation: Optional[str] = None) -> dict:
    try:
        payload = {
            "owner_id": owner_id,
            "name": person.get("name") or "Unbenannt",
            "is_self": is_self,
            "relation": relation,
            "birth_date": _birth_date_from_person(person),
            "birth_time": _birth_time_from_person(person),
            "time_known": bool(person.get("hour") is not None),
            "birthplace": person.get("birthplace") or person.get("resolved_place"),
            "lat": person.get("lat"),
            "lng": person.get("lng"),
            "tz_str": person.get("tz_str"),
            "chart_json": chart_json,
        }
        resp = get_supabase().table("people").insert(payload).execute()
        rows = _response_data(resp) or []
        return _ok(rows[0] if rows else None)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def get_people(owner_id: str) -> dict:
    try:
        resp = (get_supabase()
                .table("people")
                .select("*")
                .eq("owner_id", owner_id)
                .order("created_at", desc=False)
                .execute())
        return _ok(_response_data(resp) or [])
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def get_person(owner_id: str, person_id: str) -> dict:
    """Laedt genau eine Person aus Supabase."""
    try:
        resp = (get_supabase()
                .table("people")
                .select("*")
                .eq("owner_id", owner_id)
                .eq("id", person_id)
                .limit(1)
                .execute())
        rows = _response_data(resp) or []
        if not rows:
            return _err("Person nicht gefunden. Bitte owner_id und person_id pruefen.")
        return _ok(rows[0])
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# analyses / horoscopes / synastries
# ---------------------------------------------------------------------------
def save_analysis(owner_id: str, person_id: str, analysis_result: dict) -> dict:
    try:
        data = analysis_result.get("data", analysis_result)
        payload = {
            "owner_id": owner_id,
            "person_id": person_id,
            "reading": data.get("reading") or "",
            "sections": data.get("sections"),
            "model": data.get("model"),
        }
        resp = get_supabase().table("analyses").insert(payload).execute()
        rows = _response_data(resp) or []
        return _ok(rows[0] if rows else None)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def save_horoscope(owner_id: str, person_id: str, horoscope_result: dict,
                   *, target_date: Optional[str] = None) -> dict:
    try:
        data = horoscope_result.get("data", horoscope_result)
        payload = {
            "owner_id": owner_id,
            "person_id": person_id,
            "period": data.get("period", "daily"),
            "target_date": target_date or _target_date_from_iso(data.get("at_utc")),
            "stimmung": data.get("stimmung"),
            "body": data.get("text"),
            "tipp": data.get("tipp"),
            "transits_used": data.get("transits_used"),
            "model": data.get("model"),
        }
        resp = (get_supabase()
                .table("horoscopes")
                .upsert(payload, on_conflict="person_id,period,target_date")
                .execute())
        rows = _response_data(resp) or []
        return _ok(rows[0] if rows else None)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def save_synastry(owner_id: str, person_a_id: str, person_b_id: str,
                  synastry_result: dict, *, reading: Optional[str] = None) -> dict:
    try:
        data = synastry_result.get("data", synastry_result)
        score = data.get("score") or {}
        payload = {
            "owner_id": owner_id,
            "person_a_id": person_a_id,
            "person_b_id": person_b_id,
            "score_value": score.get("value"),
            "score": score,
            "aspects": data.get("aspects"),
            "summary": data.get("summary"),
            "reading": reading,
        }
        resp = (get_supabase()
                .table("synastries")
                .upsert(payload, on_conflict="person_a_id,person_b_id")
                .execute())
        rows = _response_data(resp) or []
        return _ok(rows[0] if rows else None)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# conversations / messages / memory
# ---------------------------------------------------------------------------
def create_conversation(owner_id: str, title: Optional[str] = None) -> dict:
    try:
        payload = {"owner_id": owner_id, "title": title or "Neue Soraya-Unterhaltung"}
        resp = get_supabase().table("conversations").insert(payload).execute()
        rows = _response_data(resp) or []
        return _ok(rows[0] if rows else None)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def save_message(owner_id: str, conversation_id: str, role: str, content: str,
                 tools_used: Optional[list] = None) -> dict:
    try:
        payload = {
            "owner_id": owner_id,
            "conversation_id": conversation_id,
            "role": role,
            "content": content,
            "tools_used": tools_used,
        }
        resp = get_supabase().table("messages").insert(payload).execute()
        rows = _response_data(resp) or []
        return _ok(rows[0] if rows else None)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def get_conversation_messages(owner_id: str, conversation_id: str, limit: int = 50) -> dict:
    try:
        resp = (get_supabase()
                .table("messages")
                .select("role,content,tools_used,created_at")
                .eq("owner_id", owner_id)
                .eq("conversation_id", conversation_id)
                .order("created_at", desc=False)
                .limit(limit)
                .execute())
        return _ok(_response_data(resp) or [])
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def update_profile_memory(owner_id: str, memory: str) -> dict:
    try:
        resp = (get_supabase()
                .table("profiles")
                .update({"memory": memory})
                .eq("id", owner_id)
                .execute())
        rows = _response_data(resp) or []
        return _ok(rows[0] if rows else None)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")
