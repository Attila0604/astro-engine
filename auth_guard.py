
"""
auth_guard.py
Einfacher API-Key-Schutz fuer Soraya.

Ziel:
Teure Backend-Endpunkte wie /analysis/save, /horoscope/save und /chat/save
duerfen nur aufgerufen werden, wenn ein geheimer Header mitgesendet wird.

Railway Variable:
    SORAYA_API_KEY=dein_langer_geheimer_key

Request Header:
    X-Soraya-API-Key: dein_langer_geheimer_key
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException


def require_soraya_api_key(x_soraya_api_key: str | None = Header(default=None)) -> bool:
    expected = os.environ.get("SORAYA_API_KEY")

    if not expected:
        raise HTTPException(
            status_code=500,
            detail="SORAYA_API_KEY ist im Backend nicht gesetzt.",
        )

    if not x_soraya_api_key or x_soraya_api_key != expected:
        raise HTTPException(
            status_code=401,
            detail="Ungueltiger oder fehlender Soraya API Key.",
        )

    return True
