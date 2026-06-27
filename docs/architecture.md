# Architecture

Investigator runs as **three processes**.

```
 ┌────────────────────┐   /api proxy ┌──────────────────────┐  HTTP POST   ┌─────────────────────────┐
 │  Frontend (Svelte) │ ───────────▶ │  UI backend (Flask)  │ ───────────▶ │  Pipeline engine        │
 │  Vite dev :5180    │              │  ui/server.py :5050  │              │  python -m investigator │
 │  graph / KB / data │ ◀─────────── │  jobs + reports + KG │ ◀─────────── │  :5003                  │
 └────────────────────┘   JSON/SSE   └──────────────────────┘  graph JSON  │  NER · graph · TMFG · BP│
                                                                            └─────────────────────────┘
```

## Pipeline engine — `python -m investigator` (port 5003)

The core. A Flask service exposing `POST /api/v1/get_nodes`. It performs entity +
event extraction (dspy + GPT-4.1), evidence consolidation, graph construction, the
corroboration filter, TMFG triangulation, and junction-tree belief propagation
(see [pipeline.md](pipeline.md)). It holds the heavy static models
(`semhash`/potion-multilingual for dedup, WordLlama for similarity).

With `--analytic_engine_enabled` it also builds the cumulative knowledge graph:
every finished investigation is merged into one persistent store, and new
investigations are pre-seeded with what the KG already knows about their subject
(see [knowledge-base.md](knowledge-base.md)).

## UI backend — `ui/server.py` (port 5050)

A thin Flask service that:

- runs investigations as **subprocesses** of `research/cross_event_investigation.py`
  (a job queue with SSE progress streaming),
- generates the customer report and serves the Cytoscape-ready graph / theme
  payloads (via the `research/build_*_prototype.py` modules),
- serves the analysis endpoints — Connections, Key network, Knowledge Base query,
  search-source listing, enrichment, and the OpenRegistry login flow.

It loads its own copy of WordLlama (claim corroboration, connectors) and lazily
opens the cumulative-KG store for Knowledge Base queries. The full HTTP contract
is in [ui-api.md](ui-api.md).

## Frontend — `ui/` (Svelte 5 + Vite, port 5180)

The investigator UI. A New-Investigation wizard (domain-aware query refinement, a
vetoable review step, a Sources step for search sources + your own PDFs/URLs),
live progress, and the per-investigation tabs:

- **Overview / Graph / TMFG themes / Data / Report / Sources**
- **Key network** — the automatic theme+bridge skeleton with brokers
- on-demand **Connections** analysis (select entities → hidden-relationship subgraph + LLM summary)
- per-actor / per-evidence **corroboration** badges

plus app-level **Knowledge Base** and **Settings** pages.

The Vite dev server proxies `/api` to the UI backend and binds all interfaces
(`host: true`) so it is reachable across the LAN.

## Data locations

| What | Where |
|---|---|
| Investigation artifacts + job state | `news_investigations/` (git-ignored) |
| Cumulative KG store (graph + vector DBs + structured/temporal sidecar) | `~/.local/share/investigator/kg` (override: `INVESTIGATOR_KG_STORE`) |
| OpenRegistry OAuth tokens | `~/.config/investigator/openregistry_oauth.json` |

See [operations.md](operations.md) for how to run all three and tune memory.
