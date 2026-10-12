"""
namen.py
Astrologische Namen in den App-Sprachen (de, en, hu, it) und eine Kontrolle auf
deutsche Reste in fremdsprachigen Texten.

Warum: Claude bekam die Chart-Daten bisher nur auf Deutsch ("Sonne in Jungfrau")
und hat Namen wie "Jungfrau" dann teils in ungarische Texte uebernommen. Jetzt
gehen die Daten in der Zielsprache an Claude, mit einem Namens-Glossar, und der
fertige Text wird auf deutsche Reste geprueft.
"""

from __future__ import annotations

import re

import chart_engine as ce

SIGNS = {
    "de": ce.SIGNS_DE,
    "en": {"Ari": "Aries", "Tau": "Taurus", "Gem": "Gemini", "Can": "Cancer", "Leo": "Leo", "Vir": "Virgo",
           "Lib": "Libra", "Sco": "Scorpio", "Sag": "Sagittarius", "Cap": "Capricorn", "Aqu": "Aquarius",
           "Pis": "Pisces"},
    "hu": {"Ari": "Kos", "Tau": "Bika", "Gem": "Ikrek", "Can": "Rák", "Leo": "Oroszlán", "Vir": "Szűz",
           "Lib": "Mérleg", "Sco": "Skorpió", "Sag": "Nyilas", "Cap": "Bak", "Aqu": "Vízöntő", "Pis": "Halak"},
    "it": {"Ari": "Ariete", "Tau": "Toro", "Gem": "Gemelli", "Can": "Cancro", "Leo": "Leone", "Vir": "Vergine",
           "Lib": "Bilancia", "Sco": "Scorpione", "Sag": "Sagittario", "Cap": "Capricorno", "Aqu": "Acquario",
           "Pis": "Pesci"},
}

PLANETS = {
    "de": ce.PLANETS_DE,
    "en": {"Sun": "Sun", "Moon": "Moon", "Mercury": "Mercury", "Venus": "Venus", "Mars": "Mars",
           "Jupiter": "Jupiter", "Saturn": "Saturn", "Uranus": "Uranus", "Neptune": "Neptune", "Pluto": "Pluto",
           "Chiron": "Chiron", "True_North_Lunar_Node": "North Node", "True_South_Lunar_Node": "South Node",
           "Mean_Lilith": "Lilith", "Ascendant": "Ascendant", "Medium_Coeli": "Midheaven (MC)",
           "Descendant": "Descendant", "Imum_Coeli": "IC"},
    "hu": {"Sun": "Nap", "Moon": "Hold", "Mercury": "Merkúr", "Venus": "Vénusz", "Mars": "Mars",
           "Jupiter": "Jupiter", "Saturn": "Szaturnusz", "Uranus": "Uránusz", "Neptune": "Neptunusz",
           "Pluto": "Plútó", "Chiron": "Kiron", "True_North_Lunar_Node": "Északi Holdcsomó",
           "True_South_Lunar_Node": "Déli Holdcsomó", "Mean_Lilith": "Lilith", "Ascendant": "Aszcendens",
           "Medium_Coeli": "MC (Medium Coeli)", "Descendant": "Descendens", "Imum_Coeli": "IC"},
    "it": {"Sun": "Sole", "Moon": "Luna", "Mercury": "Mercurio", "Venus": "Venere", "Mars": "Marte",
           "Jupiter": "Giove", "Saturn": "Saturno", "Uranus": "Urano", "Neptune": "Nettuno", "Pluto": "Plutone",
           "Chiron": "Chirone", "True_North_Lunar_Node": "Nodo Nord", "True_South_Lunar_Node": "Nodo Sud",
           "Mean_Lilith": "Lilith", "Ascendant": "Ascendente", "Medium_Coeli": "Medio Cielo (MC)",
           "Descendant": "Discendente", "Imum_Coeli": "Fondo Cielo (IC)"},
}

