# investigator — all-in-one production image: engine (127.0.0.1:5003, not
# exposed) + UI backend serving the built frontend, both under gunicorn, on a
# single exposed port (:5050).
#
# Single container (not three) on purpose: the investigation subprocess posts to
# a hardcoded 127.0.0.1:5003 engine URL, so the backend and engine must share a
# network namespace. Splitting into three containers would need that URL made
# configurable first.
#
# Build:  docker build -t investigator .
# Run:    docker run --rm -p 5050:5050 \
#             -e OPENAI_API_KEY=sk-... -v investigator-data:/data investigator
# Or use docker compose (see docker-compose.yml). Open http://localhost:5050
FROM python:3.12-slim

# System deps: Node 20 (frontend build), git + curl, and the build toolchain +
# libxml/libxslt headers the lxml / newspaper3k stack may need.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
         curl git build-essential libxml2-dev libxslt1-dev \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Python package (engine + UI backend) — pulls the full pipeline stack.
RUN pip install --no-cache-dir -e .

# Frontend: install deps and build the static bundle the UI backend serves.
RUN cd ui && npm install --no-audit --no-fund && npm run build

# Durable session state + cumulative KG live under /data — mount a volume to keep
# them across container restarts.
ENV PYTHONPATH=/app/src:/app \
    INVESTIGATOR_TMFG=1 \
    INVESTIGATOR_STATE_DB=/data/state.sqlite3 \
    INVESTIGATOR_KG_STORE=/data/kg
VOLUME ["/data"]

EXPOSE 5050
ENTRYPOINT ["bash", "/app/docker/entrypoint.sh"]
