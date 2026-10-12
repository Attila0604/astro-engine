"""
agents.py
LLM-Layer der Horoskop-App: Domaenen-Agenten + Claude-Anbindung.

Jeder Agent bekommt denselben warmen deutschen Persona-Prompt plus einen
fokussierten Auftrag fuer seine Domaene (Persoenlichkeit, Liebe, Karriere,
Wachstum) und nur die lesbar aufbereiteten Chart-Daten. Der LLM-Call ist
hinter _call_claude gekapselt -> Modellwechsel = eine Zeile.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from anthropic import AsyncAnthropic

import namen

# Modell fuer die (einmalige) Tiefen-Analyse. Per ENV ueberschreibbar.
# Claude Sonnet 5.5 schreibt deutlich natuerlicheres Ungarisch/Italienisch als 4.6.
ANALYSIS_MODEL = os.environ.get("ANALYSIS_MODEL", "claude-sonnet-5-5")
# Sprach-Lektorat fuer lange Texte in en/hu/it (liefert nur eine kurze Korrekturliste)
LEKTORAT_MODEL = os.environ.get("LEKTORAT_MODEL", "claude-sonnet-5-5")

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


def _modell_optionen(model: str, max_tokens: int) -> dict:
    """Neuere Modelle (Sonnet 5.5 u. a.) denken standardmaessig mit, und das Denken zaehlt
    zu max_tokens. Damit kein Text abgeschnitten wird: kurzes Denken (effort low) und
    zusaetzlicher Platz. Aeltere Modelle bekommen die Anfrage unveraendert."""
    m = (model or "").lower()
    neu = any(m.startswith(p) for p in ("claude-sonnet-5", "claude-opus-5", "claude-fable-5", "claude-haiku-5"))
    if not neu:
        return {"max_tokens": max_tokens}
    return {"max_tokens": max_tokens + 3000,
            "extra_body": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}}


async def _call_claude(system: str, user: str, max_tokens: int,
                       model: str = ANALYSIS_MODEL) -> str:
    """Ein Claude-Aufruf -> reiner Text (Denk-Bloecke werden ignoriert)."""
    resp = await _get_client().messages.create(
        model=model,
        system=system,
        messages=[{"role": "user", "content": user}],
        **_modell_optionen(model, max_tokens),
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


# ---------------------------------------------------------------------------
# Gemeinsame Persona
# ---------------------------------------------------------------------------
PERSONA = """Du bist eine erfahrene, warmherzige Astrologin.
Du sprichst die Person durchgehend informell an (auf Deutsch "du") -- nahbar, persoenlich, auf Augenhoehe.
In welcher Sprache du schreibst, steht in der SPRACHE-Anweisung am Ende.

Dein Stil:
- konkret und auf die tatsaechlichen Stellungen bezogen, nie generisch oder beliebig
- psychologisch geerdet: du beschreibst Anlagen, Spannungen und Staerken als Angebot zur Selbstreflexion
- ermutigend und ehrlich zugleich -- du benennst auch Herausforderungen, aber nie fatalistisch oder angstmachend
- fliessende Prosa in Absaetzen, keine Stichpunkt-Listen, keine Emojis
- reiner Fliesstext ohne Markdown: keine Sternchen fuer fett oder kursiv, keine Rauten fuer Ueberschriften, keine Backticks. Wenn du etwas betonen willst, mach das ueber die Wortwahl.

Wichtige Grenzen:
- Du bist keine Wahrsagerin. Du sagst keine fixen Ereignisse voraus, sondern beschreibst Tendenzen und Moeglichkeiten.
- Keine medizinischen, finanziellen, rechtlichen oder gesundheitlichen Ratschlaege oder Diagnosen.
- Astrologie ist hier ein Werkzeug zur Selbstreflexion und Unterhaltung, kein Schicksalsspruch.

