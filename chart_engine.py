"""
chart_engine.py
Deterministischer Astrologie-Kern fuer die Horoskop-App.

Nimmt Geburtsdaten (Name, Ort als lat/lng, Datum, Zeit) und liefert sauberes
Natal-, Transit- und Synastry-JSON auf Basis der Swiss Ephemeris (via kerykeion).
Kein LLM, keine externen API-Calls -- reine Mathematik, voll reproduzierbar.

Public API (alle geben das Result-Pattern zurueck: {"ok": True, "data": ...}
oder {"ok": False, "error": "..."}):

    compute_natal(person)             -> Geburtshoroskop (Radix)
    compute_transits(person, at=None) -> aktuelle Transite gegen das Chart
    compute_synastry(person_a, b)     -> Beziehungs-Dynamik zweier Charts

Eingabe-Schema `person` (dict):
    {
      "name":   str,
      "year":   int, "month": int, "day": int,
      "hour":   int | None,   # None  -> Zeit unbekannt -> 12:00, time_known=False
      "minute": int | None,
      "lat":    float, "lng": float,   # aus Geocoding beim Onboarding
      "tz_str": str | None,            # optional; sonst aus lat/lng abgeleitet
    }

Abhaengigkeiten:  pip install kerykeion timezonefinder
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from kerykeion import (
    AstrologicalSubjectFactory,
    NatalAspects,
    SynastryAspects,
    RelationshipScoreFactory,
)
from timezonefinder import TimezoneFinder

_TF = TimezoneFinder()

# Placidus -- das Standard-Haeusersystem der westlichen Astrologie.
# (Bewusst NICHT Porphyry, fuer das Co-Star kritisiert wird.)
HOUSE_SYSTEM = "P"

# ---------------------------------------------------------------------------
# Deutsche Labels
# ---------------------------------------------------------------------------
SIGNS_DE = {
    "Ari": "Widder", "Tau": "Stier", "Gem": "Zwillinge", "Can": "Krebs",
    "Leo": "Loewe", "Vir": "Jungfrau", "Lib": "Waage", "Sco": "Skorpion",
    "Sag": "Schuetze", "Cap": "Steinbock", "Aqu": "Wassermann", "Pis": "Fische",
}
ELEMENTS_DE = {"Fire": "Feuer", "Earth": "Erde", "Air": "Luft", "Water": "Wasser"}
QUALITIES_DE = {"Cardinal": "kardinal", "Fixed": "fix", "Mutable": "veraenderlich"}
PLANETS_DE = {
    "Sun": "Sonne", "Moon": "Mond", "Mercury": "Merkur", "Venus": "Venus",
    "Mars": "Mars", "Jupiter": "Jupiter", "Saturn": "Saturn", "Uranus": "Uranus",
    "Neptune": "Neptun", "Pluto": "Pluto", "Chiron": "Chiron",
    "True_North_Lunar_Node": "Mondknoten (Nord)",
    "True_South_Lunar_Node": "Mondknoten (Sued)",
    "Mean_Lilith": "Lilith",
    "Ascendant": "Aszendent", "Medium_Coeli": "MC (Himmelsmitte)",
    "Descendant": "Deszendent", "Imum_Coeli": "IC",
}
ASPECTS_DE = {
    "conjunction": "Konjunktion", "opposition": "Opposition", "trine": "Trigon",
    "square": "Quadrat", "sextile": "Sextil", "quincunx": "Quincunx",
    "semisextile": "Halbsextil", "semisquare": "Halbquadrat",
    "sesquiquadrate": "Anderthalbquadrat", "quintile": "Quintil",
    "biquintile": "Biquintil",
}
_WORD_NUM = {
    "First": 1, "Second": 2, "Third": 3, "Fourth": 4, "Fifth": 5, "Sixth": 6,
    "Seventh": 7, "Eighth": 8, "Ninth": 9, "Tenth": 10, "Eleventh": 11,
    "Twelfth": 12,
}

# Punkte, die wir nach aussen geben
NATAL_POINTS = [
    "sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn",
    "uranus", "neptune", "pluto", "chiron",
    "true_north_lunar_node", "mean_lilith",
]
ANGLES = ["ascendant", "medium_coeli", "descendant", "imum_coeli"]
_CORE_TEN = [
    "sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn",
    "uranus", "neptune", "pluto",
]

HARMONIOUS = {"trine", "sextile"}
CHALLENGING = {"square", "opposition"}


# ---------------------------------------------------------------------------
# Result-Helfer
# ---------------------------------------------------------------------------
def _ok(data: dict) -> dict:
    return {"ok": True, "data": data}


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


# ---------------------------------------------------------------------------
# Eingabe-Normalisierung
# ---------------------------------------------------------------------------
def resolve_timezone(lat: float, lng: float) -> Optional[str]:
    """lat/lng -> IANA-Zeitzone (offline, via timezonefinder)."""
    return _TF.timezone_at(lat=lat, lng=lng)


def _normalize_person(p: dict) -> dict:
    name = p.get("name") or "Unbenannt"
    for field in ("year", "month", "day"):
        if p.get(field) is None:
            raise ValueError(f"Pflichtfeld fehlt: {field}")
    lat, lng = p.get("lat"), p.get("lng")
    if lat is None or lng is None:
        raise ValueError("lat/lng erforderlich -- Geburtsort beim Onboarding geocoden")

    hour, minute = p.get("hour"), p.get("minute")
    time_known = hour is not None
    if not time_known:
        hour, minute = 12, 0          # Naeherung bei unbekannter Geburtszeit
    elif minute is None:
        minute = 0

    tz_str = p.get("tz_str") or resolve_timezone(float(lat), float(lng))
    if not tz_str:
        raise ValueError("Zeitzone konnte nicht bestimmt werden (lat/lng pruefen)")

    return {
        "name": name,
        "year": int(p["year"]), "month": int(p["month"]), "day": int(p["day"]),
        "hour": int(hour), "minute": int(minute),
        "lat": float(lat), "lng": float(lng), "tz_str": tz_str,
        "time_known": time_known,
    }


def _build_subject(np: dict):
    """Normalisierte Person -> kerykeion AstrologicalSubjectModel (offline)."""
    return AstrologicalSubjectFactory.from_birth_data(
        name=np["name"],
        year=np["year"], month=np["month"], day=np["day"],
        hour=np["hour"], minute=np["minute"],
        lat=np["lat"], lng=np["lng"], tz_str=np["tz_str"],
        online=False,
        houses_system_identifier=HOUSE_SYSTEM,
        suppress_geonames_warning=True,
    )


# ---------------------------------------------------------------------------
# Serialisierung
# ---------------------------------------------------------------------------
def _house_num(house_str: Optional[str]) -> Optional[int]:
    if not house_str:
        return None
    return _WORD_NUM.get(house_str.replace("_House", ""))


def _point(pt) -> dict:
    """Ein Planet/Winkel -> flaches, JSON-fertiges dict."""
    return {
        "name": pt.name,
        "name_de": PLANETS_DE.get(pt.name, pt.name),
        "sign": pt.sign,
        "sign_de": SIGNS_DE.get(pt.sign, pt.sign),
        "degree": round(pt.position, 2),     # Grad im Zeichen (0-30)
        "abs_pos": round(pt.abs_pos, 2),     # absolute Laenge (0-360)
        "house": _house_num(pt.house),
        "retrograde": bool(pt.retrograde) if pt.retrograde is not None else False,
        "element": pt.element,
        "element_de": ELEMENTS_DE.get(pt.element, pt.element),
        "quality": pt.quality,
    }


def _distributions(s) -> dict:
    elems = {"Fire": 0, "Earth": 0, "Air": 0, "Water": 0}
    quals = {"Cardinal": 0, "Fixed": 0, "Mutable": 0}
    for key in _CORE_TEN:
        pt = getattr(s, key)
        if pt.element in elems:
            elems[pt.element] += 1
        if pt.quality in quals:
            quals[pt.quality] += 1
    return {
        "elements": {ELEMENTS_DE.get(k, k): v for k, v in elems.items()},
        "qualities": {QUALITIES_DE.get(k, k): v for k, v in quals.items()},
    }


def _lunar(s) -> Optional[dict]:
    lp = getattr(s, "lunar_phase", None)
    if lp is None:
        return None
    d = lp.model_dump() if hasattr(lp, "model_dump") else dict(lp)
    return {
        "name": d.get("moon_phase_name"),
        "phase_num": d.get("moon_phase"),
        "sun_moon_angle": round(d.get("degrees_between_s_m", 0), 2),
    }


# ---------------------------------------------------------------------------
# Datums-Parsing fuer Transite
# ---------------------------------------------------------------------------
def _parse_dt(at: Any) -> datetime:
    """ISO-String oder datetime -> tz-aware UTC datetime. Reines Datum -> 12:00 UTC."""
    if isinstance(at, datetime):
        return at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    s = str(at).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            d = datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d":
                d = d.replace(hour=12)
            return d.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ===========================================================================
# PUBLIC API
# ===========================================================================
def compute_natal(person: dict) -> dict:
    """Geburtshoroskop (Radix) als strukturiertes JSON."""
    try:
        np = _normalize_person(person)
        s = _build_subject(np)

        points = []
        for key in NATAL_POINTS + ANGLES:
            pt = getattr(s, key, None)
            if pt is not None:
                points.append(_point(pt))

        houses = []
        for i, key in enumerate(s.houses_names_list, start=1):
            h = getattr(s, key.lower())
            houses.append({
                "number": i,
                "sign": h.sign,
                "sign_de": SIGNS_DE.get(h.sign, h.sign),
                "cusp_abs_pos": round(h.abs_pos, 2),
            })

        data = {
            "meta": {
                "name": np["name"],
                "local_datetime": s.iso_formatted_local_datetime,
                "utc_datetime": s.iso_formatted_utc_datetime,
                "julian_day": s.julian_day,
                "house_system": s.houses_system_name,
                "zodiac_type": s.zodiac_type,
                "time_known": np["time_known"],
                "lat": np["lat"], "lng": np["lng"], "tz_str": np["tz_str"],
            },
            "big_three": {
                "sun": _point(s.sun),
                "moon": _point(s.moon),
                "ascendant": _point(s.ascendant),
            },
            "points": points,
            "houses": houses,
            "aspects": _natal_aspects(NatalAspects(s).relevant_aspects),
            "distributions": _distributions(s),
            "lunar_phase": _lunar(s),
        }
        return _ok(data)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def _natal_aspects(rel) -> list:
    out = []
    for a in rel:
        out.append({
            "p1": a.p1_name, "p1_de": PLANETS_DE.get(a.p1_name, a.p1_name),
            "p2": a.p2_name, "p2_de": PLANETS_DE.get(a.p2_name, a.p2_name),
            "type": a.aspect, "type_de": ASPECTS_DE.get(a.aspect, a.aspect),
            "orb": round(a.orbit, 2),
            "movement": getattr(a, "aspect_movement", None),
        })
    out.sort(key=lambda x: x["orb"])   # enger Orb = staerker
    return out


def compute_transits(person: dict, at: Any = None) -> dict:
    """Aktuelle (oder zu `at`) Transite gegen das Geburtshoroskop."""
    try:
        np = _normalize_person(person)
        natal = _build_subject(np)

        dt = datetime.now(timezone.utc) if at is None else _parse_dt(at)
        transit = AstrologicalSubjectFactory.from_birth_data(
            name="Transit",
            year=dt.year, month=dt.month, day=dt.day,
            hour=dt.hour, minute=dt.minute,
            lat=np["lat"], lng=np["lng"], tz_str="UTC",
            online=False,
            houses_system_identifier=HOUSE_SYSTEM,
            suppress_geonames_warning=True,
        )

        aspects = []
        for a in SynastryAspects(transit, natal).relevant_aspects:
            aspects.append({
                "transit": a.p1_name, "transit_de": PLANETS_DE.get(a.p1_name, a.p1_name),
                "natal": a.p2_name, "natal_de": PLANETS_DE.get(a.p2_name, a.p2_name),
                "type": a.aspect, "type_de": ASPECTS_DE.get(a.aspect, a.aspect),
                "orb": round(a.orbit, 2),
                "movement": getattr(a, "aspect_movement", None),
            })
        aspects.sort(key=lambda x: x["orb"])

        positions = [_point(getattr(transit, k)) for k in _CORE_TEN]

        data = {
            "meta": {
                "at_utc": dt.astimezone(timezone.utc).isoformat(),
                "natal_name": np["name"],
            },
            "transit_positions": positions,
            "aspects_to_natal": aspects,
        }
        return _ok(data)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def compute_synastry(person_a: dict, person_b: dict) -> dict:
    """Beziehungs-Dynamik zweier Charts: Cross-Aspekte + Kompatibilitaets-Score."""
    try:
        na = _normalize_person(person_a)
        nb = _normalize_person(person_b)
        a = _build_subject(na)
        b = _build_subject(nb)

        aspects = []
        harm = chal = 0
        for x in SynastryAspects(a, b).relevant_aspects:
            if x.aspect in HARMONIOUS:
                harm += 1
            elif x.aspect in CHALLENGING:
                chal += 1
            aspects.append({
                "owner1": x.p1_owner, "p1": x.p1_name,
                "p1_de": PLANETS_DE.get(x.p1_name, x.p1_name),
                "owner2": x.p2_owner, "p2": x.p2_name,
                "p2_de": PLANETS_DE.get(x.p2_name, x.p2_name),
                "type": x.aspect, "type_de": ASPECTS_DE.get(x.aspect, x.aspect),
                "orb": round(x.orbit, 2),
            })
        aspects.sort(key=lambda z: z["orb"])

        sd = RelationshipScoreFactory(a, b).get_relationship_score().model_dump()

        data = {
            "meta": {"a": na["name"], "b": nb["name"]},
            "score": {
                "value": sd.get("score_value"),
                "description": sd.get("score_description"),
                "breakdown": sd.get("score_breakdown"),
            },
            "aspects": aspects,
            # Heuristik als Startpunkt fuer den Synastry-Agenten (nicht als Urteil)
            "summary": {
                "harmonious": harm,
                "challenging": chal,
                "total": len(aspects),
                "tone": ("harmonisch" if harm > chal
                         else "herausfordernd" if chal > harm
                         else "ausgewogen"),
            },
        }
        return _ok(data)
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


# ===========================================================================
# Demo
# ===========================================================================
if __name__ == "__main__":
    import json

    # Beispielperson A -- Meran/Merano, Suedtirol
    person_a = {
        "name": "Gyoergy", "year": 1985, "month": 7, "day": 12,
        "hour": 8, "minute": 30, "lat": 46.6713, "lng": 11.1597,
    }
    # Beispielperson B -- Woergl, Tirol (tz wird aus lat/lng abgeleitet)
    person_b = {
        "name": "Sarah", "year": 1990, "month": 3, "day": 22,
        "hour": 19, "minute": 15, "lat": 47.4894, "lng": 12.0658,
    }

    print("\n=== NATAL (Gyoergy) ===")
    natal = compute_natal(person_a)
    if natal["ok"]:
        d = natal["data"]
        b3 = d["big_three"]
        print(f"Sonne:     {b3['sun']['sign_de']} {b3['sun']['degree']}deg  (Haus {b3['sun']['house']})")
        print(f"Mond:      {b3['moon']['sign_de']} {b3['moon']['degree']}deg  (Haus {b3['moon']['house']})")
        print(f"Aszendent: {b3['ascendant']['sign_de']} {b3['ascendant']['degree']}deg")
        print(f"Zeit bekannt: {d['meta']['time_known']} | Haeusersystem: {d['meta']['house_system']}")
        print(f"Elemente: {d['distributions']['elements']}")
        print(f"Top-Aspekt: {d['aspects'][0]['p1_de']} {d['aspects'][0]['type_de']} "
              f"{d['aspects'][0]['p2_de']} (Orb {d['aspects'][0]['orb']}deg)")
        print(f"Mondphase: {d['lunar_phase']['name']}")
    else:
        print("FEHLER:", natal["error"])

    print("\n=== TRANSITE heute (Gyoergy) ===")
    tr = compute_transits(person_a)
    if tr["ok"]:
        for a in tr["data"]["aspects_to_natal"][:5]:
            print(f"  {a['transit_de']} {a['type_de']} natal {a['natal_de']} "
                  f"(Orb {a['orb']}deg, {a['movement']})")
    else:
        print("FEHLER:", tr["error"])

    print("\n=== SYNASTRY (Gyoergy <-> Sarah) ===")
    syn = compute_synastry(person_a, person_b)
    if syn["ok"]:
        d = syn["data"]
        print(f"Score: {d['score']['value']} ({d['score']['description']})  "
              f"Ton: {d['summary']['tone']}  "
              f"(+{d['summary']['harmonious']} / -{d['summary']['challenging']})")
        for a in d["aspects"][:5]:
            print(f"  {a['owner1']} {a['p1_de']} {a['type_de']} "
                  f"{a['owner2']} {a['p2_de']} (Orb {a['orb']}deg)")
    else:
        print("FEHLER:", syn["error"])

    # Beispiel: unbekannte Geburtszeit
    print("\n=== NATAL ohne Geburtszeit ===")
    no_time = compute_natal({
        "name": "Test", "year": 1979, "month": 11, "day": 2,
        "hour": None, "minute": None, "lat": 47.27, "lng": 11.39,
    })
    print("ok:", no_time["ok"], "| time_known:",
          no_time["data"]["meta"]["time_known"] if no_time["ok"] else no_time["error"])
