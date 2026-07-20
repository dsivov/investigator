# Handoff — project state and backlog (2026-07-20)

The single continuation point for any agent (cohermes/Hermes, Claude Code) or
human picking up this project. Written at the switch to the cohermes
team-agent methodology; supersedes nothing — roadmap, operations, and the
changelog stay authoritative for their own areas. Update this file when the
picture changes materially.

## Where the project stands

- **`main` @ `8ab75e7`** (pushed), tag `v1.1.0` + the monitor-phase-2 work.
  `feat/claim-investigation` is merged and deleted (local + origin).
- Last shipped: **CEP alert-once** (`96fa157`) — pattern-chain collapsing per
  (rule, final event) + persistent fired-pattern state (`fired_patterns.json`
  in the KG store), `patternsSeen` context in digests, UI badges. Verified by
  `tests/test_cep_state.py` + a two-pass e2e digest run on an isolated store.
- Roadmap P0 (M1 durable state, M2 concurrency, production serving, auth) is
  **closed**. See `roadmap.md` for the full status; `CHANGELOG.md` for history.

## Context Graph (the project's decision memory)

- Server `http://localhost:9621`, **workspace `investigator`** — this exact
  workspace holds all recorded CRs/ADRs; anything else starts amnesiac.
- Protocol: `cg/AGENT_CG_GUIDE.md` (query-before-build → record-the-why →
  verify writes with `/graph/entity/exists`). Hard-won lessons in
  `cg/FEEDBACK.md` — notably: if MCP tools aren't in the session, the REST
  surface with the `LIGHTRAG-WORKSPACE: investigator` header is equivalent
  (write path verified fixed 2026-07-12; still confirm writes with
  `entity/exists`).
- Latest governance: `cr-monitor-cep-state` (closed), `adr-cep-chain-signature`
  (the chain-identity decision + rejected alternatives).
- Pending server-side: delete probe node `adr-cg-writepath-probe-0712`
  (no delete action exists in the manifest).

## Running the stack

`docs/operations.md` is the reference. As of this handoff three processes are
**live** (started 2026-07-20, `nohup`, they survive session exit):
engine gunicorn on `127.0.0.1:5003` (keep localhost — no auth), UI backend
gunicorn on `0.0.0.0:5050` serving the built bundle, Vite dev frontend on
`:5180`. Use the project `.venv` (`/storage/Work/investigator/.venv`) — the
conda env has an incompatible lightrag; the numpy-1.x warning spew from
nltk/numexpr/bottleneck at import time is known noise, not a failure.

## Backlog (prioritized)

| Pri | Item | Where / notes |
|---|---|---|
| P1 | **Prompt-fix pass PR1–PR6** on the five live LLM signatures (schema/prompt contradictions, muddled score scales). Explicitly left open in `roadmap.md` §2. | `src/investigator/llm/signatures.py` |
| P1 | **M3 — silent chunk failures**: `extract_entities_from_chunk` swallows exceptions and reports success; surface per-chunk failure in monitoring. | pipeline / orchestrator |
| P2 | **Monitor phase 3 candidates**: fired-state management UI (inspect/reset `fired_patterns.json`), expiry policy for stale fired signatures, locking if the digest ever gets a second writer. | `src/investigator/monitor/` |
| P2 | **S3–S5 fragile modeling choices**: identifier-clustering threshold, no LLM-output schema validation, uncalibrated self-reported relevance. | `roadmap.md` §2 table |
| P3 | **PDF ingestion** — biggest functional gap vs. the stated goal. | Stage-1 intake |
| P3 | **M4** — `/tmp/graph_nodes_log.csv` grows unbounded; logrotate or structured logging. | `_append_csv_audit` |

## Session-restore ritual (what "restore our session" means)

Reconstruct from: working tree (`git status`/`diff`) → this file → `CHANGELOG.md`
→ CG queries (`query_auto` / REST `/query` mode `mix`). Then verify any
in-flight work's tests before continuing.