ASPECTS = {
    "de": ce.ASPECTS_DE,
    "en": {"conjunction": "conjunction", "opposition": "opposition", "trine": "trine", "square": "square",
           "sextile": "sextile", "quincunx": "quincunx", "semisextile": "semi-sextile",
           "semisquare": "semi-square", "sesquiquadrate": "sesquiquadrate", "quintile": "quintile",
           "biquintile": "biquintile"},
    "hu": {"conjunction": "együttállás", "opposition": "szembenállás", "trine": "trigon", "square": "kvadrát",
           "sextile": "szextil", "quincunx": "kvinkunx", "semisextile": "félszextil", "semisquare": "félkvadrát",
           "sesquiquadrate": "másfélkvadrát", "quintile": "kvintil", "biquintile": "bikvintil"},
    "it": {"conjunction": "congiunzione", "opposition": "opposizione", "trine": "trigono", "square": "quadratura",
           "sextile": "sestile", "quincunx": "quinconce", "semisextile": "semisestile",
           "semisquare": "semiquadrato", "sesquiquadrate": "sesquiquadrato", "quintile": "quintile",
           "biquintile": "biquintile"},
}

# Elemente/Qualitaeten kommen aus chart_engine bereits mit deutschen Schluesseln
ELEMENTS = {
    "en": {"Feuer": "fire", "Erde": "earth", "Luft": "air", "Wasser": "water"},
    "hu": {"Feuer": "tűz", "Erde": "föld", "Luft": "levegő", "Wasser": "víz"},
    "it": {"Feuer": "fuoco", "Erde": "terra", "Luft": "aria", "Wasser": "acqua"},
}
QUALITIES = {
    "en": {"kardinal": "cardinal", "fix": "fixed", "veränderlich": "mutable"},
    "hu": {"kardinal": "kardinális", "fix": "fix", "veränderlich": "változó"},
    "it": {"kardinal": "cardinale", "fix": "fisso", "veränderlich": "mobile"},
}

_SIGN_KEY_BY_DE = {v: k for k, v in ce.SIGNS_DE.items()}
_PLANET_KEY_BY_DE = {v: k for k, v in ce.PLANETS_DE.items()}
_ASPECT_KEY_BY_DE = {v: k for k, v in ce.ASPECTS_DE.items()}


def _lang(lang) -> str:
    lang = (lang or "de").lower()[:2]
    return lang if lang in SIGNS else "de"


def zeichen(key_or_de, lang) -> str:
    """Sternzeichen aus Schluessel ("Vir") oder deutschem Namen ("Jungfrau")."""
    key = _SIGN_KEY_BY_DE.get(key_or_de, key_or_de)
    return SIGNS[_lang(lang)].get(key, ce.SIGNS_DE.get(key, key_or_de or ""))


def planet(name, lang) -> str:
    key = _PLANET_KEY_BY_DE.get(name, name)
    return PLANETS[_lang(lang)].get(key, ce.PLANETS_DE.get(key, name or ""))


def aspekt(typ, lang) -> str:
    key = _ASPECT_KEY_BY_DE.get(typ, typ)
    return ASPECTS[_lang(lang)].get(key, ce.ASPECTS_DE.get(key, typ or ""))


def element(name_de, lang) -> str:
    return ELEMENTS.get(_lang(lang), {}).get(name_de, name_de)


def qualitaet(name_de, lang) -> str:
    return QUALITIES.get(_lang(lang), {}).get(name_de, name_de)


def glossar(lang) -> str:
    """Kurzes Namens-Glossar fuer den System-Prompt (nur fuer en/hu/it)."""
    lang = _lang(lang)
    if lang == "de":
        return ""
    s = ", ".join(SIGNS[lang][k] for k in ce.SIGNS_DE)
    p = ", ".join(PLANETS[lang][k] for k in ("Sun", "Moon", "Mercury", "Venus", "Mars", "Jupiter", "Saturn",
                                             "Uranus", "Neptune", "Pluto", "Ascendant"))
    a = ", ".join(ASPECTS[lang][k] for k in ("conjunction", "opposition", "trine", "square", "sextile"))
    return f"\nGLOSSARY ({lang}) - zodiac signs: {s}. Planets/points: {p}. Aspects: {a}."


