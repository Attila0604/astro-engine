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
   Himmel von heute. Wird pro Zeichen, Sprache und Tag nur EINMAL bei Claude
   erzeugt und dann aus der Tabelle sign_horoscopes (und dem Speicher) geliefert
   (max. 12 Zeichen x 4 Sprachen = 48 Erzeugungen/Tag).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from datetime import date, datetime, timezone
from typing import Callable, Optional

import chart_engine as ce
import namen
from agents import PERSONA, _call_claude, lektorat, norm_lang, with_lang
from sky import sky_today
from supabase_client import get_latest_sign_horoscope, get_sign_horoscope, save_sign_horoscope

SIGN_KEYS = ["Ari", "Tau", "Gem", "Can", "Leo", "Vir", "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis"]
SIGN_HOROSCOPE_MODEL = os.environ.get("SIGN_HOROSCOPE_MODEL", os.environ.get("HOROSCOPE_MODEL", "claude-sonnet-4-6"))

# Standardort fuer den Schnellstart (keine Ortseingabe, kein Geocoding)
_DEFAULT_PLACE = {"lat": 48.2082, "lng": 16.3738, "tz_str": "Europe/Vienna"}

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


def _today_sky_text(lang: str = "de") -> str:
    """Planetenstaende von heute als kurzer Text fuer den Prompt (Namen in der Zielsprache)."""
    if norm_lang(lang) != "de":
        return _today_sky_text_intl(norm_lang(lang))
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


def _today_sky_text_intl(lang: str) -> str:
    now = datetime.now(timezone.utc)
    sky = ce.compute_transits({"name": "Himmel", "year": now.year, "month": now.month, "day": now.day,
                               "hour": 12, "minute": 0, **_DEFAULT_PLACE})
    lines = []
    if sky.get("ok"):
        for p in sky["data"]["transit_positions"]:
            retro = " (retrograde)" if p.get("retrograde") else ""
            lines.append(f"- {namen.planet(p.get('name'), lang)} in {namen.zeichen(p.get('sign') or p.get('sign_de'), lang)}{retro}")
    s = sky_today()
    if s.get("ok"):
        m = s["data"]["moon"]
        phasen = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon",
                  "Waning Gibbous", "Last Quarter", "Waning Crescent"]
        i = m.get("phase_index")
        phase = phasen[i] if isinstance(i, int) and 0 <= i < 8 else "Moon phase"
        lines.append(f"- {phase} ({m.get('illumination')} % illuminated)")
    return "\n".join(lines)


def _system() -> str:
    return (
        f"{PERSONA}\n\n"
        "Deine Aufgabe: Schreibe ein ALLGEMEINES Tageshoroskop für ein Sonnenzeichen, "
        "passend zum tatsächlichen Himmel von heute (Planetenstände unten). Es richtet sich an "
        "alle Menschen mit diesem Sonnenzeichen, nicht an eine bestimmte Person – erwähne das "
        "aber nicht ausdrücklich. Beziehe dich konkret auf 1–2 der heutigen Stände, übersetzt in "
        "Alltagssprache. Kurz, warm, ehrlich, ohne Dramatik. Weniger ist mehr: niemand liest am Handy "
        "lange Texte – lieber ein klarer, eleganter Gedanke als viele.\n\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Markdown, in genau diesem Format:\n"
        "{\n"
        '  "stimmung": "<Schlagzeile, max 7 Wörter>",\n'
        '  "text": "<2–3 Sätze, zusammen höchstens 55 Wörter, du-Form, ein Absatz>",\n'
        '  "tipp": "<ein konkreter Tipp, 1 Satz>",\n'
        '  "liebe": "<1–2 Sätze>",\n'
        '  "beruf": "<1–2 Sätze>"\n'
        "}"
    )


_KEYS = ("stimmung", "text", "tipp", "liebe", "beruf")


def _als_text(wert) -> Optional[str]:
    """Claude liefert manchmal Listen (z. B. Absaetze) statt Text -> immer Text oder None."""
    if wert is None:
        return None
    if isinstance(wert, list):
        wert = "\n\n".join(str(x).strip() for x in wert if x is not None and str(x).strip())
    elif not isinstance(wert, str):
        wert = str(wert)
    wert = wert.strip()
    return wert or None


