# Operations

How to run Investigator, the environment knobs, and how to keep it inside memory.

## Running in development

Three processes (see [architecture.md](architecture.md)). Secrets load from a
git-ignored `.env` (`cp .env.example .env`, set `OPENAI_API_KEY`).

```sh
# 1. Pipeline engine (port 5003)
INVESTIGATOR_TMFG=1 INVESTIGATOR_VIZ=1 INVESTIGATOR_DISABLE_CACHE=1 \
  PYTHONPATH=src:. python -m investigator
# add --analytic_engine_enabled (or ANALYTIC_ENGINE_ENABLED=1) to accumulate
# runs into the cumulative KG

# 2. UI backend (port 5050)
PYTHONPATH=.:src python ui/server.py --port 5050
# add --host 0.0.0.0 to reach it from another machine on the LAN

# 3. Frontend (port 5180, hot reload)
cd ui && npm install && npm run dev
```

Open **http://localhost:5180** (or `http://<host-lan-ip>:5180` from another
workstation — the Vite dev server binds all interfaces).

## Running in production (gunicorn, two processes)

The dev servers above are single-user development conveniences. For anything
other people can reach, run both Flask apps under gunicorn and serve the
built frontend from the UI backend — this is what the Docker image does
(`docker compose up --build`, then open **http://localhost:5050**):

```sh
cd ui && npm run build && cd ..     # bundle -> ui/dist (backend auto-serves it)

# 1. Pipeline engine — bind to LOCALHOST ONLY; it has no auth and only the
#    UI backend should ever reach it.
INVESTIGATOR_TMFG=1 ANALYTIC_ENGINE_ENABLED=1 PYTHONPATH=src:. \
  gunicorn -w 1 --threads 16 --timeout 0 -b 127.0.0.1:5003 investigator.wsgi:app

# 2. UI backend — one exposed port for API + frontend.
gunicorn -w 1 --threads 32 --timeout 0 -b 0.0.0.0:5050 --chdir ui server:app
```

Open **http://localhost:5050**. Non-negotiable flags:

- **`-w 1`** on both: engine session state / per-session locks and the
  backend's job queue / SSE pub-sub are in-process; a second worker process
  would silently see none of it. Concurrency comes from `--threads`.
- **`--timeout 0`**: stage-2 pipeline calls run for many minutes and SSE
  progress streams live for a whole investigation; gunicorn's default 30 s
  timeout would kill both.
- Under gunicorn all configuration comes from **environment variables** (CLI
  flags belong to gunicorn); every engine flag has an env fallback.

There is **no built-in authentication yet** — keep :5050 on localhost or a
trusted network, or front it with an authenticating reverse proxy.

## Environment variables

| Variable | Effect |
|---|---|
| `OPENAI_API_KEY` | LLM access (engine, UI query-refinement, cumulative-KG layer). |
| `ANALYTIC_ENGINE_ENABLED=1` | Accumulate finished investigations into the cumulative KG (equivalent to `--analytic_engine_enabled`; required under gunicorn where CLI flags are unavailable). |
| `INVESTIGATOR_TMFG=1` | Enable TMFG themes + belief propagation (required for the themes / Key-network tabs). |
| `INVESTIGATOR_DISABLE_CACHE=1` | Disable the LLM response cache. |
| `INVESTIGATOR_ASYNC_WORKERS` | NER/extraction concurrency (default 16). Lower it to shrink the memory spike — see below. |
| `INVESTIGATOR_TMFG_UNIFORM_WEIGHTS=1` | Restore topology-only theme weighting (default is evidence-aware). |
| `INVESTIGATOR_UI_MAX_CONCURRENT` | Max concurrent investigations the UI backend runs (default 1). |
| `INVESTIGATOR_CORRO_GAIN` / `INVESTIGATOR_CORRO_CAP` | Multi-source corroboration confidence boost (default 0.35 / 8). |
| `INVESTIGATOR_CLAIM_SIM` / `INVESTIGATOR_SYNDICATION_SIM` | Claim-clustering / syndication thresholds for fact-checking badges (default 0.78 / 0.97). |
| `INVESTIGATOR_KG_LLM_MODEL` | OpenAI model for the cumulative-KG layer (default `gpt-4.1-mini`). |
| `INVESTIGATOR_KG_STORE` | Cumulative-KG store directory (default `~/.local/share/investigator/kg`). |
| `INVESTIGATOR_OPENSANCTIONS_KEY` | API key enabling the OpenSanctions search source. |
| `INVESTIGATOR_GOOGLE_API_KEY` / `INVESTIGATOR_GOOGLE_CSE_ID` | Google Programmable Search for the web-search source (else DuckDuckGo). |
| `INVESTIGATOR_OPENREGISTRY_TOKEN` | Static bearer for OpenRegistry (skips the OAuth login). |
| `INVESTIGATOR_OAUTH_DIR` | Where OpenRegistry tokens are stored (default `~/.config/investigator`). |

## Memory / OOM

The pipeline is memory-heavy. On a single host running the engine + UI backend +
an investigation + an editor, a heavy run can exhaust RAM and swap, and the Linux
OOM-killer terminates the investigation subprocess mid-run (the symptom: the job
shows **failed** with **no Python traceback** and no artifact, so its tabs are
empty).

**Resident baseline (idle):** the engine is legitimately ~2 GB (the
`potion-multilingual-128M` dedup model + PyTorch runtime); the UI backend ~0.8 GB
(WordLlama + the cumulative-KG store + structured sidecar).

**Levers, in order:**

1. **Keep the engine fresh.** A long-running engine accumulates per-session state
   over its lifetime; restart it periodically (or when free memory gets tight). A
   restart reclaims that growth.
2. **Lower NER concurrency.** `INVESTIGATOR_ASYNC_WORKERS=8` (from 16) roughly
   halves the transient spike during Stage-1/2 — each worker holds a payload
   chunk plus a large gpt-4.1 response in flight. Trades throughput for a smaller
   peak.
3. **Don't oversubscribe the box.** The editor/language-server, multiple UI-server
   restarts, and stray background jobs all add up; close what you don't need
   during a heavy run.
4. **Trim the run** only as a last resort — fewer `top-n-entities` and
   `articles-per-entity`, or `enhanced-retrieval` off, dramatically cut the
   Stage-2 payload and the resulting graph size.

> Lighter settings *avoid* OOM; the levers above *resolve* it. Prefer fixing the
> baseline/spike over permanently shrinking the workload.

## Running an investigation from the CLI (no UI)

```sh
PYTHONPATH=.:src python research/cross_event_investigation.py \
  --domain sanctions_evasion --period 30d \
  --event "russia_oil:Russia oil sanctions evasion dark fleet 2026" \
  --event "china_yuan:China yuan settlement Russia trade sanctions 2026" \
  --source wikipedia --source websearch
# then: python research/build_customer_report.py news_investigations/cross_event/<artifact>.json
```

`--no-gnews` runs purely over `--extra-pdf` / `--extra-url` / `--source` inputs —
e.g. analysing a single case file under the `criminal_investigation` domain.

## Tests

Standalone test scripts under `tests/` (each runnable directly):

```sh
PYTHONPATH=.:src python tests/test_structured_store.py
PYTHONPATH=.:src python tests/test_connector.py
# ...etc
```
