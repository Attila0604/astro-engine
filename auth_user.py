"""
auth_user.py
Mobile-sichere Supabase-User-Pruefung fuer Soraya.

Die Android-App sendet nach dem Supabase-Login:
    Authorization: Bearer <supabase_access_token>

Das Backend fragt Supabase Auth, wer dieser User ist.
owner_id kommt dadurch nicht mehr aus dem Request-Body.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import Header, HTTPException


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HTTPException(status_code=500, detail=f"{name} ist im Backend nicht gesetzt.")
    return value


async def get_current_supabase_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Fehlender Authorization Bearer Token.")

    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Leerer Authorization Bearer Token.")

    supabase_url = _env("SUPABASE_URL").rstrip("/")
    anon_key = _env("SUPABASE_ANON_KEY")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{supabase_url}/auth/v1/user",
                headers={
                    "apikey": anon_key,
                    "Authorization": f"Bearer {token}",
                },
            )
    except httpx.HTTPError as e:
        raise HTTPException(status_code=503, detail=f"Supabase Auth nicht erreichbar: {type(e).__name__}")

    if resp.status_code != 200:
        raise HTTPException(status_code=401, detail="Ungueltiger oder abgelaufener Supabase Token.")

    user = resp.json()
    if not user.get("id"):
        raise HTTPException(status_code=401, detail="Supabase User konnte nicht gelesen werden.")

    return user