def _bis_satzende(text: str) -> str:
    """Abgeschnittenen Text (Antwort zu lang) am letzten Satzende kappen."""
    ende = max(text.rfind(z) for z in ".!?…")
    return text[: ende + 1] if ende >= 20 else text


_FELD_MUSTER = {k: re.compile(r'"' + k + r'"\s*:\s*"((?:[^"\\]|\\.)*)("?)', re.S) for k in _KEYS}


def _parse(raw: str) -> dict:
    out = {k: None for k in _KEYS}
    raw = (raw or "").strip()
    if not raw:
        return out
    try:
        obj = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
        if isinstance(obj, dict):
            return {k: _als_text(obj.get(k)) for k in _KEYS}
    except Exception:
        pass
    if "{" in raw:
        # Kaputtes oder abgeschnittenes JSON: Felder einzeln retten, nie rohes JSON zeigen
        for k, muster in _FELD_MUSTER.items():
            m = muster.search(raw)
            if not m:
                continue
            try:
                wert = json.loads('"' + m.group(1) + '"')
            except Exception:
                wert = m.group(1).replace('\\"', '"').replace("\\n", "\n")
            wert = _als_text(wert)
            if wert and not m.group(2):   # ohne schliessendes Anfuehrungszeichen = abgeschnitten
                wert = _bis_satzende(wert)
            out[k] = wert
        return out
    out["text"] = raw
    return out


def _brauchbar(parsed: dict) -> bool:
    text = parsed.get("text")
    return isinstance(text, str) and len(text.strip()) >= 20


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


# ---------------------------------------------------------------------------
# Sternzeichen-Tageshoroskop: robust und sparsam
# - Fertige Horoskope liegen zusaetzlich im Speicher (kein DB-Zugriff, kein Limit).
# - Pro Zeichen+Sprache+Tag laeuft hoechstens EINE Erzeugung gleichzeitig; alle
#   anderen Anfragen warten auf dieselbe (kein doppelter Claude-Aufruf).
# - Klappt die Erzeugung nicht (Claude-Fehler, kaputte Antwort) oder dauert sie
#   zu lange, wird das letzte gespeicherte Horoskop dieses Zeichens geliefert.
# - Nach einem Fehlschlag hoechstens ein weiterer Versuch am Tag (nach einer Pause).
# ---------------------------------------------------------------------------
WARTEN_SEK = float(os.environ.get("SIGN_HOROSCOPE_WARTEN", "12") or 12)
MAX_VERSUCHE = int(os.environ.get("SIGN_HOROSCOPE_VERSUCHE", "6") or 6)
PAUSE_NACH_FEHLER_SEK = 600
GLEICHZEITIG = int(os.environ.get("SIGN_HOROSCOPE_GLEICHZEITIG", "4") or 4)

_fertig: dict[str, dict] = {}            # "Lib:hu:2026-10-20" -> data
_laufend: dict[str, asyncio.Task] = {}   # laufende Erzeugungen
_versuche: dict[str, list] = {}          # key -> [anzahl, zeitpunkt_letzter_versuch]
_fehler: dict[str, str] = {}             # key -> letzter Fehler (fuer /public/horoskop-status)
_sem: Optional[asyncio.Semaphore] = None
_sem_loop = None


def _semaphore() -> asyncio.Semaphore:
    """Begrenzt gleichzeitige Claude-Aufrufe (z. B. wenn morgens viele Seiten zugleich laden)."""
    global _sem, _sem_loop
    loop = asyncio.get_running_loop()
    if _sem is None or _sem_loop is not loop:
        _sem, _sem_loop = asyncio.Semaphore(max(1, GLEICHZEITIG)), loop
    return _sem


def _aufraeumen(today: str) -> None:
    for d in (_fertig, _versuche, _fehler):
        for k in [k for k in d if not k.endswith(":" + today)]:
            d.pop(k, None)


def reset_cache() -> None:
    """Nur fuer Tests."""
    global _sem, _sem_loop
    _fertig.clear()
    _laufend.clear()
    _versuche.clear()
    _sem = _sem_loop = None


def _norm_sign(sign) -> Optional[str]:
    s = (sign or "").strip()
    for k in SIGN_KEYS:
        if k.lower() == s.lower():
            return k
    return None


