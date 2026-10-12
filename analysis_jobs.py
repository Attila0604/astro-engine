"""
analysis_jobs.py
Tiefen-Analyse als Hintergrund-Auftrag.

Warum: Eine Analyse dauert 1-2 Minuten. Als eine einzige lange Anfrage riss sie am
Handy leicht ab (Funkloch, Bildschirm aus, App im Hintergrund) -- die App meldete
dann "schlechte Verbindung", obwohl der Server oft trotzdem fertig wurde, oder der
Auftrag ging verloren. Jetzt antwortet der Start sofort, die Analyse laeuft hier
unabhaengig von der Verbindung weiter, und die App fragt kurz nach dem Stand
(Phase, vergangene und erwartete Sekunden).

Der Stand liegt im Speicher (ein Prozess). Geht er durch einen Neustart verloren,
sieht die App das ("keiner") und schaut, ob die Analyse schon gespeichert ist.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import limits
from analysis import generate_full_analysis
from supabase_client import get_person, person_row_to_engine_person, save_analysis

# Reihenfolge der Schritte (die App zeigt sie an); "pruefen" nur bei en/hu/it
PHASEN = ("chart", "bereiche", "verweben", "pruefen", "speichern")

_JOBS: dict[tuple, dict] = {}
_TASKS: set = set()                      # starke Referenzen, sonst raeumt Python laufende Tasks weg
_DAUERN = {"de": deque(maxlen=20), "intl": deque(maxlen=20)}
_STANDARD = {"de": 75, "intl": 100}      # Sekunden, bis echte Messwerte vorliegen
AUFRAEUMEN_NACH = 30 * 60


def _gruppe(lang: str) -> str:
    return "de" if lang == "de" else "intl"


def erwartete_dauer(lang: str) -> int:
    werte = _DAUERN[_gruppe(lang)]
    if not werte:
        return _STANDARD[_gruppe(lang)]
    return int(round(sum(werte) / len(werte)))


def _aufraeumen() -> None:
    jetzt = time.time()
    for key, job in list(_JOBS.items()):
        if job["status"] != "laeuft" and jetzt - (job.get("ende") or jetzt) > AUFRAEUMEN_NACH:
            _JOBS.pop(key, None)


def stand(owner_id: str, person_id: str, lang: str) -> dict | None:
    """Oeffentlicher Stand eines Auftrags (ohne interne Felder) oder None."""
    _aufraeumen()
    job = _JOBS.get((str(owner_id), str(person_id), lang))
    if not job:
        return None
    jetzt = job.get("ende") or time.time()
    return {
        "status": job["status"],
        "phase": job["phase"],
        "phasen": [p for p in PHASEN if lang != "de" or p != "pruefen"],
        "vergangen_sekunden": int(jetzt - job["start"]),
        "erwartet_sekunden": job["erwartet"],
        "fehler": job.get("fehler"),
        "analysis_id": job.get("analysis_id"),
    }


async def _lauf(key: tuple, owner_id: str, person_id: str, lang: str) -> None:
    job = _JOBS[key]

    def fortschritt(phase: str) -> None:
        job["phase"] = phase

    try:
        person_row = get_person(owner_id, person_id)
        if not person_row["ok"] or not person_row.get("data"):
            raise RuntimeError(person_row.get("error") or "Profil nicht gefunden.")
        person = person_row_to_engine_person(person_row["data"])
        reading = await generate_full_analysis(person, lang, fortschritt=fortschritt)
        if not reading["ok"]:
            raise RuntimeError(reading.get("error") or "Analyse fehlgeschlagen.")
        job["phase"] = "speichern"
        saved = save_analysis(owner_id, person_id, reading, lang)
        if not saved["ok"]:
            raise RuntimeError(saved.get("error") or "Speichern fehlgeschlagen.")
        job.update(status="fertig", phase="fertig", ende=time.time(),
                   analysis_id=(saved.get("data") or {}).get("id"))
        _DAUERN[_gruppe(lang)].append(job["ende"] - job["start"])
    except Exception as e:  # noqa: BLE001 -- jeder Fehler landet beim Nutzer, nie still
        limits.release(owner_id, "analyse")
        job.update(status="fehler", ende=time.time(), fehler=f"{type(e).__name__}: {e}"[:300])


def starten(owner_id: str, person_id: str, lang: str) -> dict:
    """Startet eine neue Analyse im Hintergrund (oder liefert den laufenden Auftrag).
    Die Kostenbremse greift hier sofort (HTTP 429), nicht erst im Hintergrund."""
    _aufraeumen()
    key = (str(owner_id), str(person_id), lang)
    laufend = _JOBS.get(key)
    if laufend and laufend["status"] == "laeuft":
        return stand(owner_id, person_id, lang)
    limits.reserve(owner_id, "analyse")
    _JOBS[key] = {"status": "laeuft", "phase": "chart", "start": time.time(), "erwartet": erwartete_dauer(lang)}
    task = asyncio.get_running_loop().create_task(_lauf(key, str(owner_id), str(person_id), lang))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return stand(owner_id, person_id, lang)