Erfinde niemals Planetenstellungen -- nutze ausschliesslich die dir gegebenen Daten."""

# ---------------------------------------------------------------------------
# Sprachen: die App gibt lang = de | en | hu | it mit. Die Anweisung kommt ans
# Ende jedes System-Prompts und hat damit Vorrang.
# ---------------------------------------------------------------------------
LANGS = ("de", "en", "hu", "it")

LANG_DIRECTIVES = {
    "de": ("\n\nSPRACHE: Schreibe die gesamte Antwort auf Deutsch und sprich die Person mit \"du\" an. "
           "JSON-Schluessel bleiben exakt wie vorgegeben."),
    "en": ("\n\nLANGUAGE: Write your ENTIRE answer in natural, warm English and address the person "
           "informally as \"you\". Use the usual English astrological terms (e.g. Leo, Moon, Ascendant, "
           "trine, square). Translate headings too. Keep any JSON keys exactly as specified."),
    "hu": ("\n\nNYELV: A TELJES választ természetes, meleg magyar nyelven írd, tegeződve. "
           "Az asztrológiai fogalmakat a megszokott magyar nevükön használd (pl. Oroszlán, Hold, "
           "Aszcendens, trigon, kvadrát). A címsorokat is fordítsd le. A JSON-kulcsokat pontosan "
           "a megadott formában hagyd."),
    "it": ("\n\nLINGUA: Scrivi TUTTA la risposta in un italiano naturale e caloroso, dando del tu. "
           "Usa i nomi italiani consueti dei termini astrologici (es. Leone, Luna, Ascendente, "
           "trigono, quadratura). Traduci anche i titoli. Lascia le chiavi JSON esattamente come indicate."),
}

# Zusatz fuer en/hu/it: wie eine muttersprachliche Astrologin schreiben, nicht uebersetzen.
# Ein Teil der Anweisungen und Daten ist deutsch -- das darf man dem Text nicht anmerken.
NATIV_REGEL = (
    "\nSTYLE ({name}): Write as a native {name}-speaking astrologer would write for a {name} reader. "
    "Do not translate sentence by sentence from the German instructions or data; think and phrase "
    "directly in {name}. Never use German words or German names of signs and planets (e.g. never "
    "\"Jungfrau\", \"Waage\", \"Sonne\") -- always the {name} names from the glossary, with correct "
    "grammar and endings. Avoid clumsy repetitions of the same word stem in one phrase "
    "(like \"viszony viszonos\"); prefer idiomatic, everyday wording over literal calques."
)
SPRACHNAMEN = {"en": "English", "hu": "Hungarian", "it": "Italian"}

# Hilfe in Krisen -- passend zur Sprache bzw. den wahrscheinlichen Laendern
CRISIS_LINES = {
    "de": ("in Oesterreich Telefonseelsorge 142 (rund um die Uhr, kostenlos), in Deutschland "
           "Telefonseelsorge 0800 111 0 111, in der Schweiz Die Dargebotene Hand 143, "
           "bei akuter Gefahr den Notruf 112"),
    "en": ("in the UK and Ireland Samaritans 116 123, in the US the 988 Suicide & Crisis Lifeline, "
           "in an emergency the local emergency number (112 in Europe, 999 in the UK, 911 in the US)"),
    "hu": ("Magyarországon a Lelki Elsősegély Telefonszolgálat 116 123 (ingyenes, éjjel-nappal), "
           "közvetlen veszély esetén a 112-es segélyhívó"),
    "it": ("in Italia Telefono Amico 02 2327 2327 oppure Samaritans Onlus 06 77208977, "
           "in caso di pericolo immediato il numero di emergenza 112"),
}


def norm_lang(lang) -> str:
    lang = (lang or "de").lower()[:2]
    return lang if lang in LANGS else "de"


def with_lang(system: str, lang) -> str:
    lang = norm_lang(lang)
    if lang == "de":
        return system + LANG_DIRECTIVES["de"]
    return (system + LANG_DIRECTIVES[lang] + NATIV_REGEL.format(name=SPRACHNAMEN[lang])
            + namen.glossar(lang))


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


# Fuer en/hu/it: Anleitung neutral auf Englisch (nicht deutsch -> keine Lehnuebersetzungen),
# Ueberschriften fertig in der Zielsprache.
PERSONA_EN = """You are an experienced, warm-hearted astrologer.
You address the person informally and personally, as an equal.
The language you write in is given in the LANGUAGE instruction at the end.

Your style:
- concrete and tied to the actual placements, never generic
- psychologically grounded: you describe tendencies, tensions and strengths as an invitation to self-reflection
- encouraging and honest at once -- you name challenges too, but never fatalistic or frightening
- flowing prose in paragraphs, no bullet lists, no emojis
- plain text without markdown emphasis: no asterisks for bold or italics, no backticks. Emphasise through word choice.

Important limits:
- You are not a fortune teller. You do not predict fixed events; you describe tendencies and possibilities.
- No medical, financial, legal or health advice or diagnoses.
- Astrology here is a tool for self-reflection and entertainment, not a verdict of fate.

