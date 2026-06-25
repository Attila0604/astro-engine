"""
chat.py
Konversations-Layer: Live-Fragen, Nachfragen, Synastrie-Dialoge, Memory.

Der Agent fuehrt ein echtes Gespraech und kann dabei SELBST Werkzeuge aufrufen
(Anthropic Tool-Use), um frische Daten aus der Chart-Engine zu holen:
    - get_transits      -> Timing-Fragen ("ist heute/diese Woche guenstig fuer X")
    - get_synastry      -> "wie passe ich zu <Name>"
    - get_person_chart  -> "was fuer ein Typ ist <Name>"

Das Geburtshoroskop des Nutzers steckt fest im System-Prompt -- Charakterfragen
brauchen also kein Tool. Andere Personen ("Sarah") werden ueber die Liste
`people` aufgeloest (Name -> Geburtsdaten).

Memory: ein kurzer, rollender Zusammenfassungstext aus frueheren Gespraechen
wird in den System-Prompt injiziert. update_memory() erzeugt/aktualisiert ihn.

Oeffentliche Funktionen (Result-Pattern):
    await chat_turn(person, people, history, message, memory)  -> {ok, data:{reply, messages, tools_used}}
    await update_memory(history, memory)                       -> {ok, data:{memory}}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import chart_engine as ce
from agents import PERSONA, _call_claude, _get_client, chart_to_text

CHAT_MODEL = os.environ.get("CHAT_MODEL", "claude-sonnet-4-6")

# ---------------------------------------------------------------------------
# Werkzeuge, die der Agent aufrufen kann
# ---------------------------------------------------------------------------
TOOLS = [
    {
        "name": "get_transits",
        "description": ("Berechnet die aktuellen (oder zu einem Datum) Transite gegen das "
                        "Geburtshoroskop des Nutzers. Nutze dies fuer Timing-Fragen, z.B. "
                        "'ist heute/diese Woche ein guter Zeitpunkt fuer X'."),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string",
                         "description": "ISO-Datum YYYY-MM-DD oder 'today'. Standard: heute."}
            },
        },
    },
    {
        "name": "get_synastry",
        "description": ("Berechnet die Beziehungs-Dynamik (Synastrie) zwischen dem Nutzer und "
                        "einer hinterlegten Person. Nutze dies fuer Fragen wie "
                        "'wie passe ich zu <Name>' oder 'harmoniere ich mit <Name>'."),
        "input_schema": {
            "type": "object",
            "properties": {"person": {"type": "string",
                                      "description": "Name der hinterlegten Person, z.B. 'Sarah'."}},
            "required": ["person"],
        },
    },
    {
        "name": "get_person_chart",
        "description": ("Liefert das Geburtshoroskop einer hinterlegten Person (oder des Nutzers). "
                        "Nutze dies fuer Fragen wie 'was fuer ein Typ ist <Name>'."),
        "input_schema": {
            "type": "object",
            "properties": {"person": {"type": "string",
                                      "description": "Name der Person. 'ich' fuer den Nutzer selbst."}},
            "required": ["person"],
        },
    },
]


def _resolve_named_person(name: str, user_person: dict, people: list):
    n = (name or "").strip().lower()
    if n in ("ich", "me", "self", "ich selbst", (user_person.get("name") or "").lower()):
        return user_person
    for p in people:
        if (p.get("name") or "").strip().lower() == n:
            return p
    return None


def _exec_tool(name: str, args: dict, user_person: dict, people: list) -> dict:
    """Fuehrt einen Tool-Aufruf aus -> Result-dict (wird als JSON an Claude zurueck)."""
    args = args or {}
    if name == "get_transits":
        at = args.get("date")
        if at in (None, "", "today", "heute"):
            at = None
        r = ce.compute_transits(user_person, at)
        if r["ok"]:
            r["data"]["aspects_to_natal"] = r["data"]["aspects_to_natal"][:8]
        return r

    if name == "get_synastry":
        other = _resolve_named_person(args.get("person"), user_person, people)
        if not other:
            return {"ok": False,
                    "error": f"Person {args.get('person')!r} ist nicht hinterlegt. "
                             "Bitte den Nutzer nach ihren Geburtsdaten (Name, Datum, Zeit, Ort) fragen."}
        r = ce.compute_synastry(user_person, other)
        if r["ok"]:
            r["data"]["aspects"] = r["data"]["aspects"][:10]
        return r

    if name == "get_person_chart":
        who = _resolve_named_person(args.get("person"), user_person, people)
        if not who:
            return {"ok": False,
                    "error": f"Person {args.get('person')!r} ist nicht hinterlegt."}
        r = ce.compute_natal(who)
        if r["ok"]:
            return {"ok": True, "data": {"name": who.get("name"),
                                         "chart_summary": chart_to_text(r["data"])}}
        return r

    return {"ok": False, "error": f"Unbekanntes Tool: {name}"}


def _system(user_person: dict, natal_text: str, people: list, memory: str | None) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    known = ", ".join(p.get("name", "?") for p in people) or "(keine)"
    parts = [
        PERSONA,
        "\nDu fuehrst gerade ein lebendiges, persoenliches Gespraech mit dem Nutzer. "
        "Antworte gespraechig und auf den Punkt -- kurze, warme Antworten, kein Essay. "
        "Stelle bei Bedarf eine Rueckfrage.",
        f"\nHeutiges Datum: {today}.",
        f"\nDas Geburtshoroskop des Nutzers ({user_person.get('name')}):\n{natal_text}",
        f"\nHinterlegte weitere Personen (fuer Synastrie/Vergleich): {known}.",
        "\nWenn du frische Daten brauchst, nutze die Werkzeuge: get_transits (Timing), "
        "get_synastry (Beziehung zu einer Person), get_person_chart (Chart einer Person). "
        "Fuer Charakterfragen zum Nutzer brauchst du kein Werkzeug -- sein Chart steht oben. "
        "Wenn nach einer Person gefragt wird, die nicht hinterlegt ist, frag freundlich nach "
        "deren Geburtsdaten.",
    ]
    if memory:
        parts.append(f"\nWas du aus frueheren Gespraechen weisst:\n{memory}")
    return "\n".join(parts)


def _block_to_dict(b) -> dict:
    if b.type == "text":
        return {"type": "text", "text": b.text}
    if b.type == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return {"type": getattr(b, "type", "unknown")}


async def chat_turn(person: dict, people: list, history: list, message: str,
                    memory: str | None = None, max_iters: int = 6) -> dict:
    """Eine Gespraechsrunde mit Tool-Nutzung. Gibt Antwort + bereinigte Historie zurueck."""
    natal_res = ce.compute_natal(person)
    if not natal_res["ok"]:
        return natal_res
    natal_text = chart_to_text(natal_res["data"])

    try:
        client = _get_client()
        system = _system(person, natal_text, people, memory)
        # bereinigte Eingangshistorie (nur text-Turns) + neue Nutzer-Nachricht
        working = [{"role": m["role"], "content": m["content"]} for m in history]
        working.append({"role": "user", "content": message})

        tools_used = []
        for _ in range(max_iters):
            resp = await client.messages.create(
                model=CHAT_MODEL, max_tokens=1500, system=system,
                tools=TOOLS, messages=working,
            )
            working.append({"role": "assistant",
                            "content": [_block_to_dict(b) for b in resp.content]})

            if resp.stop_reason == "tool_use":
                results = []
                for b in resp.content:
                    if b.type == "tool_use":
                        out = _exec_tool(b.name, b.input, person, people)
                        tools_used.append({"name": b.name, "input": b.input})
                        results.append({"type": "tool_result", "tool_use_id": b.id,
                                        "content": json.dumps(out, ensure_ascii=False)})
                working.append({"role": "user", "content": results})
                continue

            reply = "".join(b.text for b in resp.content if b.type == "text").strip()
            clean = ([{"role": m["role"], "content": m["content"]} for m in history]
                     + [{"role": "user", "content": message},
                        {"role": "assistant", "content": reply}])
            return {"ok": True, "data": {"reply": reply, "messages": clean,
                                         "tools_used": tools_used}}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    return {"ok": False, "error": "Zu viele Tool-Schritte ohne Antwort."}


def _render_transcript(history: list, message: str | None = None) -> str:
    lines = []
    for m in history:
        who = "Nutzer" if m.get("role") == "user" else "App"
        lines.append(f"{who}: {m.get('content','')}")
    if message:
        lines.append(f"Nutzer: {message}")
    return "\n".join(lines)


async def update_memory(history: list, memory: str | None = None) -> dict:
    """Erzeugt/aktualisiert die rollende Memory aus dem Gespraech (ein Claude-Call)."""
    system = (
        "Du fasst Gespraeche zwischen einem Nutzer und einer Astrologie-App zusammen, "
        "um dir Wichtiges fuer kuenftige Gespraeche zu merken. Extrahiere nur DAUERHAFTE "
        "Fakten und wiederkehrende Themen: genannte Personen (Partner, Freunde, Familie) "
        "mit Bezug, Lebensthemen, Anliegen, Praeferenzen. Keine Tagesdetails, kein Smalltalk. "
        "Max 8 kurze Stichpunkte. Wenn schon eine Memory existiert, fuege Neues hinzu und "
        "halte sie kompakt."
    )
    user = (f"Bisherige Memory:\n{memory or '(noch keine)'}\n\n"
            f"Gespraech:\n{_render_transcript(history)}\n\n"
            "Gib die aktualisierte Memory als kurze Stichpunktliste zurueck.")
    try:
        text = await _call_claude(system, user, max_tokens=500, model=CHAT_MODEL)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "data": {"memory": text.strip()}}
