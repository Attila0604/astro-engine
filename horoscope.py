"""
horoscope.py
Transit-Agent: Tages-, Wochen- und Monatshoroskop.

Grundidee: Das Geburtshoroskop (Radix) ist fix -- es beschreibt deinen Charakter.
Die "Transite" sind die Winkel, die die HEUTIGEN Planetenpositionen zu deinen
GEBURTS-Positionen bilden. Genau daraus entsteht "heute / diese Woche / dieser Monat".

Ablauf:
  1. chart_engine.compute_transits() liefert die aktiven Transit-Aspekte (deterministisch).
  2. Pro Zeitraum werden die passenden Transite gefiltert (s.u.).
  3. Der Transit-Agent (Claude) macht daraus Stimmung + Text + Tipp, auf Deutsch, "du".

Astrologische Logik der Filter:
  - daily   : alle Transite (der schnelle Mond gibt dem Tag seine Farbe)
  - weekly  : ohne Mond (wechselt mehrmals pro Woche -> kein Wochenthema)
  - monthly : nur langsame Laeufer + Sonne (alles Schnelle ist ueber einen Monat Rauschen)

Oeffentliche Funktion (Result-Pattern):
    await generate_horoscope(person, period) -> {"ok": True, "data": {...}}
"""

from __future__ import annotations

import json
import os

import chart_engine as ce
import namen
from agents import PERSONA, _call_claude, _closeness, lektorat, norm_lang, with_lang

# Eigenes Modell -- guenstiger Default fuer das (spaeter taegliche, hochvolumige)
# Horoskop. Per ENV auf z.B. claude-sonnet-4-6 hochschaltbar fuer mehr Waerme.
HOROSCOPE_MODEL = os.environ.get("HOROSCOPE_MODEL", "claude-sonnet-4-6")

PERIODS = {
    "daily": {
        "span": "den heutigen Tag",
        "tip_label": "Tipp des Tages",
        "mood_label": "Stimmung des Tages",
        "max_aspects": 7,
        "length": "3-4 warme Absaetze",
        "arc": "Beschreibe die Energie des Tages von morgens bis abends, wenn es sich anbietet.",
    },
    "weekly": {
        "span": "die kommende Woche",
        "tip_label": "Tipp der Woche",
        "mood_label": "Grundton der Woche",
        "max_aspects": 6,
        "length": "4-5 substanzielle Absaetze",
        "arc": "Gib der Woche einen Bogen: Wochenbeginn, Mitte, Wochenende -- was baut sich auf, was loest sich.",
    },
    "monthly": {
        "span": "den kommenden Monat",
        "tip_label": "Tipp des Monats",
        "mood_label": "Grundton des Monats",
        "max_aspects": 6,
        "length": "5-6 substanzielle Absaetze",
        "arc": "Gliedere den Monat in Phasen (erste Tage, Mitte, letztes Drittel) und zeige die Entwicklung der Themen.",
    },
}

