"""
Erinnerung an die E-Mail-Bestaetigung (Wunsch Attila 21.11.2026).

Wer sich registriert, aber die Bestaetigungs-Mail nach einem Tag noch nicht angeklickt hat,
bekommt sie EINMAL erneut (gleiche Supabase-Vorlage "Confirm sign up", Sprache aus den Metadaten).
Gemerkt wird das in den user_metadata ("bestaetigung_erinnert"), damit niemand zweimal erinnert wird.

Railway: ERINNERUNG_STUNDEN (Standard 3 = alle 3 Stunden nachsehen, 0 = aus).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import httpx

AB_STUNDEN = 20          # fruehestens so lange nach der Registrierung
BIS_TAGE = 7             # danach nicht mehr (Adresse vermutlich falsch oder kein Interesse)
MERKER = "bestaetigung_erinnert"


def _zeit(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def faellig(users: list[dict], jetzt: datetime) -> list[dict]:
    """Nutzer, die jetzt eine Erinnerung bekommen sollen."""
    out = []
    for u in users:
        if u.get("email_confirmed_at") or u.get("confirmed_at") or not u.get("email"):
            continue
        if (u.get("user_metadata") or {}).get(MERKER):
            continue
        erstellt = _zeit(u.get("created_at"))
        if not erstellt:
            continue
        alter = jetzt - erstellt
        if timedelta(hours=AB_STUNDEN) <= alter <= timedelta(days=BIS_TAGE):
            out.append(u)
    return out


def _kopf() -> tuple[str, dict]:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY fehlen")
    return url, {"apikey": key, "Authorization": f"Bearer {key}"}


def alle_nutzer(client: httpx.Client, url: str, kopf: dict) -> list[dict]:
    users, seite = [], 1
    while seite <= 50:
        r = client.get(f"{url}/auth/v1/admin/users", params={"page": seite, "per_page": 200}, headers=kopf)
        r.raise_for_status()
        teil = (r.json() or {}).get("users") or []
        users += teil
        if len(teil) < 200:
            break
        seite += 1
    return users


def lauf(jetzt: datetime | None = None, client: httpx.Client | None = None) -> dict:
    """Ein Durchgang: faellige Nutzer erinnern. Rueckgabe fuers Log."""
    jetzt = jetzt or datetime.now(timezone.utc)
    url, kopf = _kopf()
    eigen = client is None
    client = client or httpx.Client(timeout=20)
    gesendet, fehler = 0, []
    try:
        for u in faellig(alle_nutzer(client, url, kopf), jetzt):
            try:
                r = client.post(f"{url}/auth/v1/resend", json={"type": "signup", "email": u["email"]}, headers=kopf)
                r.raise_for_status()
                meta = dict(u.get("user_metadata") or {})
                meta[MERKER] = jetzt.isoformat(timespec="minutes")
                r = client.put(f"{url}/auth/v1/admin/users/{u['id']}", json={"user_metadata": meta}, headers=kopf)
                r.raise_for_status()
                gesendet += 1
            except Exception as e:  # noqa: BLE001 -- ein Fehler darf die anderen nicht aufhalten
                fehler.append(f"{type(e).__name__}: {str(e)[:120]}")
    finally:
        if eigen:
            client.close()
    return {"gesendet": gesendet, "fehler": fehler}


def _log(text: str) -> None:
    print(f"[Erinnerung] {text}", flush=True)


def intervall_stunden() -> float:
    try:
        return float(os.environ.get("ERINNERUNG_STUNDEN", "3"))
    except ValueError:
        return 3.0


async def schleife(stunden: float) -> None:
    await asyncio.sleep(120)                     # App-Start nicht aufhalten
    while True:
        try:
            ergebnis = await asyncio.to_thread(lauf)
            if ergebnis["gesendet"] or ergebnis["fehler"]:
                _log(f"{ergebnis['gesendet']} erinnert" + (f", Fehler: {ergebnis['fehler'][:3]}" if ergebnis["fehler"] else ""))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            _log(f"FEHLER {type(e).__name__}: {e}")
        await asyncio.sleep(max(1.0, stunden) * 3600)
