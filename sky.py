"""
sky.py
"Der Himmel heute": allgemeine Himmelsereignisse fuer die Startseite der App.

Liefert (fuer alle Nutzer gleich, kein Geburtsdatum noetig):
  - Mondphase, Beleuchtung und Mondzeichen
  - Sonnenzeichen
  - ruecklaeufige Planeten inkl. Datum, an dem sie wieder direktlaeufig werden
  - Planeten, die in den naechsten 14 Tagen rueckläufig werden
  - naechster Neumond und Vollmond

Reine Mathematik ueber die Swiss Ephemeris (pyswisseph, kommt mit kerykeion),
kein Claude-Aufruf. Das Ergebnis wird pro Stunde im Speicher gecacht.

Oeffentliche Funktion (Result-Pattern):
    sky_today(now=None) -> {"ok": True, "data": {...}}
"""

from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

import swisseph as swe

from chart_engine import SIGNS_DE

_SIGN_KEYS = ["Ari", "Tau", "Gem", "Can", "Leo", "Vir",
              "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis"]

# Planeten, deren Ruecklaeufigkeit fuer Nutzer interessant ist
_RETRO_PLANETS = [
    (swe.MERCURY, "Merkur"),
    (swe.VENUS, "Venus"),
    (swe.MARS, "Mars"),
    (swe.JUPITER, "Jupiter"),
    (swe.SATURN, "Saturn"),
    (swe.URANUS, "Uranus"),
    (swe.NEPTUNE, "Neptun"),
    (swe.PLUTO, "Pluto"),
]

_PHASES = [
    (1.85, "Neumond", "Zeit für Neuanfänge und klare Absichten."),
    (5.5, "Zunehmende Sichel", "Etwas Neues nimmt Form an."),
    (9.2, "Erstes Viertel", "Zeit zu handeln und dranzubleiben."),
    (12.9, "Zunehmender Mond", "Wachstum, Klärung, Ausrichtung."),
    (16.6, "Vollmond", "Höhepunkt: sehen, was gereift ist."),
    (20.3, "Abnehmender Mond", "Loslassen und dankbar sein."),
    (23.99, "Letztes Viertel", "Aufräumen und Klarheit schaffen."),
    (29.6, "Abnehmende Sichel", "Ruhe, Rückzug, Vorbereitung."),
]
_SYNODIC = 29.530588853

_cache_lock = threading.Lock()
_cache: dict[str, dict] = {}


def _jd(dt: datetime) -> float:
    dt = dt.astimezone(timezone.utc)
    return swe.julday(dt.year, dt.month, dt.day,
                      dt.hour + dt.minute / 60 + dt.second / 3600)


def _from_jd(jd: float) -> datetime:
    y, m, d, h = swe.revjul(jd)
    return datetime(y, m, d, tzinfo=timezone.utc) + timedelta(hours=h)


def _lon(jd: float, body: int) -> float:
    return swe.calc_ut(jd, body)[0][0]


def _speed(jd: float, body: int) -> float:
    return swe.calc_ut(jd, body)[0][3]


def _sign_de(lon: float) -> str:
    key = _SIGN_KEYS[int(lon // 30) % 12]
    return SIGNS_DE.get(key, key)


def _elongation(jd: float) -> float:
    """Winkel Mond minus Sonne, 0..360 (0 = Neumond, 180 = Vollmond)."""
    return (_lon(jd, swe.MOON) - _lon(jd, swe.SUN)) % 360.0


def _next_lunation(jd_start: float, target: float) -> datetime:
    """Naechster Zeitpunkt, an dem die Elongation `target` (0 oder 180) erreicht."""
    def diff(jd):
        # vorzeichenbehafteter Abstand zur Zielelongation, -180..180
        return (_elongation(jd) - target + 180.0) % 360.0 - 180.0

    step = 0.25  # 6 Stunden
    jd = jd_start
    prev = diff(jd)
    for _ in range(int(32 / step)):
        nxt_jd = jd + step
        cur = diff(nxt_jd)
        if prev < 0 <= cur:  # Vorzeichenwechsel -> Ereignis im Intervall
            lo, hi = jd, nxt_jd
            for _ in range(30):  # Bisektion auf Sekunden genau
                mid = (lo + hi) / 2
                if diff(mid) < 0:
                    lo = mid
                else:
                    hi = mid
            return _from_jd(hi)
        jd, prev = nxt_jd, cur
    return _from_jd(jd_start + _SYNODIC)


def _station(jd_start: float, body: int, want_direct: bool, max_days: int) -> Optional[datetime]:
    """Naechster Tag, an dem der Planet die Richtung wechselt (Tagesgenauigkeit)."""
    for day in range(1, max_days + 1):
        speed = _speed(jd_start + day, body)
        if (speed >= 0) == want_direct:
            return _from_jd(jd_start + day)
    return None


def _compute(now: datetime) -> dict:
    jd = _jd(now)
    sun = _lon(jd, swe.SUN)
    moon = _lon(jd, swe.MOON)
    elong = (moon - sun) % 360.0
    age = elong / 360.0 * _SYNODIC
    illum = round((1 - math.cos(math.radians(elong))) / 2 * 100)
    phase_name, phase_text = _PHASES[-1][1], _PHASES[-1][2]
    for limit, name, text in _PHASES:
        if age < limit:
            phase_name, phase_text = name, text
            break

    retrograde = []
    upcoming = []
    for body, name in _RETRO_PLANETS:
        if _speed(jd, body) < 0:
            until = _station(jd, body, want_direct=True, max_days=220)
            retrograde.append({
                "planet": name,
                "until": until.date().isoformat() if until else None,
            })
        else:
            starts = _station(jd, body, want_direct=False, max_days=14)
            if starts:
                upcoming.append({"planet": name, "from": starts.date().isoformat()})

    new_moon = _next_lunation(jd, 0.0)
    full_moon = _next_lunation(jd, 180.0)

    return {
        "at_utc": now.astimezone(timezone.utc).isoformat(timespec="minutes"),
        "moon": {
            "phase": phase_name,
            "phase_text": phase_text,
            "illumination": illum,
            "sign_de": _sign_de(moon),
        },
        "sun": {"sign_de": _sign_de(sun)},
        "retrograde": retrograde,
        "retrograde_soon": upcoming,
        "next_new_moon": new_moon.isoformat(timespec="minutes"),
        "next_full_moon": full_moon.isoformat(timespec="minutes"),
    }


def sky_today(now: Optional[datetime] = None) -> dict:
    """Himmel heute, pro Stunde gecacht."""
    try:
        now = now or datetime.now(timezone.utc)
        key = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
        with _cache_lock:
            if key in _cache:
                return {"ok": True, "data": _cache[key]}
        data = _compute(now)
        with _cache_lock:
            _cache.clear()
            _cache[key] = data
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
