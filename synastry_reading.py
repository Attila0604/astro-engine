"""
synastry_reading.py
Beziehungs-Agent: verwandelt die berechneten Synastrie-Aspekte in eine
warme, konkrete Deutung -- als Gesamttext plus vier Karten-Feldern
(Harmonie, Spannung, Anziehung, Kommunikation), passend zur Web-App.

Ablauf:
  1. chart_engine.compute_synastry() liefert Aspekte + Score (deterministisch).
  2. Dieser Agent (Claude) macht daraus die Deutung, auf Deutsch, "ihr"-Form.

Oeffentliche Funktion (Result-Pattern):
    await generate_synastry_reading(name_a, name_b, syn_data) -> {"ok": True, "data": {...}}
"""

from __future__ import annotations

import json
import os

from agents import PERSONA, _call_claude

SYNASTRY_MODEL = os.environ.get(
    "SYNASTRY_MODEL", os.environ.get("HOROSCOPE_MODEL", "claude-sonnet-4-6")
)

_FIELDS = ("text", "harmonie", "spannung", "anziehung", "kommunikation")

_MAX_ASPECTS = 12  # engste zuerst; mehr ist fuer eine Deutung Rauschen


def _aspects_to_text(name_a: str, name_b: str, syn: dict) -> str:
    lines = [f"Synastrie zwischen {name_a} (Person A) und {name_b} (Person B)."]
    score = syn.get("score")
    if isinstance(score, (int, float)):
        lines.append(f"Kompatibilitaets-Score: {score}.")

    aspects = (syn.get("aspects") or [])[:_MAX_ASPECTS]
    if aspects:
        lines.append("\nDie engsten Cross-Aspekte (engste zuerst):")
        for a in aspects:
            lines.append(
                f"- {a.get('p1_de', a.get('p1'))} von {name_a} {a.get('type_de', a.get('type'))} "
                f"{a.get('p2_de', a.get('p2'))} von {name_b} (Orb {a.get('orb')})"
            )
    else:
        lines.append("\n(keine engen Cross-Aspekte -- eine lose, freie Verbindung)")
    return "\n".join(lines)


def _system() -> str:
    return (
        f"{PERSONA}\n\n"
        "Deine Aufgabe: Deute die Synastrie (Beziehungs-Astrologie) zweier Menschen "
        "anhand ihrer Cross-Aspekte. Sprich beide gemeinsam in der ihr-Form an. "
        "Uebersetze die Aspekte in gelebte Beziehungssprache statt Fachjargon; "
        "nenne Planeten nur, wenn es das Bild greifbarer macht. Sei warm und ehrlich: "
        "Spannungsaspekte sind Wachstumsfelder, keine Urteile. Erfinde nichts, "
        "was die Aspekte nicht hergeben.\n\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Markdown, ohne Vorrede, "
        "in genau diesem Format:\n"
        "{\n"
        '  "text": "<3-4 warme Absaetze Gesamtdeutung eurer Verbindung>",\n'
        '  "harmonie": "<wo eure Verbindung leicht fliesst, 2-3 Saetze>",\n'
        '  "spannung": "<eure Lern- und Wachstumsfelder, 2-3 Saetze>",\n'
        '  "anziehung": "<was euch magnetisch verbindet, 2-3 Saetze>",\n'
        '  "kommunikation": "<wie ihr einander versteht und erreicht, 2-3 Saetze>"\n'
        "}"
    )


def _parse_json(raw: str) -> dict:
    """Robust: schneidet das erste {...} heraus, faellt sonst auf Rohtext zurueck."""
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        obj = json.loads(raw[start:end])
        return {k: obj.get(k) for k in _FIELDS}
    except Exception:
        out = {k: None for k in _FIELDS}
        out["text"] = raw.strip()
        return out


async def generate_synastry_reading(name_a: str, name_b: str, syn_data: dict) -> dict:
    """Claude-Deutung aus berechneten Synastrie-Daten (Result-Pattern)."""
    try:
        user_text = _aspects_to_text(name_a or "Person A", name_b or "Person B", syn_data)
        raw = await _call_claude(
            _system(),
            user_text + "\n\nSchreibe nun die Deutung als JSON.",
            max_tokens=2000,
            model=SYNASTRY_MODEL,
        )
        parsed = _parse_json(raw)
        return {"ok": True, "data": {**parsed, "model": SYNASTRY_MODEL}}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