async def _ersatz(sign: str, lang: str, today: str) -> Optional[dict]:
    """Letztes gespeichertes Horoskop (z. B. von gestern) fuer dieses Zeichen und diese Sprache."""
    try:
        alt = await asyncio.to_thread(get_latest_sign_horoscope, sign, today, lang)
    except Exception:
        return None
    if alt.get("ok") and alt.get("data") and alt["data"].get("body"):
        return _row_to_data(alt["data"], sign, "fallback")
    return None


async def _erzeugen(sign: str, lang: str, today: str, key: str) -> Optional[dict]:
    """Ein neues Horoskop bei Claude. Gibt None zurueck, wenn es nicht geklappt hat (wirft nie)."""
    eintrag = _versuche.setdefault(key, [0, 0.0])
    eintrag[0] += 1
    eintrag[1] = time.monotonic()
    try:
        async with _semaphore():
            if lang == "de":
                himmel = await asyncio.to_thread(_today_sky_text)
                user = (f"Sonnenzeichen: {ce.SIGNS_DE.get(sign, sign)}\n\nDer Himmel heute ({today}):\n{himmel}\n\n"
                        "Schreibe nun das Tageshoroskop als JSON.")
            else:
                himmel = await asyncio.to_thread(_today_sky_text, lang)
                user = (f"Sun sign: {namen.zeichen(sign, lang)}\n\nThe sky today ({today}):\n{himmel}\n\n"
                        "Now write the daily horoscope as JSON.")
            # Ungarisch/Italienisch brauchen mehr Tokens -- abgeschnittenes JSON vermeiden
            max_tokens = 900 if lang == "de" else 1500
            raw = await _call_claude(with_lang(_system(), lang), user, max_tokens=max_tokens,
                                     model=SIGN_HOROSCOPE_MODEL)
            parsed = _parse(raw)
            if not _brauchbar(parsed):
                print(f"[sign-horoscope] unbrauchbare Antwort {key}: {raw[:200]!r}", flush=True)
                _fehler[key] = f"unbrauchbare Antwort: {raw[:120]!r}"
                return None
            try:
                geprueft = await lektorat(parsed, lang, immer=norm_lang(lang) == "hu")   # Ungarisch immer pruefen
                if not _brauchbar(geprueft):
                    geprueft = parsed
            except Exception as e:  # Lektorat ist nur Feinschliff -- nie daran scheitern
                print(f"[sign-horoscope] Lektorat-Fehler {key}: {type(e).__name__}: {e}", flush=True)
                geprueft = {k: namen.ersetze_namen(v, lang) if isinstance(v, str) else v for k, v in parsed.items()}
    except Exception as e:
        print(f"[sign-horoscope] Erzeugung fehlgeschlagen {key}: {type(e).__name__}: {e}", flush=True)
        _fehler[key] = f"{type(e).__name__}: {str(e)[:200]}"
        return None

    row = {
        "sign": sign, "target_date": today, "stimmung": geprueft.get("stimmung"), "body": geprueft.get("text"),
        "tipp": geprueft.get("tipp"), "liebe": geprueft.get("liebe"), "beruf": geprueft.get("beruf"),
        "model": SIGN_HOROSCOPE_MODEL, "lang": lang,
    }
    data = _row_to_data(row, sign, "created")
    # Im Speicher merken, auch wenn das Speichern in der DB scheitert -> kein zweiter Claude-Aufruf
    _fertig[key] = {**data, "source": "cached"}
    try:
        saved = await asyncio.to_thread(save_sign_horoscope, row)
        if not saved.get("ok"):
            print(f"[sign-horoscope] Speichern fehlgeschlagen {key}: {saved.get('error')}", flush=True)
    except Exception as e:
        print(f"[sign-horoscope] Speichern fehlgeschlagen {key}: {type(e).__name__}: {e}", flush=True)
    return data