# langsame Laeufer + Sonne -> die einzigen, deren Transite ueber einen Monat tragen
_MONTHLY_KEEP = {"Sun", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"}

# nur echte transitierende Planeten -- Lilith und Mondknoten sind als Transit-
# Ausloeser fuer Horoskope unueblich (Rauschen). Der natale Gegenpunkt darf alles sein.
_TRANSIT_BODIES = {"Sun", "Moon", "Mercury", "Venus", "Mars",
                   "Jupiter", "Saturn", "Uranus", "Neptune", "Pluto"}


def _select_transits(aspects: list, period: str) -> list:
    """Filtert die Transit-Aspekte passend zum Zeitraum (bereits nach Orb sortiert)."""
    pool = [a for a in aspects if a["transit"] in _TRANSIT_BODIES]
    if period == "weekly":
        pool = [a for a in pool if a["transit"] != "Moon"]
    elif period == "monthly":
        pool = [a for a in pool if a["transit"] in _MONTHLY_KEEP]
    return pool[:PERIODS[period]["max_aspects"]]


_SPAN_EN = {"daily": "today", "weekly": "the coming week", "monthly": "the coming month"}


def _transits_to_text_intl(natal: dict, transit_data: dict, selected: list, period: str, lang: str) -> str:
    """en/hu/it: neutrale englische Beschriftung, alle Namen in der Zielsprache."""
    b3 = natal["big_three"]
    z = lambda pt: namen.zeichen(pt.get("sign") or pt.get("sign_de"), lang)  # noqa: E731
    lines = [f"Core axis of the person: {namen.planet('Sun', lang)} in {z(b3['sun'])}, "
             f"{namen.planet('Moon', lang)} in {z(b3['moon'])}, {namen.planet('Ascendant', lang)} in {z(b3['ascendant'])}."]
    if period == "daily":
        moon = next((p for p in transit_data["transit_positions"] if p["name"] == "Moon"), None)
        if moon:
            lines.append(f"Today the {namen.planet('Moon', lang)} is in {z(moon)} (it colours the day).")
    lines.append(f"\nActive transits for {_SPAN_EN[period]} (tightest first):")
    if not selected:
        lines.append("- (no tight transits -- a calm, quiet period without big themes)")
    for a in selected:
        mv = "building up" if a.get("movement") == "Applying" else "fading"
        lines.append(f"- transiting {namen.planet(a.get('transit') or a.get('transit_de'), lang)} "
                     f"{namen.aspekt(a.get('type') or a.get('type_de'), lang)} natal "
                     f"{namen.planet(a.get('natal') or a.get('natal_de'), lang)} ({_closeness(a['orb'], lang)}, {mv})")
    return "\n".join(lines)


def _transits_to_text(natal: dict, transit_data: dict, selected: list, period: str, lang: str = "de") -> str:
    if norm_lang(lang) != "de":
        return _transits_to_text_intl(natal, transit_data, selected, period, norm_lang(lang))
    cfg = PERIODS[period]
    b3 = natal["big_three"]
    lines = [
        f"Kernachse der Person: Sonne in {b3['sun']['sign_de']}, "
        f"Mond in {b3['moon']['sign_de']}, Aszendent in {b3['ascendant']['sign_de']}."
    ]
    if period == "daily":
        moon = next((p for p in transit_data["transit_positions"] if p["name"] == "Moon"), None)
        if moon:
            lines.append(f"Der Mond steht heute in {moon['sign_de']} (gibt dem Tag seine Grundfaerbung).")

    lines.append(f"\nAktive Transite fuer {cfg['span']} (engste zuerst):")
    if not selected:
        lines.append("- (keine engen Transite -- ein ruhiger, unaufgeregter Zeitraum ohne grosse Themen)")
    for a in selected:
        mv = "im Kommen" if a.get("movement") == "Applying" else "klingt ab"
        lines.append(
            f"- Transit-{a['transit_de']} {a['type_de']} deine/n natale/n "
            f"{a['natal_de']} ({_closeness(a['orb'])}, {mv})"
        )
    return "\n".join(lines)


def _system(period: str) -> str:
    cfg = PERIODS[period]
    return (
        f"{PERSONA}\n\n"
        f"Deine Aufgabe: Schreibe ein persoenliches Horoskop fuer {cfg['span']}, "
        "basierend auf den aktiven Transiten (die Bewegung der heutigen Planeten "
        "relativ zum Geburtshoroskop). Deute NICHT den Geburtscharakter, sondern was "
        "in diesem Zeitraum energetisch ansteht. Beziehe dich konkret auf die genannten "
        "Transite, aber uebersetze sie in gelebte Alltagssprache statt Fachjargon. "
        "'Im Kommen' heisst, das Thema verstaerkt sich; 'klingt ab' heisst, es loest sich auf. "
        f"{cfg['arc']} "
        "Bei wenigen oder keinen Transiten ist ein ruhiger, sammelnder Zeitraum voellig in Ordnung "
        "-- erfinde keine Dramatik.\n\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, ohne Markdown, ohne Vorrede, "
        "in genau diesem Format:\n"
        "{\n"
        f'  "stimmung": "<kurze {cfg["mood_label"]}, max 8 Woerter, wie eine Schlagzeile>",\n'
        f'  "text": "<{cfg["length"]}, was in diesem Zeitraum dran ist, in du-Form>",\n'
        f'  "tipp": "<ein konkreter, umsetzbarer {cfg["tip_label"]}, 1-2 Saetze>",\n'
        '  "fokus": "<worauf sich die Person in diesem Zeitraum ausrichten sollte, 2 Saetze>",\n'
        '  "liebe": "<was in Herz, Naehe und Beziehungen ansteht, 2 Saetze>",\n'
        '  "beruf": "<was in Arbeit, Richtung und Tatkraft ansteht, 2 Saetze>",\n'
        '  "ritual": "<ein kleines, konkretes Ritual passend zu den Transiten, 1-2 Saetze>",\n'
        '  "affirmation": "<eine kraftvolle Ich-Affirmation passend zum Zeitraum, 1 Satz>"\n'
        "}"
    )


_FIELDS = ("stimmung", "text", "tipp", "fokus", "liebe", "beruf", "ritual", "affirmation")


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


async def generate_horoscope(person: dict, period: str = "daily", at=None, lang: str = "de") -> dict:
    """Tages-/Wochen-/Monatshoroskop fuer eine Person."""
    period = (period or "daily").lower()
    if period not in PERIODS:
        return {"ok": False,
                "error": f"Unbekannter Zeitraum: {period!r}. Erlaubt: daily, weekly, monthly."}

    natal_res = ce.compute_natal(person)
    if not natal_res["ok"]:
        return natal_res
    tr_res = ce.compute_transits(person, at)
    if not tr_res["ok"]:
        return tr_res

    selected = _select_transits(tr_res["data"]["aspects_to_natal"], period)
    text = _transits_to_text(natal_res["data"], tr_res["data"], selected, period, lang)
    auftrag = ("\n\nSchreibe nun das Horoskop als JSON." if norm_lang(lang) == "de"
               else "\n\nNow write the horoscope as JSON.")

    try:
        raw = await _call_claude(with_lang(_system(period), lang), text + auftrag,
                                 max_tokens=2400, model=HOROSCOPE_MODEL)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # en/hu/it: nur bei deutschen Resten ein kurzes Lektorat (Horoskope sind haeufig -> Kosten)
    parsed = await lektorat(_parse_json(raw), lang, immer=False)
    return {
        "ok": True,
        "data": {
            "period": period,
            "lang": norm_lang(lang),
            "at_utc": tr_res["data"]["meta"]["at_utc"],
            **parsed,
            "model": HOROSCOPE_MODEL,
            "transits_used": [
                {"transit": a["transit_de"], "type": a["type_de"],
                 "natal": a["natal_de"], "orb": a["orb"], "movement": a.get("movement")}
                for a in selected
            ],
        },
    }
