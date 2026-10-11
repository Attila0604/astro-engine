"""
public_content.py
Inhalte VOR dem Login (Schnellstart in der App).

1. preview(year, month, day, hour=None, minute=None)
   Sonnen- und Mondzeichen aus dem Geburtsdatum. Ohne Geburtsort wird mit
   Wien gerechnet; ohne Geburtszeit mit 12:00. Wechselt Sonne oder Mond an
   diesem Tag das Zeichen, wird das als "unsicher" markiert (beide Zeichen).
   Es wird NICHTS gespeichert.

2. await sign_horoscope(sign)
   Allgemeines Tageshoroskop fuer ein Sonnenzeichen, passend zum echten
   Himmel von heute. Wird pro Zeichen und Tag nur EINMAL bei Claude erzeugt
   und dann aus der Tabelle sign_horoscopes geliefert (max. 12 Aufrufe/Tag).
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timezone
from typing import Optional

import chart_engine as ce
from agents import PERSONA, _call_claude
from sky import sky_today
from supabase_client import get_sign_horoscope, save_sign_horoscope

SIGN_KEYS = ["Ari", "Tau", "Gem", "Can", "Leo", "Vir", "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis"]
SIGN_HOROSCOPE_MODEL = os.environ.get("SIGN_HOROSCOPE_MODEL", os.environ.get("HOROSCOPE_MODEL", "claude-sonnet-4-6"))

# Standardort fuer den Schnellstart (keine Ortseingabe, kein Geocoding)
_DEFAULT_PLACE = {"lat": 48.2082, "lng": 16.3738, "tz_str": "Europe/Vienna"}

_locks: dict[str, asyncio.Lock] = {}


def _point_info(pt: dict) -> dict:
    return {
        "sign": pt.get("sign"),
        "sign_de": pt.get("sign_de"),
        "element_de": pt.get("element_de"),
        "degree": pt.get("degree"),
    }


def _validate_date(year: int, month: int, day: int) -> Optional[str]:
    try:
        d = date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return "Bitte ein gültiges Geburtsdatum angeben."
    if d.year < 1900 or d > date.today():
        return "Bitte ein Geburtsdatum zwischen 1900 und heute angeben."
    return None


def preview(year: int, month: int, day: int, hour: Optional[int] = None, minute: Optional[int] = None) -> dict:
    invalid = _validate_date(year, month, day)
    if invalid:
        return {"ok": False, "error": invalid}
    if hour is not None and not (0 <= int(hour) <= 23):
        return {"ok": False, "error": "Bitte eine Stunde zwischen 0 und 23 angeben."}
    if minute is not None and not (0 <= int(minute) <= 59):
        return {"ok": False, "error": "Bitte eine Minute zwischen 0 und 59 angeben."}

    base = {"name": "Vorschau", "year": int(year), "month": int(month), "day": int(day), **_DEFAULT_PLACE}
    natal = ce.compute_natal({**base, "hour": hour, "minute": minute})
    if not natal["ok"]:
        return natal
    big = natal["data"]["big_three"]
    sun = _point_info(big["sun"])
    moon = _point_info(big["moon"])

    if hour is None:
        # Ohne Geburtszeit: Zeichenwechsel im Laufe des Tages erkennen
        early = ce.compute_natal({**base, "hour": 0, "minute": 1})
        late = ce.compute_natal({**base, "hour": 23, "minute": 59})
        if early["ok"] and late["ok"]:
            for key, info in (("sun", sun), ("moon", moon)):
                a = early["data"]["big_three"][key]["sign_de"]
                b = late["data"]["big_three"][key]["sign_de"]
                if a != b:
                    info["uncertain"] = True
                    info["alternatives"] = [a, b]

    return {
        "ok": True,
        "data": {
            "sun": sun,
            "moon": moon,
            "time_known": hour is not None,
            "note": "Ohne Geburtsort gerechnet. Den Aszendenten berechnet Soraya mit Geburtszeit und -ort in deinem Profil.",
        },
    }


def _today_sky_text() -> str:
    """Planetenstaende von heute als kurzer Text fuer den Prompt."""
    now = datetime.now(timezone.utc)
    sky = ce.compute_transits({"name": "Himmel", "year": now.year, "month": now.month, "day": now.day,
                               "hour": 12, "minute": 0, **_DEFAULT_PLACE})
    lines = []
    if sky.get("ok"):
        for p in sky["data"]["transit_positions"]:
            retro = " (rückläufig)" if p.get("retrograde") else ""
            lines.append(f"- {p.get('name_de')} in {p.get('sign_de')}{retro}")
    s = sky_today()
    if s.get("ok"):
        m = s["data"]["moon"]
        lines.append(f"- Mondphase: {m.get('phase')} ({m.get('illumination')} % beleuchtet)")
    return "\n".join(lines)


def _system() -> str:
    return (
        f"{PERSONA}\n\n"
        "Deine Aufgabe: Schreibe ein ALLGEMEINES Tageshoroskop für ein Sonnenzeichen, "
        "passend zum tatsächlichen Himmel von heute (Planetenstände unten). Es richtet sich an "
        "alle Menschen mit diesem Sonnenzeichen, nicht an eine bestimmte Person – erwähne das "
        "aber nicht ausdrücklich. Beziehe dich konkret auf 1–2 der heutigen Stände, übersetzt in "
        "Alltagssprache. Kurz, warm, ehrlich, ohne Dramatik.\n\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Markdown, in genau diesem Format:\n"
        "{\n"
        '  "stimmung": "<Schlagzeile, max 7 Wörter>",\n'
        '  "text": "<2 kurze Absätze, du-Form>",\n'
        '  "tipp": "<ein konkreter Tipp, 1 Satz>",\n'
        '  "liebe": "<1–2 Sätze>",\n'
        '  "beruf": "<1–2 Sätze>"\n'
        "}"
    )


def _parse(raw: str) -> dict:
    keys = ("stimmung", "text", "tipp", "liebe", "beruf")
    try:
        obj = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        return {k: obj.get(k) for k in keys}
    except Exception:
        out = {k: None for k in keys}
        out["text"] = raw.strip()
        return out


def _row_to_data(row: dict, sign: str, source: str) -> dict:
    return {
        "sign": sign,
        "sign_de": ce.SIGNS_DE.get(sign, sign),
        "date": str(row.get("target_date")),
        "stimmung": row.get("stimmung"),
        "text": row.get("body"),
        "tipp": row.get("tipp"),
        "liebe": row.get("liebe"),
        "beruf": row.get("beruf"),
        "source": source,
    }


async def sign_horoscope(sign: str) -> dict:
    if sign not in SIGN_KEYS:
        return {"ok": False, "error": "Unbekanntes Sternzeichen."}
    today = datetime.now(timezone.utc).date().isoformat()

    cached = get_sign_horoscope(sign, today)
    if cached["ok"] and cached["data"]:
        return {"ok": True, "data": _row_to_data(cached["data"], sign, "cached")}

    lock = _locks.setdefault(sign, asyncio.Lock())
    async with lock:
        # Waehrend wir gewartet haben, koennte es ein anderer Aufruf erzeugt haben
        cached = get_sign_horoscope(sign, today)
        if cached["ok"] and cached["data"]:
            return {"ok": True, "data": _row_to_data(cached["data"], sign, "cached")}

        sign_de = ce.SIGNS_DE.get(sign, sign)
        user = (f"Sonnenzeichen: {sign_de}\n\nDer Himmel heute ({today}):\n{_today_sky_text()}\n\n"
                "Schreibe nun das Tageshoroskop als JSON.")
        try:
            raw = await _call_claude(_system(), user, max_tokens=900, model=SIGN_HOROSCOPE_MODEL)
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}
        parsed = _parse(raw)
        row = {
            "sign": sign, "target_date": today, "stimmung": parsed["stimmung"], "body": parsed["text"],
            "tipp": parsed["tipp"], "liebe": parsed["liebe"], "beruf": parsed["beruf"],
            "model": SIGN_HOROSCOPE_MODEL,
        }
        saved = save_sign_horoscope(row)
        return {"ok": True, "data": _row_to_data(saved["data"] if saved.get("ok") and saved.get("data") else row, sign, "created")}


# ---------------------------------------------------------------------------
# Partner-Einladung: Die eingeladene Person sieht nur Vorname und Sonnen-
# zeichen der einladenden Person und das gemeinsame Ergebnis -- nie deren
# Geburtsdaten. Gerechnet wird ausschliesslich hier im Backend.
# ---------------------------------------------------------------------------
from supabase_client import get_invite_with_person, increment_invite_uses, person_row_to_engine_person  # noqa: E402
from synastry_reading import score_label, score_percent  # noqa: E402

_OLD = {"Loewe": "Löwe", "Schuetze": "Schütze"}


def _first_name(name: Optional[str]) -> str:
    first = (name or "").strip().split(" ")[0] if name else ""
    return (first or "Jemand")[:20]


def _sun_sign_of_row(row: dict) -> Optional[str]:
    chart = row.get("chart_json") or {}
    sun = ((chart.get("big_three") or {}).get("sun") or {}).get("sign_de")
    if sun:
        return _OLD.get(sun, sun)
    natal = ce.compute_natal(person_row_to_engine_person(row))
    if natal.get("ok"):
        return natal["data"]["big_three"]["sun"]["sign_de"]
    return None


def invite_info(code: str) -> dict:
    found = get_invite_with_person(code)
    if not found.get("ok") or not found.get("data"):
        return {"ok": False, "error": "Diese Einladung gibt es nicht (mehr)."}
    row = found["data"]["person"]
    return {"ok": True, "data": {"name": _first_name(row.get("name")), "sun_sign_de": _sun_sign_of_row(row)}}


def invite_match(code: str, year: int, month: int, day: int,
                 hour: Optional[int] = None, minute: Optional[int] = None) -> dict:
    found = get_invite_with_person(code)
    if not found.get("ok") or not found.get("data"):
        return {"ok": False, "error": "Diese Einladung gibt es nicht (mehr)."}
    me = preview(year, month, day, hour, minute)
    if not me.get("ok"):
        return me

    inviter_row = found["data"]["person"]
    inviter = person_row_to_engine_person(inviter_row)
    guest = {"name": "Du", "year": int(year), "month": int(month), "day": int(day),
             "hour": hour, "minute": minute, **_DEFAULT_PLACE}
    syn = ce.compute_synastry(inviter, guest)
    if not syn.get("ok"):
        return syn
    score = syn["data"].get("score") or {}
    summary = syn["data"].get("summary") or {}
    invite = found["data"]["invite"]
    increment_invite_uses(invite["code"], invite.get("uses"))
    return {
        "ok": True,
        "data": {
            "inviter": {"name": _first_name(inviter_row.get("name")), "sun_sign_de": _sun_sign_of_row(inviter_row)},
            "you": me["data"],
            "score_percent": score_percent(score.get("value")),
            "score_label": score_label(score.get("description")),
            "tone": summary.get("tone"),
            "harmonious": summary.get("harmonious", 0),
            "challenging": summary.get("challenging", 0),
        },
    }
