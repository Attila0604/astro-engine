from datetime import datetime, timedelta, timezone

import httpx

import erinnerung

JETZT = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def _u(i, stunden, **kw):
    return {"id": f"u{i}", "email": f"a{i}@x.hu", "created_at": (JETZT - timedelta(hours=stunden)).isoformat(),
            "user_metadata": {"lang": "hu"}, **kw}


def test_faellig():
    users = [_u(1, 30), _u(2, 5), _u(3, 30, email_confirmed_at="2026-11-20T10:00:00Z"),
             _u(4, 30, user_metadata={erinnerung.MERKER: "x"}), _u(5, 24 * 10)]
    assert [u["id"] for u in erinnerung.faellig(users, JETZT)] == ["u1"]


def test_lauf_sendet_einmal_und_merkt(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "k")
    aufrufe = []

    def antwort(req: httpx.Request):
        aufrufe.append((req.method, req.url.path, req.content))
        if req.url.path.endswith("/admin/users") and req.method == "GET":
            return httpx.Response(200, json={"users": [_u(1, 30), _u(2, 2)]})
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(antwort)) as c:
        r = erinnerung.lauf(JETZT, c)
    assert r == {"gesendet": 1, "fehler": []}
    assert ("POST", "/auth/v1/resend") in [(m, p) for m, p, _ in aufrufe]
    put = [b for m, p, b in aufrufe if m == "PUT"][0]
    assert b"bestaetigung_erinnert" in put and b'"lang": "hu"' in put.replace(b'":"', b'": "')
