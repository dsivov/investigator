#!/usr/bin/env bash
# Start all three investigator services on localhost inside the container:
#   engine (:5003) · UI backend (:5050) · frontend/Vite (:5180)
# They live in one container because the investigation subprocess posts to a
# hardcoded 127.0.0.1:5003 engine URL, so backend + engine must share localhost.
set -euo pipefail

mkdir -p /data

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "WARNING: OPENAI_API_KEY is not set — extraction/query calls will fail." >&2
fi

echo "[investigator] starting engine on :5003 ..."
INVESTIGATOR_TMFG=1 python -m investigator &
engine_pid=$!

echo "[investigator] starting UI backend on :5050 ..."
python ui/server.py --host 0.0.0.0 --port 5050 &
backend_pid=$!

echo "[investigator] starting frontend on :5180 ..."
( cd ui && npm run dev -- --host 0.0.0.0 ) &
frontend_pid=$!

echo "[investigator] up — open http://localhost:5180"

# If any service exits, tear the container down so the failure is visible.
wait -n "$engine_pid" "$backend_pid" "$frontend_pid"
echo "[investigator] a service exited; shutting down." >&2
kill "$engine_pid" "$backend_pid" "$frontend_pid" 2>/dev/null || true
exit 1