async def sign_horoscope(sign: str, lang: str = "de", reserve: Optional[Callable[[], None]] = None) -> dict:
    """
    reserve: wird NUR aufgerufen, wenn diese Anfrage eine neue Erzeugung bei Claude startet
    (Tageslimit). Wirft es (z. B. HTTP 429), wird stattdessen das letzte Horoskop geliefert.
    """
    key_sign = _norm_sign(sign)
    if not key_sign:
        return {"ok": False, "error": "Unbekanntes Sternzeichen."}
    sign = key_sign
    lang = norm_lang(lang)
    today = datetime.now(timezone.utc).date().isoformat()
    key = f"{sign}:{lang}:{today}"
    _aufraeumen(today)

    if key in _fertig:
        return {"ok": True, "data": dict(_fertig[key])}

    task = _laufend.get(key)
    if task is None:
        cached = await asyncio.to_thread(get_sign_horoscope, sign, today, lang)
        if cached.get("ok") and cached.get("data") and cached["data"].get("body"):
            data = _row_to_data(cached["data"], sign, "cached")
            _fertig[key] = data
            return {"ok": True, "data": dict(data)}
        task = _laufend.get(key)   # waehrend der DB-Abfrage gestartet?

    if task is None:
        if key in _fertig:
            return {"ok": True, "data": dict(_fertig[key])}
        anzahl, zuletzt = _versuche.get(key, [0, 0.0])
        gesperrt = anzahl >= MAX_VERSUCHE or (anzahl > 0 and time.monotonic() - zuletzt < PAUSE_NACH_FEHLER_SEK)
        if not gesperrt and reserve is not None:
            try:
                reserve()
            except Exception:
                gesperrt = True
        if gesperrt:
            ersatz = await _ersatz(sign, lang, today)
            if ersatz:
                return {"ok": True, "data": ersatz}
            return {"ok": False, "error": "Das Tageshoroskop ist gerade nicht verfügbar. Bitte später nochmal versuchen."}
        task = asyncio.ensure_future(_erzeugen(sign, lang, today, key))
        _laufend[key] = task
        task.add_done_callback(lambda _t, k=key: _laufend.pop(k, None))

    try:
        data = await asyncio.wait_for(asyncio.shield(task), timeout=WARTEN_SEK)
    except asyncio.TimeoutError:
        # Dauert zu lange: Erzeugung laeuft im Hintergrund weiter, Besucher bekommt das letzte Horoskop
        ersatz = await _ersatz(sign, lang, today)
        if ersatz:
            return {"ok": True, "data": ersatz}
        data = await asyncio.shield(task)
    if data:
        return {"ok": True, "data": data}
    ersatz = await _ersatz(sign, lang, today)
    if ersatz:
        return {"ok": True, "data": ersatz}
    return {"ok": False, "error": "Das Tageshoroskop ist gerade nicht verfügbar. Bitte später nochmal versuchen."}


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


def _sun_sign_key_of_row(row: dict) -> Optional[str]:
    """Sprachneutraler Schluessel (z. B. "Gem") fuer die Uebersetzung in der App."""
    chart = row.get("chart_json") or {}
    key = ((chart.get("big_three") or {}).get("sun") or {}).get("sign")
    if key:
        return key
    natal = ce.compute_natal(person_row_to_engine_person(row))
    if natal.get("ok"):
        return natal["data"]["big_three"]["sun"]["sign"]
    return None


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
    return {"ok": True, "data": {"name": _first_name(row.get("name")), "sun_sign_de": _sun_sign_of_row(row),
                                 "sun_sign": _sun_sign_key_of_row(row)}}


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
            "inviter": {"name": _first_name(inviter_row.get("name")), "sun_sign_de": _sun_sign_of_row(inviter_row),
                        "sun_sign": _sun_sign_key_of_row(inviter_row)},
            "you": me["data"],
            "score_percent": score_percent(score.get("value")),
            "score_label": score_label(score.get("description")),
            "score_description": score.get("description"),
            "tone": summary.get("tone"),
            "harmonious": summary.get("harmonious", 0),
            "challenging": summary.get("challenging", 0),
        },
    }


def horoskop_status() -> dict:
    """Stand der Tageshoroskope heute (ohne Inhalte, ohne Geheimnisse) -- zur Fehlersuche."""
    today = datetime.now(timezone.utc).date().isoformat()
    heute = lambda d: sorted(k.rsplit(":", 1)[0] for k in d if k.endswith(today))
    return {"ok": True, "data": {"datum": today, "modell": SIGN_HOROSCOPE_MODEL, "warten_sek": WARTEN_SEK,
            "fertig": heute(_fertig), "laeuft": heute(_laufend),
            "versuche": {k.rsplit(":", 1)[0]: v[0] for k, v in _versuche.items() if k.endswith(today)},
            "fehler": {k.rsplit(":", 1)[0]: v for k, v in _fehler.items() if k.endswith(today)}}}
