# Dockerfile -- robustes Image fuer die Astro-Engine.
# Ersetzt Nixpacks: ein normales Debian-Python-Image, in dem System-
# Bibliotheken (libsqlite3 etc.) genau dort liegen, wo Python sie sucht.

FROM python:3.11-slim

# build-essential = C-Compiler, damit pyswisseph (Swiss Ephemeris) bauen kann.
# libsqlite3-0 ist im python-slim-Image bereits enthalten -- der Crash von vorhin
# kann hier also gar nicht erst entstehen.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Railway setzt $PORT; lokaler Fallback ist 8080.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]