# ---------------------------------------------------------------------------
# Kontrolle: deutsche Reste in fremdsprachigen Texten
# ---------------------------------------------------------------------------
# Nur Woerter, die es in en/hu/it so nicht gibt (Wortgrenzen beachten: "Neptun"
# trifft nicht "Neptunusz", "Skorpion" nicht "Skorpió").
_DEUTSCHE_NAMEN = (
    [v for v in ce.SIGNS_DE.values()]
    + ["Zwilling", "Sonne", "Merkur", "Neptun", "Aszendent", "Deszendent", "Mondknoten", "Himmelsmitte",
       "Konjunktion", "Quadrat", "Trigon", "Sextil", "Halbsextil", "Mondphase"]
)
_MUSTER_NAMEN = re.compile(r"(?<![\wÀ-ÿ])(" + "|".join(sorted(map(re.escape, _DEUTSCHE_NAMEN), key=len, reverse=True))
                           + r")(?![a-zäöüß])")
# In hu/it heissen auch diese Planeten anders (im Englischen gleich -> dort nicht pruefen)
_DEUTSCH_NICHT_EN = ["Saturn", "Uranus", "Pluto", "Venus"]
_MUSTER_NICHT_EN = re.compile(r"(?<![\wÀ-ÿ])(" + "|".join(_DEUTSCH_NICHT_EN) + r")(?![a-zäöüß])")
# haeufige deutsche Fuellwoerter (nicht fuer Englisch, wo "die" ein Wort ist)
_MUSTER_WOERTER = re.compile(r"(?<![\wÀ-ÿ])(und|nicht|auch|deine|dein|wenn|oder|Haus)(?![\wÀ-ÿ])")


# Deutsche Zeichennamen sind so eindeutig, dass sie auch mit angehaengter Endung
# zaehlen ("Jungfrauban"); "Skorpion" nur ohne Endung (italienisch "Scorpione" ist anders geschrieben).
_MUSTER_ZEICHEN_ENDUNG = re.compile(r"(?<![\wÀ-ÿ])(" + "|".join(
    sorted((re.escape(v) for v in ce.SIGNS_DE.values() if v != "Skorpion"), key=len, reverse=True)) + r")[a-zäöüőű]+")


def deutsche_reste(text: str, lang) -> list[str]:
    """Deutsche Sternzeichen-/Planeten-Namen und Fuellwoerter in einem en/hu/it-Text."""
    lang = _lang(lang)
    if lang == "de" or not text:
        return []
    funde = _MUSTER_NAMEN.findall(text) + _MUSTER_ZEICHEN_ENDUNG.findall(text)
    if lang in ("hu", "it"):
        funde += _MUSTER_WOERTER.findall(text) + _MUSTER_NICHT_EN.findall(text)
    # "Trigon" ist auch ungarisch, "Sextil" fast: nur im Englischen/Italienischen pruefen
    if lang == "hu":
        funde = [f for f in funde if f not in ("Trigon",)]
    return sorted(set(funde))


def ersetze_namen(text: str, lang) -> str:
    """Letzte Sicherung: deutsche Zeichen-/Planetennamen durch die Zielsprache ersetzen."""
    lang = _lang(lang)
    if lang == "de" or not isinstance(text, str) or not text:
        return text
    tabelle = {de: SIGNS[lang][key] for key, de in ce.SIGNS_DE.items()}
    tabelle.update({"Zwilling": SIGNS[lang]["Gem"], "Sonne": PLANETS[lang]["Sun"], "Merkur": PLANETS[lang]["Mercury"],
                    "Neptun": PLANETS[lang]["Neptune"], "Aszendent": PLANETS[lang]["Ascendant"],
                    "Deszendent": PLANETS[lang]["Descendant"]})
    if lang in ("hu", "it"):
        tabelle.update({"Saturn": PLANETS[lang]["Saturn"], "Uranus": PLANETS[lang]["Uranus"],
                        "Pluto": PLANETS[lang]["Pluto"], "Venus": PLANETS[lang]["Venus"]})
    muster = re.compile(r"(?<![\wÀ-ÿ])(" + "|".join(sorted(map(re.escape, tabelle), key=len, reverse=True))
                        + r")(?![a-zäöüß])")
    return muster.sub(lambda m: tabelle[m.group(1)], text)
