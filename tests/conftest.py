"""
Gemeinsame Attrappen fuer die Tests: keine echte Datenbank, kein Claude.
Starten:  pip install -r requirements.txt pytest  &&  python -m pytest tests
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import agents  # noqa: E402
import limits  # noqa: E402
import main  # noqa: E402
import public_content  # noqa: E402

GUTE_ANTWORT = {
    "stimmung": "Nyugodt, tiszta nap",
    "text": "A mai nap nyugalmat hoz. A Hold támogat abban, hogy rendet tegyél a gondolataidban.\n\nEste jó egy séta.",
    "tipp": "Írd le három dolgot, amiért hálás vagy.",
    "liebe": "Egy kedves szó sokat számít.",
    "beruf": "Egy feladatot fejezz be teljesen.",
}


class FakeDB:
    def __init__(self):
        self.rows = {}           # (sign, date, lang) -> row
        self.save_fehler = None  # Text -> save liefert ok:false
        self.lese_fehler = None
        self.saves = 0
        self.zaehler = []

    def get(self, sign, target_date, lang="de"):
        if self.lese_fehler:
            return {"ok": False, "error": self.lese_fehler}
        return {"ok": True, "data": self.rows.get((sign, target_date, lang))}

    def latest(self, sign, before_date, lang="de"):
        if self.lese_fehler:
            return {"ok": False, "error": self.lese_fehler}
        alte = sorted((d, r) for (s, d, l), r in self.rows.items() if s == sign and l == lang and d < before_date)
        return {"ok": True, "data": alte[-1][1] if alte else None}

    def save(self, row):
        self.saves += 1
        if self.save_fehler:
            return {"ok": False, "error": self.save_fehler}
        self.rows[(row["sign"], row["target_date"], row["lang"])] = dict(row)
        return {"ok": True, "data": dict(row)}


class FakeClaude:
    def __init__(self):
        self.antwort = json.dumps(GUTE_ANTWORT, ensure_ascii=False)
        self.fehler = None
        self.verzoegerung = 0.0
        self.aufrufe = 0
        self.gleichzeitig = 0
        self.max_gleichzeitig = 0

    async def __call__(self, system, user, max_tokens, model=None):
        import asyncio
        self.aufrufe += 1
        self.gleichzeitig += 1
        self.max_gleichzeitig = max(self.max_gleichzeitig, self.gleichzeitig)
        try:
            if self.verzoegerung:
                await asyncio.sleep(self.verzoegerung)
            if self.fehler:
                raise self.fehler
            return self.antwort
        finally:
            self.gleichzeitig -= 1


@pytest.fixture
def db(monkeypatch):
    d = FakeDB()
    monkeypatch.setattr(public_content, "get_sign_horoscope", d.get)
    monkeypatch.setattr(public_content, "get_latest_sign_horoscope", d.latest)
    monkeypatch.setattr(public_content, "save_sign_horoscope", d.save)
    monkeypatch.setattr(main, "increment_public_usage", lambda kind: d.zaehler.append(kind))
    return d


@pytest.fixture
def claude(monkeypatch):
    c = FakeClaude()
    monkeypatch.setattr(public_content, "_call_claude", c)
    # Lektorat nutzt ebenfalls Claude -- nie echt aufrufen; Ungarisch wird immer geprueft (Attrappe: keine Korrekturen)
    c.lektorat_aufrufe = []
    async def kein_lektorat(system, text, **k):
        c.lektorat_aufrufe.append(text)
        return '{"korrekturen": []}'
    monkeypatch.setattr(agents, "_call_claude", kein_lektorat)
    # Himmelsdaten sind fuer die Tests egal (spart Rechenzeit)
    monkeypatch.setattr(public_content, "_today_sky_text", lambda lang="de": "- Sonne in Waage")
    return c


@pytest.fixture(autouse=True)
def sauber():
    limits.reset()
    public_content.reset_cache()
    yield
    limits.reset()
    public_content.reset_cache()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    return TestClient(main.app, raise_server_exceptions=False)