Never invent placements -- use only the data you are given."""

TITEL = {
    "persoenlichkeit": {"de": "Persoenlichkeit & Wesenskern", "en": "Personality & Core Self",
                        "hu": "Személyiség és belső lényeg", "it": "Personalità e nucleo interiore"},
    "liebe": {"de": "Liebe & Beziehung", "en": "Love & Relationships",
              "hu": "Szerelem és kapcsolatok", "it": "Amore e relazioni"},
    "karriere": {"de": "Karriere & Berufung", "en": "Career & Calling",
                 "hu": "Karrier és hivatás", "it": "Carriera e vocazione"},
    "wachstum": {"de": "Wachstum & Lebensthema", "en": "Growth & Life Theme",
                 "hu": "Fejlődés és életfeladat", "it": "Crescita e tema di vita"},
    "roter_faden": {"de": "Dein roter Faden", "en": "Your Common Thread",
                    "hu": "A vezérfonalad", "it": "Il tuo filo conduttore"},
}

FOKUS_EN = {
    "persoenlichkeit": ("Interpret the core of the personality. Centre on the Sun (core self, drive), the Moon "
                        "(emotional world, need for security) and the Ascendant (outer impression). Include "
                        "Mercury (thinking, communication) and the balance of elements and modalities. Bring out "
                        "the most important aspects between the personal planets -- especially the tight ones."),
    "liebe": ("Interpret love and relationships. Centre on Venus (how you love, what attracts you, your values in "
              "closeness) and Mars (desire, energy, how you approach others). Include the 5th house (romance, play), "
              "the 7th house (partnership, meeting as equals) and aspects to Venus and Mars."),
    "karriere": ("Interpret calling and impact in the world. Centre on the MC and 10th house (public role, calling), "
                 "Saturn (structure, responsibility, mastery) and Jupiter (growth, opportunities, meaning). Include "
                 "the 2nd house (values, resources) and 6th house (work, daily life). Phrase it as potential, not as "
                 "a concrete job recommendation."),
    "wachstum": ("Interpret the common thread of personal development. Centre on the lunar nodes (life direction, "
                 "where you come from and where you are drawn), Saturn (maturing, lessons) and Chiron (wound and "
                 "healing gift). Include the outer planets and Pluto (transformation, deep themes)."),
}


def titel(key: str, lang) -> str:
    return TITEL.get(key, {}).get(norm_lang(lang)) or TITEL.get(key, {}).get("de", key)


def _domain_system(domain: dict, lang: str = "de") -> str:
    if norm_lang(lang) != "de":
        return (
            f"{PERSONA_EN}\n\n"
            f"Your task in this section: {titel(domain['key'], lang)}.\n{FOKUS_EN[domain['key']]}\n\n"
            "Write 3-5 dense, flowing paragraphs. No heading, no introduction like "
            '"In this section" -- go straight into the interpretation.'
        )
    return (
        f"{PERSONA}\n\n"
        f"Deine Aufgabe in diesem Abschnitt: {domain['title']}.\n{domain['focus']}\n\n"
        "Schreibe 3-5 dichte, fliessende Absaetze. Keine Ueberschrift, keine Einleitung "
        'wie "In diesem Abschnitt" -- steige direkt in die Deutung ein.'
    )


def _domain_user(chart_text: str, lang: str = "de") -> str:
    if norm_lang(lang) != "de":
        return ("Here is the birth chart as your data basis:\n\n"
                f"{chart_text}\n\n"
                "Now write your section based on this data.")
    return (
        "Hier ist das Geburtshoroskop als Datengrundlage:\n\n"
        f"{chart_text}\n\n"
        "Schreibe nun deinen Abschnitt auf Basis dieser Daten."
    )


# ---------------------------------------------------------------------------
# Chart -> lesbarer deutscher Text (Datengrundlage fuer die Agenten)
# ---------------------------------------------------------------------------
_ANGLE_NAMES = {"Ascendant", "Medium_Coeli", "Descendant", "Imum_Coeli"}


def _closeness(orb: float, lang: str = "de") -> str:
    stufe = 0 if orb < 1 else 1 if orb < 3 else 2 if orb < 6 else 3
    if norm_lang(lang) == "de":
        return ("sehr eng", "eng", "mittel", "weit")[stufe]
    return ("very tight", "tight", "medium", "wide")[stufe]


def chart_to_text(natal: dict, max_aspects: int = 12, lang: str = "de") -> str:
    """Wandelt das Natal-JSON in eine kompakte, lesbare Beschreibung.
    Fuer en/hu/it: Beschriftung neutral englisch, alle Namen in der Zielsprache,
    damit Claude keine deutschen Namen ("Jungfrau") in den Text uebernimmt."""
    if norm_lang(lang) != "de":
        return _chart_to_text_intl(natal, max_aspects, norm_lang(lang))
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


def _chart_to_text_intl(natal: dict, max_aspects: int, lang: str) -> str:
    b3 = natal["big_three"]
    z = lambda pt: namen.zeichen(pt.get("sign") or pt.get("sign_de"), lang)  # noqa: E731
    lines = ["CORE AXIS:",
             f"- {namen.planet('Sun', lang)} in {z(b3['sun'])} {b3['sun']['degree']}deg "
             f"(house {b3['sun']['house']}) -- core self",
             f"- {namen.planet('Moon', lang)} in {z(b3['moon'])} {b3['moon']['degree']}deg "
             f"(house {b3['moon']['house']}) -- emotional world",
             f"- {namen.planet('Ascendant', lang)} in {z(b3['ascendant'])} "
             f"{b3['ascendant']['degree']}deg -- outer impression",
             "\nPLANETS & POINTS:"]
    for p in natal["points"]:
        if p["name"] in {"Sun", "Moon", "Ascendant"}:
            continue
        rng = ", retrograde" if p.get("retrograde") else ""
        haus = f", house {p['house']}" if p.get("house") else ""
        lines.append(f"- {namen.planet(p['name'], lang)} in {z(p)} {p['degree']}deg{haus}{rng}")
    dist = natal["distributions"]
    lines.append("\nELEMENTS: " + ", ".join(f"{namen.element(k, lang)} {v}" for k, v in dist["elements"].items()))
    lines.append("MODALITIES: " + ", ".join(f"{namen.qualitaet(k, lang)} {v}" for k, v in dist["qualities"].items()))
    if natal.get("lunar_phase"):
        lines.append(f"LUNAR PHASE: {natal['lunar_phase']['name']}")
    lines.append("\nKEY ASPECTS (tightest first):")
    for a in natal["aspects"][:max_aspects]:
        lines.append(f"- {namen.planet(a['p1'], lang)} {namen.aspekt(a['type'], lang)} {namen.planet(a['p2'], lang)} "
                     f"({_closeness(a['orb'], lang)}, orb {a['orb']}deg)")
    if not natal["meta"].get("time_known", True):
        lines.append("\nNOTE: birth time unknown -- ascendant and houses are uncertain and "
                     "should only be interpreted cautiously.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Sprach-Lektorat fuer lange Texte (en/hu/it)
# Statt den ganzen Text neu zu schreiben (langsam), liefert Claude nur eine kurze
# Liste "alt -> neu". Danach letzte Sicherung: deutsche Namen ersetzen.
# ---------------------------------------------------------------------------
def _lektorat_system(lang: str) -> str:
    name = SPRACHNAMEN[lang]
    return (
        f"You are a meticulous native {name} copy editor for an astrology app. You receive a {name} text "
        "that was written by an AI. Find ONLY real problems: German words or German names left in the text "
        "(e.g. \"Jungfrau\" instead of the proper sign name), grammar or suffix errors, and phrases a native "
        "speaker would find clumsy, unnatural or nonsensical (e.g. literal translations, odd repetitions like "
        "\"viszony viszonos\"). Do not change content, tone, headings or style that is already fine."
        + namen.glossar(lang) +
        "\n\nAnswer ONLY with JSON, no preface: {\"korrekturen\": [{\"alt\": \"<exact passage copied from the "
        "text, 2-15 words>\", \"neu\": \"<corrected passage>\"}]}. At most 25 corrections; an empty list if "
        "the text is fine. \"alt\" must match the text exactly, character for character."
    )


def _korrekturen_anwenden(texte: dict, korrekturen: list) -> tuple[dict, int]:
    n = 0
    for k in korrekturen[:25]:
        alt, neu = (k or {}).get("alt"), (k or {}).get("neu")
        if not isinstance(alt, str) or not isinstance(neu, str) or len(alt.strip()) < 3 or alt == neu:
            continue
        if len(neu) > 3 * len(alt) + 40:   # kein Umschreiben ganzer Passagen
            continue
        for feld, wert in texte.items():
            if isinstance(wert, str) and alt in wert:
                texte[feld] = wert.replace(alt, neu)
                n += 1
    return texte, n


async def lektorat(texte: dict, lang, immer: bool = True) -> dict:
    """Glaettet mehrere Textfelder (z. B. Analyse, Synastrie) in en/hu/it.
    immer=False: nur, wenn deutsche Reste gefunden wurden. Fehler -> Texte unveraendert."""
    lang = norm_lang(lang)
    if lang == "de":
        return texte
    texte = dict(texte)
    gesamt = "\n\n".join(v for v in texte.values() if isinstance(v, str) and v)
    reste = namen.deutsche_reste(gesamt, lang)
    if gesamt and (immer or reste):
        hinweis = (f"\n\nGerman leftovers detected (must be fixed): {', '.join(reste)}" if reste else "")
        try:
            raw = await _call_claude(_lektorat_system(lang), gesamt + hinweis, max_tokens=2500, model=LEKTORAT_MODEL)
            obj = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            texte, _ = _korrekturen_anwenden(texte, obj.get("korrekturen") or [])
        except Exception:
            pass
    # letzte Sicherung fuer Zeichen-/Planetennamen
    return {k: namen.ersetze_namen(v, lang) if isinstance(v, str) else v for k, v in texte.items()}
