"""
geocode.py
Ortsname -> Koordinaten (Geocoding) ueber Nominatim (OpenStreetMap).

Kostenlos, kein API-Key noetig. Laeuft an der API-Grenze, NICHT im
deterministischen Rechenkern -- chart_engine bleibt netzfrei und nimmt
weiterhin nur lat/lng.

Produktions-Muster: einmal beim Onboarding geocoden, das Ergebnis (lat/lng)
am Nutzer-Datensatz speichern und danach nie wieder geocoden.
"""

from __future__ import annotations

from functools import lru_cache

from geopy.geocoders import Nominatim

# Nominatim verlangt einen aussagekraeftigen User-Agent.
_geolocator = Nominatim(user_agent="rgym-astro-engine/1.0", timeout=10)


@lru_cache(maxsize=2048)
def _lookup(place: str):
    """Roher Lookup -- gibt (lat, lng, anzeigename) zurueck oder wirft.
    lru_cache merkt sich nur Erfolge (Exceptions werden nicht gecacht)."""
    loc = _geolocator.geocode(place, language="de")
    if loc is None:
        raise ValueError(f"Ort nicht gefunden: {place!r}")
    return (round(loc.latitude, 6), round(loc.longitude, 6), loc.address)


def geocode_place(place: str) -> dict:
    """Ortsname -> Result {ok, data:{lat,lng,display_name}} | {ok:false,error}."""
    if not place or not place.strip():
        return {"ok": False, "error": "Kein Ort angegeben"}
    try:
        lat, lng, name = _lookup(place.strip())
        return {"ok": True, "data": {"lat": lat, "lng": lng, "display_name": name}}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
