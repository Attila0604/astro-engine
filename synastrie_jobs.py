"""
synastrie_jobs.py
Partner-Vergleich (Synastrie) als Hintergrund-Auftrag -- gleiches Muster wie
analysis_jobs.py: Start antwortet sofort, die App fragt kurz nach dem Stand,
die Berechnung laeuft unabhaengig von der Verbindung weiter.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import limits

PHASEN = ("chart", "deutung", "pruefen", "speichern")   # "pruefen" nur bei en/hu/it

_JOBS: dict[tuple, dict] = {}
_TASKS: set = set()
_DAUERN = {"de": deque(maxlen=20), "intl": deque(maxlen=20)}
_STANDARD = {"de": 30, "intl": 45}
AUFRAEUMEN_NACH = 30 * 60


def _gruppe(lang: str) -> str:
    return "de" if lang == "de" else "intl"


def erwartete_dauer(lang: str) -> int:
    werte = _DAUERN[_gruppe(lang)]
    return int(round(sum(werte) / len(werte))) if werte else _STANDARD[_gruppe(lang)]


def _key(owner_id, a_id, b_id, lang) -> tuple:
    return (str(owner_id), str(a_id), str(b_id), lang)


def _aufraeumen() -> None:
    jetzt = time.time()
    for key, job in list(_JOBS.items()):
        if job["status"] != "laeuft" and jetzt - (job.get("ende") or jetzt) > AUFRAEUMEN_NACH:
            _JOBS.pop(key, None)


def stand(owner_id, a_id, b_id, lang: str) -> dict | None:
    _aufraeumen()
    job = _JOBS.get(_key(owner_id, a_id, b_id, lang))
    if not job:
        return None
    jetzt = job.get("ende") or time.time()
    out = {
        "status": job["status"],
        "phase": job["phase"],
        "phasen": [p for p in PHASEN if lang != "de" or p != "pruefen"],
        "vergangen_sekunden": int(jetzt - job["start"]),
        "erwartet_sekunden": job["erwartet"],
        "fehler": job.get("fehler"),
    }
    if job["status"] == "fertig":
        out["ergebnis"] = job.get("ergebnis")
    return out


async def _lauf(key: tuple, owner_id: str, lang: str, erzeugen) -> None:
    job = _JOBS[key]

    def fortschritt(phase: str) -> None:
        job["phase"] = phase

    try:
        ergebnis = await erzeugen(fortschritt)
    except Exception as e:  # noqa: BLE001 -- unerwartet: Limit hier freigeben
        limits.release(owner_id, "synastrie")
        job.update(status="fehler", ende=time.time(), fehler=f"{type(e).__name__}: {e}"[:300])
        return
    if not isinstance(ergebnis, dict) or not ergebnis.get("ok"):
        # erzeugen() hat das Limit in diesem Fall schon selbst freigegeben
        job.update(status="fehler", ende=time.time(),
                   fehler=str((ergebnis or {}).get("error") or "Partner-Vergleich fehlgeschlagen.")[:300])
        return
    job.update(status="fertig", phase="fertig", ende=time.time(), ergebnis=ergebnis.get("data"))
    _DAUERN[_gruppe(lang)].append(job["ende"] - job["start"])


def starten(owner_id, a_id, b_id, lang: str, erzeugen) -> dict:
    """erzeugen(fortschritt) -> Ergebnis wie /mobile/synastry/save. Limit wird hier sofort reserviert (429)."""
    _aufraeumen()
    key = _key(owner_id, a_id, b_id, lang)
    laufend = _JOBS.get(key)
    if laufend and laufend["status"] == "laeuft":
        return stand(owner_id, a_id, b_id, lang)
    limits.reserve(owner_id, "synastrie")
    _JOBS[key] = {"status": "laeuft", "phase": "chart", "start": time.time(), "erwartet": erwartete_dauer(lang)}
    task = asyncio.get_running_loop().create_task(_lauf(key, str(owner_id), lang, erzeugen))
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return stand(owner_id, a_id, b_id, lang)
