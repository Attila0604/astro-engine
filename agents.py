"""
agents.py
LLM-Layer der Horoskop-App: Domaenen-Agenten + Claude-Anbindung.

Jeder Agent bekommt denselben warmen deutschen Persona-Prompt plus einen
fokussierten Auftrag fuer seine Domaene (Persoenlichkeit, Liebe, Karriere,
Wachstum) und nur die lesbar aufbereiteten Chart-Daten. Der LLM-Call ist
hinter _call_claude gekapselt -> Modellwechsel = eine Zeile.
"""

from __future__ import annotations

import os
from typing import Optional

from anthropic import AsyncAnthropic

# Modell fuer die (einmalige, teure) Tiefen-Analyse. Per ENV ueberschreibbar.
ANALYSIS_MODEL = os.environ.get("ANALYSIS_MODEL", "claude-sonnet-4-6")

# ---------------------------------------------------------------------------
# Claude-Anbindung (lazy -- kein Crash beim Start, wenn der Key fehlt)
# ---------------------------------------------------------------------------
_client: Optional[AsyncAnthropic] = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY ist nicht gesetzt -- in Railway unter Variables hinterlegen."
            )
        _client = AsyncAnthropic(api_key=key)
    return _client


async def _call_claude(system: str, user: str, max_tokens: int,
                       model: str = ANALYSIS_MODEL) -> str:
    """Ein Claude-Aufruf -> reiner Text."""
    resp = await _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# Gemeinsame Persona
# ---------------------------------------------------------------------------
PERSONA = """Du bist eine erfahrene, warmherzige Astrologin, die auf Deutsch schreibt.
Du sprichst die Person durchgehend mit "du" an -- nahbar, persoenlich, auf Augenhoehe.

Dein Stil:
- konkret und auf die tatsaechlichen Stellungen bezogen, nie generisch oder beliebig
- psychologisch geerdet: du beschreibst Anlagen, Spannungen und Staerken als Angebot zur Selbstreflexion
- ermutigend und ehrlich zugleich -- du benennst auch Herausforderungen, aber nie fatalistisch oder angstmachend
- fliessende Prosa in Absaetzen, keine Stichpunkt-Listen, keine Emojis

Wichtige Grenzen:
- Du bist keine Wahrsagerin. Du sagst keine fixen Ereignisse voraus, sondern beschreibst Tendenzen und Moeglichkeiten.
- Keine medizinischen, finanziellen, rechtlichen oder gesundheitlichen Ratschlaege oder Diagnosen.
- Astrologie ist hier ein Werkzeug zur Selbstreflexion und Unterhaltung, kein Schicksalsspruch.

Erfinde niemals Planetenstellungen -- nutze ausschliesslich die dir gegebenen Daten."""

# ---------------------------------------------------------------------------
# Domaenen-Agenten
# ---------------------------------------------------------------------------
DOMAINS = [
    {
        "key": "persoenlichkeit",
        "title": "Persoenlichkeit & Wesenskern",
        "focus": (
            "Deute den Kern der Persoenlichkeit. Im Zentrum stehen Sonne (Wesenskern, "
            "Antrieb), Mond (Gefuehlswelt, Beduerfnis nach Sicherheit) und Aszendent "
            "(Aussenwirkung, erster Eindruck). Beziehe Merkur (Denken, Kommunikation) und "
            "die Verteilung der Elemente und Qualitaeten ein. Arbeite die wichtigsten "
            "Aspekte zwischen den persoenlichen Planeten heraus -- besonders die engen."
        ),
    },
    {
        "key": "liebe",
        "title": "Liebe & Beziehung",
        "focus": (
            "Deute das Beziehungsthema. Im Zentrum stehen Venus (wie du liebst, was du "
            "anziehend findest, deine Werte in Naehe) und Mars (Begehren, Energie, wie du "
            "auf andere zugehst). Beziehe das 5. Haus (Romantik, Spiel) und 7. Haus "
            "(Partnerschaft, Begegnung auf Augenhoehe) sowie Aspekte zu Venus und Mars ein."
        ),
    },
    {
        "key": "karriere",
        "title": "Karriere & Berufung",
        "focus": (
            "Deute Berufung und Wirken in der Welt. Im Zentrum stehen MC und 10. Haus "
            "(oeffentliche Rolle, Berufung), Saturn (Struktur, Verantwortung, Meisterschaft) "
            "und Jupiter (Wachstum, Chancen, Sinn). Beziehe das 2. Haus (Werte, Ressourcen) "
            "und 6. Haus (Arbeit, Alltag) ein. Formuliere es als Potenzial, nicht als "
            "konkrete Berufsempfehlung."
        ),
    },
    {
        "key": "wachstum",
        "title": "Wachstum & Lebensthema",
        "focus": (
            "Deute den roten Faden der persoenlichen Entwicklung. Im Zentrum stehen die "
            "Mondknoten (Lebensrichtung, woher du kommst und wohin es dich zieht), Saturn "
            "(Reifung, Lektionen) und Chiron (wunde Stelle und Heilungsgabe). Beziehe die "
            "aeusseren Planeten und Pluto (Wandlung, tiefe Themen) ein."
        ),
    },
]


