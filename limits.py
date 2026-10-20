"""
limits.py
Kostenbremse fuer Soraya: Tageslimits pro Nutzer.

Jede Claude-Anfrage kostet Geld. Damit ein einzelner Account (oder ein
geklauter Token) nicht unbegrenzt Kosten erzeugen kann, zaehlt das Backend
pro Nutzer und Tag (UTC) mit, wie oft teure Aktionen ausgefuehrt wurden.

Die Zaehler liegen im Speicher des Prozesses. Railway startet die Engine mit
genau einem uvicorn-Prozess, daher reicht das. Nach einem Neustart/Deploy
beginnen die Zaehler bei 0 -- fuer eine Kostenbremse ist das in Ordnung.

Limits sind per Railway-Variable anpassbar, z. B. LIMIT_CHAT=60.
Ein Wert von 0 (oder kleiner) schaltet das jeweilige Limit ab.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from fastapi import HTTPException

# kind -> (ENV-Name, Standardwert, Text fuer die Fehlermeldung)
LIMITS = {
    "chat": ("LIMIT_CHAT", 40, "Chat-Nachrichten"),
    "horoskop": ("LIMIT_HOROSKOP", 12, "neue Horoskope"),
    "analyse": ("LIMIT_ANALYSE", 3, "neue Analysen"),
    "synastrie": ("LIMIT_SYNASTRIE", 10, "Partner-Vergleiche"),
    "berechnung": ("LIMIT_BERECHNUNG", 300, "Chart-Berechnungen"),
    "gedaechtnis": ("LIMIT_GEDAECHTNIS", 10, "Gedaechtnis-Updates"),
    # Schnellstart ohne Login, pro IP-Adresse
    # Viele Handy-Nutzer teilen sich eine IP (CGNAT), daher grosszuegig.
    "oeffentlich": ("LIMIT_OEFFENTLICH", 200, "Vorschau-Abfragen"),
    # Anonyme Zaehler-Ereignisse (/public/event): kosten nichts, eigener Topf,
    # damit sie Vorschau und Horoskop nicht aufbrauchen.
    "ereignis": ("LIMIT_EREIGNIS", 500, "Ereignisse"),
    # Sternzeichen-Tageshoroskop: Fertige Horoskope zaehlen NICHT. Nur wenn eine
    # Anfrage ein neues Horoskop bei Claude ausloest (max. 48 am Tag moeglich, daher
    # reicht 60 auch, wenn ein Besucher oder Googlebot als Erster alle 48 Seiten oeffnet).
    "sternzeichen_neu": ("LIMIT_STERNZEICHEN_NEU", 60, "neue Sternzeichen-Horoskope"),
    # Gesamtdeckel fuer alle neuen Sternzeichen-Horoskope (alle IPs zusammen)
    "sternzeichen_tag": ("LIMIT_STERNZEICHEN_TAG", 120, "neue Sternzeichen-Horoskope (gesamt)"),
}

_lock = threading.Lock()
_counts: dict[tuple[str, str], int] = {}
_day = ""


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def limit_for(kind: str) -> int:
    env_name, default, _ = LIMITS[kind]
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def reserve(owner_id: str, kind: str) -> None:
    """
    Zaehlt eine Aktion. Ist das Tageslimit erreicht, wird HTTP 429 geworfen.
    Schlaegt die Aktion danach fehl, mit release() wieder freigeben.
    """
    global _day
    limit = limit_for(kind)
    if limit <= 0:
        return

    today = _today()
    with _lock:
        if today != _day:
            _counts.clear()
            _day = today
        key = (str(owner_id), kind)
        used = _counts.get(key, 0)
        if used >= limit:
            label = LIMITS[kind][2]
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Tageslimit erreicht: maximal {limit} {label} pro Tag. "
                    "Morgen geht es weiter ✨"
                ),
            )
        _counts[key] = used + 1


def release(owner_id: str, kind: str) -> None:
    """Gibt eine reservierte Aktion zurueck (z. B. wenn Claude ausgefallen ist)."""
    with _lock:
        key = (str(owner_id), kind)
        if _counts.get(key, 0) > 0:
            _counts[key] -= 1


def reset() -> None:
    """Nur fuer Tests."""
    global _day
    with _lock:
        _counts.clear()
        _day = ""
