"""Tests fuer die oeffentlichen Endpunkte (/public/sign-horoscope, /public/preview, /public/event)."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

import limits
import public_content

URL = "/public/sign-horoscope?sign={}&lang={}"
IP = {"x-forwarded-for": "100.64.0.7"}   # z. B. geteilte Handy-IP (CGNAT)


def heute():
    return datetime.now(timezone.utc).date().isoformat()


def gestern_row(sign="Lib", lang="hu"):
    d = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    return (sign, d, lang), {"sign": sign, "target_date": d, "lang": lang, "stimmung": "Tegnapi",
                             "body": "Tegnapi horoszkóp szövege, elég hosszú.", "tipp": "t", "liebe": "l", "beruf": "b"}


# --- IP-Limit -------------------------------------------------------------

def test_fertige_horoskope_verbrauchen_kein_ip_limit(client, db, claude):
    for _ in range(300):
        r = client.get(URL.format("Lib", "hu"), headers=IP)
        assert r.status_code == 200 and r.json()["ok"] is True
    assert claude.aufrufe == 1


def test_ereignisse_und_vorschau_blockieren_horoskop_nicht(client, db, claude):
    for _ in range(80):
        client.post("/public/event", json={"kind": "besuch"}, headers=IP)
    for _ in range(61):
        assert client.post("/public/preview", json={"year": 1990, "month": 5, "day": 3}, headers=IP).status_code == 200
    r = client.get(URL.format("Lib", "hu"), headers=IP)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_alle_48_seiten_von_einer_ip(client, db, claude):
    """Googlebot / jemand klickt alle 48 Sternzeichen-Seiten durch (je 2x)."""
    for _ in range(2):
        for lang in ("de", "en", "hu", "it"):
            for sign in public_content.SIGN_KEYS:
                r = client.get(URL.format(sign, lang), headers=IP)
                assert r.status_code == 200 and r.json()["ok"] is True, (sign, lang, r.text)
    assert claude.aufrufe == 48


def test_limit_fuer_neue_erzeugungen_liefert_ersatz_statt_429(client, db, claude, monkeypatch):
    monkeypatch.setenv("LIMIT_STERNZEICHEN_NEU", "1")
    k, row = gestern_row("Leo", "hu")
    db.rows[k] = row
    assert client.get(URL.format("Lib", "hu"), headers=IP).json()["ok"] is True   # 1. Erzeugung
    r = client.get(URL.format("Leo", "hu"), headers=IP)                         # Limit erreicht
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["data"]["source"] == "fallback"
    assert claude.aufrufe == 1
    # Ohne Ersatz: freundliches ok:false, aber kein 429
    r = client.get(URL.format("Sco", "it"), headers=IP)
    assert r.status_code == 200 and r.json()["ok"] is False


def test_gesamtdeckel_gibt_ip_zaehler_zurueck(client, db, claude, monkeypatch):
    monkeypatch.setenv("LIMIT_STERNZEICHEN_TAG", "1")
    client.get(URL.format("Lib", "hu"), headers=IP)
    r = client.get(URL.format("Leo", "hu"), headers={"x-forwarded-for": "5.5.5.5"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert claude.aufrufe == 1
    assert limits._counts.get(("ip:5.5.5.5", "sternzeichen_neu"), 0) == 0


# --- Kostenkontrolle --------------------------------------------------------

def test_speichern_scheitert_trotzdem_nur_ein_claude_aufruf(client, db, claude):
    db.save_fehler = "APIError: there is no unique or exclusion constraint matching the ON CONFLICT"
    for _ in range(5):
        r = client.get(URL.format("Ari", "it"), headers=IP)
        assert r.json()["ok"] is True
    assert claude.aufrufe == 1


def test_db_lesen_scheitert_trotzdem_nur_ein_claude_aufruf(client, db, claude):
    db.lese_fehler = "ConnectError"
    for _ in range(3):
        assert client.get(URL.format("Ari", "en"), headers=IP).json()["ok"] is True
    assert claude.aufrufe == 1


def test_gleichzeitige_anfragen_ein_aufruf_je_zeichen_und_sprache(db, claude):
    claude.verzoegerung = 0.05

    async def los():
        jobs = [public_content.sign_horoscope(s, l) for s in public_content.SIGN_KEYS
                for l in ("de", "en", "hu", "it") for _ in range(3)]
        return await asyncio.gather(*jobs)

    ergebnisse = asyncio.run(los())
    assert all(r["ok"] for r in ergebnisse)
    assert claude.aufrufe == 48
    assert claude.max_gleichzeitig <= public_content.GLEICHZEITIG
    assert db.saves == 48


def test_nach_fehler_begrenzte_versuche_und_status(client, db, claude, monkeypatch):
    claude.fehler = RuntimeError("overloaded")
    r = client.get(URL.format("Sco", "hu"), headers=IP)
    assert r.status_code == 200 and r.json()["ok"] is False
    client.get(URL.format("Sco", "hu"), headers=IP)        # in der Pause: kein neuer Aufruf
    assert claude.aufrufe == 1
    monkeypatch.setattr(public_content, "PAUSE_NACH_FEHLER_SEK", 0)
    for _ in range(public_content.MAX_VERSUCHE + 2):
        client.get(URL.format("Sco", "hu"), headers=IP)    # weitere Versuche bis zur Grenze
    assert claude.aufrufe == public_content.MAX_VERSUCHE
    st = client.get("/public/horoskop-status").json()["data"]
    assert st["versuche"]["Sco:hu"] == public_content.MAX_VERSUCHE and "overloaded" in st["fehler"]["Sco:hu"]


# --- Fehler -> sanfter Ersatz --------------------------------------------------

def test_claude_fehler_liefert_letztes_horoskop(client, db, claude):
    k, row = gestern_row()
    db.rows[k] = row
    claude.fehler = RuntimeError("overloaded")
    r = client.get(URL.format("Lib", "hu"), headers=IP).json()
    assert r["ok"] is True and r["data"]["source"] == "fallback" and r["data"]["text"].startswith("Tegnapi")


def test_lektorat_fehler_gibt_ungeprueften_text(client, db, claude, monkeypatch):
    async def kaputt(*a, **k):
        raise ValueError("lektorat kaputt")
    monkeypatch.setattr(public_content, "lektorat", kaputt)
    r = client.get(URL.format("Cap", "hu"), headers=IP)
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["data"]["text"].startswith("A mai nap")


def test_abgeschnittenes_json_zeigt_kein_rohes_json(client, db, claude):
    claude.antwort = ('{"stimmung": "Mai nap", "text": "Az első mondat teljes. A második mondat is kész. '
                      'A harmadik mondat félbe')
    r = client.get(URL.format("Leo", "hu"), headers=IP).json()
    assert r["ok"] is True
    assert r["data"]["stimmung"] == "Mai nap"
    assert r["data"]["text"] == "Az első mondat teljes. A második mondat is kész."
    assert "{" not in r["data"]["text"]


def test_leere_antwort_wird_nicht_gespeichert(client, db, claude):
    k, row = gestern_row("Vir", "it")
    db.rows[k] = row
    claude.antwort = ""
    r = client.get(URL.format("Vir", "it"), headers=IP).json()
    assert r["ok"] is True and r["data"]["source"] == "fallback"
    assert db.saves == 0


def test_absaetze_als_liste_werden_text(client, db, claude):
    claude.antwort = json.dumps({"stimmung": "Oggi", "text": ["Primo paragrafo di prova.", "Secondo paragrafo."],
                                 "tipp": "x", "liebe": "y", "beruf": "z"})
    r = client.get(URL.format("Pis", "it"), headers=IP).json()
    assert r["data"]["text"] == "Primo paragrafo di prova.\n\nSecondo paragrafo."


def test_langsame_erzeugung_liefert_ersatz_und_spaeter_neues(db, claude, monkeypatch):
    monkeypatch.setattr(public_content, "WARTEN_SEK", 0.05)
    claude.verzoegerung = 0.3
    k, row = gestern_row("Tau", "en")
    db.rows[k] = row

    async def los():
        erst = await public_content.sign_horoscope("Tau", "en")
        await asyncio.sleep(0.5)
        danach = await public_content.sign_horoscope("Tau", "en")
        return erst, danach

    erst, danach = asyncio.run(los())
    assert erst["data"]["source"] == "fallback"
    assert danach["data"]["source"] == "cached" and danach["data"]["date"] == heute()
    assert claude.aufrufe == 1


def test_unerwarteter_fehler_wird_kein_500(client, db, claude, monkeypatch):
    async def kaputt(*a, **k):
        raise KeyError("x")
    monkeypatch.setattr(public_content, "sign_horoscope", kaputt)
    r = client.get(URL.format("Lib", "hu"), headers={**IP, "Origin": "https://www.sorayaluxuryastroguide.com"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert r.headers.get("access-control-allow-origin") == "*"


@pytest.mark.parametrize("q", ["sign=lib&lang=hu", "sign=Lib&lang=HU", "sign=Lib&lang=xx", "sign=Lib"])
def test_schreibweisen(client, db, claude, q):
    r = client.get("/public/sign-horoscope?" + q, headers=IP)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_ohne_oder_falsches_zeichen(client, db, claude):
    for q in ("", "?sign=", "?sign=Foo"):
        r = client.get("/public/sign-horoscope" + q, headers=IP)
        assert r.status_code == 200 and r.json() == {"ok": False, "error": "Unbekanntes Sternzeichen."}
    assert claude.aufrufe == 0


def test_alter_eintrag_ohne_text_gilt_nicht_als_fertig(client, db, claude):
    db.rows[("Gem", heute(), "hu")] = {"sign": "Gem", "target_date": heute(), "lang": "hu", "body": None}
    r = client.get(URL.format("Gem", "hu"), headers=IP).json()
    assert r["ok"] is True and r["data"]["text"]
    assert claude.aufrufe == 1


# --- /public/preview und /public/event -------------------------------------------

def test_preview_ok_und_fehler(client, db):
    r = client.post("/public/preview", json={"year": 1990, "month": 5, "day": 3}, headers=IP).json()
    assert r["ok"] is True and r["data"]["sun"]["sign"] == "Tau"
    r = client.post("/public/preview", json={"year": 1990, "month": 2, "day": 31}, headers=IP).json()
    assert r["ok"] is False
    r = client.post("/public/preview", json={"year": 1990, "month": 5, "day": 3, "hour": 25}, headers=IP).json()
    assert r["ok"] is False


def test_event(client, db):
    assert client.post("/public/event", json={"kind": "besuch", "q": "P12"}, headers=IP).json() == {"ok": True}
    assert db.zaehler[0] == "besuch" and db.zaehler[2] == "q:p12:besuch"
    assert db.zaehler[1].startswith("stunde:") and len(db.zaehler[1]) == 9      # Uhrzeit des Besuchs (Wien)
    assert client.post("/public/event", json={"kind": "boese"}, headers=IP).json()["ok"] is False
    assert client.post("/public/event", json={"kind": "horoskop_gelesen", "q": "bio-ig"}, headers=IP).json()["ok"] is True
    assert client.post("/public/event", json={"kind": "schnellstart", "q": "bio-ig"}, headers=IP).json()["ok"] is True
    assert limits._counts.get(("ip:100.64.0.7", "oeffentlich"), 0) == 0
    vorher = list(db.zaehler)
    for ua in ("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
               "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36 (compatible; Googlebot/2.1)",
               "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 HeadlessChrome/120.0 Safari/537.36"):
        assert client.post("/public/event", json={"kind": "besuch", "q": "seo-widder"}, headers={**IP, "user-agent": ua}).json()["ok"] is True
    assert db.zaehler == vorher                       # Roboter zaehlen nicht als Besucher
    client.post("/public/event", json={"kind": "besuch", "q": "seo-widder"},
                headers={**IP, "user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Instagram 300.0"})
    assert "q:seo-widder:besuch" in db.zaehler
