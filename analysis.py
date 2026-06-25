"""
analysis.py
Orchestrator fuer die "komplett Analyse".

Ablauf:
  1. Chart-Engine rechnet das Geburtshoroskop (deterministisch).
  2. Die vier Domaenen-Agenten laufen PARALLEL ueber das Chart.
  3. Der Synthese-Schritt webt die vier Abschnitte zu einem zusammenhaengenden,
     warmen Tiefen-Reading auf Deutsch ("du").

Oeffentliche Funktion (Result-Pattern):
    await generate_full_analysis(person) -> {"ok": True, "data": {...}}
"""

from __future__ import annotations

import asyncio

import chart_engine as ce
from agents import (
    DOMAINS,
    PERSONA,
    ANALYSIS_MODEL,
    _call_claude,
    _domain_system,
    _domain_user,
    chart_to_text,
)


async def _run_domain_agents(chart_text: str) -> dict:
    """Alle Domaenen-Agenten gleichzeitig -- spart Wartezeit."""
    tasks = [
        _call_claude(_domain_system(d), _domain_user(chart_text), max_tokens=1200)
        for d in DOMAINS
    ]
    texts = await asyncio.gather(*tasks)
    return {d["key"]: {"title": d["title"], "text": t} for d, t in zip(DOMAINS, texts)}


def _synthesis_system() -> str:
    return (
        f"{PERSONA}\n\n"
        "Deine Aufgabe: Du erhaeltst vier fertige Deutungs-Abschnitte (Persoenlichkeit, "
        "Liebe, Karriere, Wachstum) und die Chart-Daten. Webe daraus EIN zusammenhaengendes, "
        "ausfuehrliches Tiefen-Reading.\n\n"
        "So baust du es auf:\n"
        "- Eine warme, persoenliche Einleitung (2-3 Absaetze), die das Gesamtbild aus Sonne, "
        "Mond und Aszendent zeichnet und neugierig macht.\n"
        "- Danach die vier Themen mit Markdown-Ueberschriften (## Persoenlichkeit & Wesenskern, "
        "## Liebe & Beziehung, ## Karriere & Berufung, ## Wachstum & Lebensthema).\n"
        "- Ein kurzer, ermutigender Abschluss (## Dein roter Faden), der die wichtigsten "
        "Spannungslinien und Staerken zu einem stimmigen Ganzen verbindet.\n\n"
        "Wichtig: Behalte die inhaltliche Tiefe der Abschnitte, aber glaette Uebergaenge und "
        "streiche Wiederholungen zwischen den Abschnitten. Sorge fuer einen durchgehenden, "
        "warmen Ton. Erfinde nichts dazu."
    )


def _synthesis_user(chart_text: str, sections: dict) -> str:
    parts = [f"### {s['title']}\n{s['text']}" for s in sections.values()]
    return (
        "CHART-DATEN:\n"
        f"{chart_text}\n\n"
        "VIER DEUTUNGS-ABSCHNITTE (Rohmaterial):\n\n"
        + "\n\n".join(parts)
        + "\n\nWebe daraus jetzt das vollstaendige, zusammenhaengende Tiefen-Reading."
    )


async def generate_full_analysis(person: dict) -> dict:
    """Komplette Tiefen-Analyse fuer eine Person."""
    natal_res = ce.compute_natal(person)
    if not natal_res["ok"]:
        return natal_res
    natal = natal_res["data"]

    try:
        chart_text = chart_to_text(natal)
        sections = await _run_domain_agents(chart_text)
        reading = await _call_claude(
            _synthesis_system(),
            _synthesis_user(chart_text, sections),
            max_tokens=6000,
        )
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {
        "ok": True,
        "data": {
            "meta": natal["meta"],
            "big_three": natal["big_three"],
            "model": ANALYSIS_MODEL,
            "sections": sections,   # die vier Einzel-Abschnitte
            "reading": reading,     # das fertige, zusammenhaengende Reading
        },
    }
