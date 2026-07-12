#!/usr/bin/env bash
# Production entrypoint: engine + UI backend under gunicorn, frontend served
# as the built static bundle by the UI backend itself. One exposed port
# (:5050); the engine binds to 127.0.0.1 only — nothing but the UI backend
# should ever reach it. They share one container because the investigation
# subprocess posts to a hardcoded 127.0.0.1:5003 engine URL.
set -euo pipefail

mkdir -p /data

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "WARNING: OPENAI_API_KEY is not set — extraction/query calls will fail." >&2
fi

# -w 1 is required for BOTH services: engine session state / per-session locks
# and the backend's job queue / SSE pub-sub are in-process. Concurrency comes
# from threads. --timeout 0: stage-2 pipeline calls and SSE progress streams
# are legitimately long-lived.
echo "[investigator] starting engine on 127.0.0.1:5003 (gunicorn) ..."
INVESTIGATOR_TMFG=1 ANALYTIC_ENGINE_ENABLED="${ANALYTIC_ENGINE_ENABLED:-1}" \
  gunicorn -w 1 --threads 16 --timeout 0 -b 127.0.0.1:5003 \
  investigator.wsgi:app &
engine_pid=$!

echo "[investigator] starting UI backend + frontend on :5050 (gunicorn) ..."
gunicorn -w 1 --threads 32 --timeout 0 -b 0.0.0.0:5050 \
  --chdir /app/ui server:app &
backend_pid=$!

echo "[investigator] up — open http://localhost:5050"

# If either service exits, tear the container down so the failure is visible.
wait -n "$engine_pid" "$backend_pid"
echo "[investigator] a service exited; shutting down." >&2
kill "$engine_pid" "$backend_pid" 2>/dev/null || true
exit 1
