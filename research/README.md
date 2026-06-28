# `research/` — runnable drivers, report generators & research tooling

This folder is the **scripts layer** that sits on top of the engine. The split:

- **`src/investigator/`** is the **engine** — an importable Python package and a
  Flask service (`python -m investigator`, port 5003). It turns a batch of
  articles into a scored, triangulated graph. It does *not* fetch news, drive
  multi-query runs, generate reports, or render UIs.
- **`research/`** is where you **actually run things**: fetch news and drive an
  investigation end-to-end, enrich and report on the result, prototype UIs, and
  explore the cumulative knowledge graph.

The name is historical — the project began as a research codebase. In practice
this folder holds both the **operational CLI drivers** the product depends on and
genuine **research/experiment scripts**. (For the full picture see the
[top-level README](../README.md) and [`docs/`](../docs/README.md).)

## Conventions

- **Run from the repo root** with both the repo and `src` on the path:
  ```sh
  PYTHONPATH=.:src python research/<script>.py …
  ```
- Scripts that *drive an investigation* (e.g. `cross_event_investigation.py`)
  **POST to the running engine** — start `python -m investigator` (port 5003)
  first. Scripts that operate on a saved artifact JSON, or on the cumulative KG,
  run standalone.
- LLM-backed scripts need `OPENAI_API_KEY` (loaded from a repo-root `.env`).

## Start here — run an investigation

```sh
# 1. start the engine (separate terminal)
PYTHONPATH=src:. python -m investigator

# 2. run a cross-event investigation (fetches news, POSTs to the engine,
#    writes an artifact under news_investigations/cross_event/)
PYTHONPATH=.:src python research/cross_event_investigation.py \
  --domain terror_financing --period 1y \
  --event "story_a:first search query" \
  --event "story_b:second search query"
```

Each `--event` is one `tag:query` thread; the engine fuses them into one graph and
finds the cross-story bridges. The resulting artifact JSON is what the UI, the
report generators, and the cumulative KG all consume.

## What's in here

### Investigation drivers (need the engine running)

| Script | Role |
|---|---|
| `cross_event_investigation.py` | **Primary entry point** — multi-query run: fetch news per thread, POST to the engine, assemble the cross-event artifact + analytics. |
| `gnews_deep_investigation.py` | Single-subject two-stage GNews runner; `cross_event` reuses its fetch/payload/picker helpers. |

### Sources, retrieval & enrichment (engine-side helpers)

| Script | Role |
|---|---|
| `search_sources.py` | Pluggable extra sources — Wikipedia / GDELT / OpenSanctions / web search. |
| `source_ingest.py` | Bring-your-own documents: turn URLs + PDFs into the engine's input shape. |
| `enhanced_retrieval.py` | Opt-in query expansion + rerank + entity-deepening for the fetch step. |
| `enrichment.py` | Post-run entity enrichment against SEC EDGAR / OpenRegistry (+ the OpenRegistry OAuth login). Standalone; runs on an artifact. |
| `domain_presets.py` | Per-domain relevance hypotheses (`--domain` bundles). |

### Reports & graph payloads (run on an artifact JSON)

| Script | Role |
|---|---|
| `build_customer_report.py` | Analyst-grade markdown report with sourced findings. |
| `build_graph_prototype.py` | The Cytoscape graph payload (`_payload`) — also imported by the UI backend for the Graph tab. |
| `build_analyst_report.py` | Analyst-style report from a deep-investigation JSON. |
| `osint_analyst_review.py` | OSINT-analyst-perspective markdown review of a cross-event artifact. |
| `build_fake_report.py` | Report from the anonymised/fictionalised crime-report fixture (a demo input). |

### Standalone prototypes (single self-contained HTML, no server)

| Script | Role |
|---|---|
| `build_tmfg_prototype.py` | TMFG-themes tab prototype. |
| `build_full_ui_prototype.py` | The whole six-tab UI in one HTML file. |
| `build_blog_post.py` | Generates the illustrated blog post (`blog_post_finding_threads_in_news.html`). |

### Cumulative-KG tooling

| Script | Role |
|---|---|
| `kg_retrieval_explore.py` | Load N artifacts into a LightRAG store and validate / compare retrieval; the **(re-)ingest** tool (`--reset`). |
| `kg_mode_analysis.py` | Measure which retrieval mode (local/global/hybrid/mix) is most useful. |
| `kg_merge_prototype.py` | Minimal in-code `merge_nodes_and_edges` validation. |
| `kg_canonicalizer.py` | Cross-investigation canonicalization experiments. |

### Experiments & demos

| Script | Role |
|---|---|
| `bp_synthetic_demo.py` | Synthetic demo of the signed junction-tree belief propagation. |
| `two_stage_experiment.py` | An early two-stage investigation experiment. |

### Design & analysis notes (not code)

`analyst_report_playbook.md`, `investigator_analysis_2stage.md`,
`investigator_analysis_news.md`, `investigator_response_format.md` — early design
/ analysis write-ups, kept for provenance. `blog_post_finding_threads_in_news.html`
is the rendered blog post. The current, maintained documentation lives in
[`docs/`](../docs/README.md).
