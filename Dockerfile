# Dockerfile -- robustes Image fuer die Astro-Engine.
# Normales Debian-Python-Image, in dem System-Bibliotheken (libsqlite3 etc.)
# genau dort liegen, wo Python sie sucht.

FROM python:3.11-slim

# build-essential = C-Compiler, damit pyswisseph (Swiss Ephemeris) bauen kann.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# --upgrade erzwingt einen frischen Install und bricht einen veralteten
# Docker-Layer-Cache (sonst fehlen neu hinzugefuegte Pakete wie geopy).
RUN pip install --no-cache-dir --upgrade -r requirements.txt

COPY . .

# Railway setzt $PORT; lokaler Fallback ist 8080.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