def _domain_system(domain: dict) -> str:
    return (
        f"{PERSONA}\n\n"
        f"Deine Aufgabe in diesem Abschnitt: {domain['title']}.\n{domain['focus']}\n\n"
        "Schreibe 3-5 dichte, fliessende Absaetze. Keine Ueberschrift, keine Einleitung "
        'wie "In diesem Abschnitt" -- steige direkt in die Deutung ein.'
    )


def _domain_user(chart_text: str) -> str:
    return (
        "Hier ist das Geburtshoroskop als Datengrundlage:\n\n"
        f"{chart_text}\n\n"
        "Schreibe nun deinen Abschnitt auf Basis dieser Daten."
    )


# ---------------------------------------------------------------------------
# Chart -> lesbarer deutscher Text (Datengrundlage fuer die Agenten)
# ---------------------------------------------------------------------------
_ANGLE_NAMES = {"Ascendant", "Medium_Coeli", "Descendant", "Imum_Coeli"}


def _closeness(orb: float) -> str:
    if orb < 1:
        return "sehr eng"
    if orb < 3:
        return "eng"
    if orb < 6:
        return "mittel"
    return "weit"


def chart_to_text(natal: dict, max_aspects: int = 12) -> str:
    """Wandelt das Natal-JSON in eine kompakte, lesbare Beschreibung."""
    b3 = natal["big_three"]
    lines = ["KERNACHSE:"]
    lines.append(f"- Sonne in {b3['sun']['sign_de']} {b3['sun']['degree']}deg "
                 f"(Haus {b3['sun']['house']}) -- Wesenskern")
    lines.append(f"- Mond in {b3['moon']['sign_de']} {b3['moon']['degree']}deg "
                 f"(Haus {b3['moon']['house']}) -- Gefuehlswelt")
    lines.append(f"- Aszendent in {b3['ascendant']['sign_de']} "
                 f"{b3['ascendant']['degree']}deg -- Aussenwirkung")

    lines.append("\nPLANETEN & PUNKTE:")
    for p in natal["points"]:
        if p["name"] in {"Sun", "Moon", "Ascendant"}:
            continue
        rng = ", rueckläufig" if p.get("retrograde") else ""
        haus = f", Haus {p['house']}" if p.get("house") else ""
        lines.append(f"- {p['name_de']} in {p['sign_de']} {p['degree']}deg{haus}{rng}")

    dist = natal["distributions"]
    el = ", ".join(f"{k} {v}" for k, v in dist["elements"].items())
    qu = ", ".join(f"{k} {v}" for k, v in dist["qualities"].items())
    lines.append(f"\nELEMENTE: {el}")
    lines.append(f"QUALITAETEN: {qu}")

    if natal.get("lunar_phase"):
        lines.append(f"MONDPHASE: {natal['lunar_phase']['name']}")

    lines.append("\nWICHTIGSTE ASPEKTE (engste zuerst):")
    for a in natal["aspects"][:max_aspects]:
        lines.append(f"- {a['p1_de']} {a['type_de']} {a['p2_de']} "
                     f"({_closeness(a['orb'])}, Orb {a['orb']}deg)")

    if not natal["meta"].get("time_known", True):
        lines.append("\nHINWEIS: Geburtszeit unbekannt -- Aszendent und Haeuser sind "
                     "ungenau und sollten nur vorsichtig gedeutet werden.")

    return "\n".join(lines)
