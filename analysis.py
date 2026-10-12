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
    PERSONA_EN,
    ANALYSIS_MODEL,
    _call_claude,
    _domain_system,
    _domain_user,
    chart_to_text,
    lektorat,
    norm_lang,
    titel,
    with_lang,
)


async def _run_domain_agents(chart_text: str, lang: str = "de") -> dict:
    """Alle Domaenen-Agenten gleichzeitig -- spart Wartezeit."""
    tasks = [
        _call_claude(with_lang(_domain_system(d, lang), lang), _domain_user(chart_text, lang), max_tokens=1200)
        for d in DOMAINS
    ]
    texts = await asyncio.gather(*tasks)
    return {d["key"]: {"title": titel(d["key"], lang), "text": t} for d, t in zip(DOMAINS, texts)}


def _synthesis_system(lang: str = "de") -> str:
    if norm_lang(lang) != "de":
        def k(key):
            return titel(key, lang)
        return (
            f"{PERSONA_EN}\n\n"
            "Your task: you receive four finished interpretation sections (personality, love, career, growth) "
            "and the chart data. Weave them into ONE coherent, detailed in-depth reading.\n\n"
            "Structure:\n"
            "- A warm, personal introduction (2-3 paragraphs) that paints the overall picture from Sun, Moon "
            "and Ascendant and sparks curiosity.\n"
            f"- Then the four themes with markdown headings, exactly: ## {k('persoenlichkeit')}, "
            f"## {k('liebe')}, ## {k('karriere')}, ## {k('wachstum')}.\n"
            f"- A short, encouraging closing (## {k('roter_faden')}) that ties the main tensions and strengths "
            "into a coherent whole.\n\n"
            "Important: keep the depth of the sections, but smooth the transitions and remove repetitions "
            "between sections. Keep a continuous, warm tone. Invent nothing. This is also your chance to "
            "polish the language: any clumsy or literally translated phrase in the sections must come out "
            "natural and idiomatic in your version."
        )
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


def _synthesis_user(chart_text: str, sections: dict, lang: str = "de") -> str:
    parts = [f"### {s['title']}\n{s['text']}" for s in sections.values()]
    if norm_lang(lang) != "de":
        return ("CHART DATA:\n"
                f"{chart_text}\n\n"
                "FOUR INTERPRETATION SECTIONS (raw material):\n\n"
                + "\n\n".join(parts)
                + "\n\nNow weave the complete, coherent in-depth reading.")
    return (
        "CHART-DATEN:\n"
        f"{chart_text}\n\n"
        "VIER DEUTUNGS-ABSCHNITTE (Rohmaterial):\n\n"
        + "\n\n".join(parts)
        + "\n\nWebe daraus jetzt das vollstaendige, zusammenhaengende Tiefen-Reading."
    )


async def generate_full_analysis(person: dict, lang: str = "de", fortschritt=None) -> dict:
    """Komplette Tiefen-Analyse fuer eine Person.
    fortschritt(phase): optionaler Rueckruf fuer die Fortschrittsanzeige der App."""
    def phase(name: str) -> None:
        if fortschritt:
            fortschritt(name)

    phase("chart")
    natal_res = ce.compute_natal(person)
    if not natal_res["ok"]:
        return natal_res
    natal = natal_res["data"]

    try:
        chart_text = chart_to_text(natal, lang=lang)
        phase("bereiche")
        sections = await _run_domain_agents(chart_text, lang)
        phase("verweben")
        reading = await _call_claude(
            with_lang(_synthesis_system(lang), lang),
            _synthesis_user(chart_text, sections, lang),
            max_tokens=6000,
        )
        # en/hu/it: Sprach-Lektorat (kurze Korrekturliste) + deutsche Namen absichern
        if norm_lang(lang) != "de":
            phase("pruefen")
        reading = (await lektorat({"reading": reading}, lang, immer=True))["reading"]
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
