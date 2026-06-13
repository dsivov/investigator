# OSINTGraph — Functional Analysis & Productization Roadmap

> Phase 3 deliverable. Analysis only — nothing here is implemented yet.
> Branch state: `refactor/phase-1` (Phase 1 refactor + Phase 2 bug fixes, all
> live-smoke-verified). Baseline preserved on `master` @ `93aaec4`.

---

## 1. What the system does (functional analysis)

OSINTGraph turns a bag of OSINT source material about a subject into a
**triangulated relationship graph** scoped to an investigation question.

### End-to-end flow (`POST /api/v1/get_nodes`)

```
JSON/text payload
   │
   ▼  chunk  (JSON → 2048-char RecursiveJsonSplitter chunks; text → 4000-char)
   ▼  Step 1  NamedEntitiesRecognition (per chunk, async fan-out)
   │            → entities (ORG/PERSON) + affiliations, each relevance-scored 0..1
   ▼  Step 2  Representative identifiers
   │            → cluster identifiers (only if ≥200) then LLM-dedup into canonical names
   ▼  Step 3  Duplicate detection & merge  (semhash + merge_list_of_dicts)
   │            → one record per canonical entity, fields merged across chunks
   ▼  Step 4  Build coarse-grained DiGraph
   │            → edges from chunk affiliations, remapped to canonical ids;
   │              "junction" nodes = entities shared across chunks
   ▼  Step 5  Edge enrichment (GraphEdgesEnrichment, per chunk)
   │            → relation type/context/attributes on each edge
   ▼  Step 6  Evidence mapping + TRIANGULATION
   │            → per-chunk evidence extraction supporting the hypothesis;
   │              evidence nodes attached, nodes marked leaf/hypothesis;
   │              score_graph_by_connectivity() drops low-relevance &
   │              graph-disconnected nodes + orphan edges
   ▼  Step 7  Merge into persisted session state, return {nodes, edges}
```

### Key concepts

- **Triangulation** = relevance filter **+** connectivity filter. A node
  survives only if it clears `relevance_threshold` **and** has a path to the
  most-connected node. This is why a single run on subject-centric data
  returns a small high-confidence core (the smoke test: 3–5 nodes out of a
  78-entity ground truth — by design, not a defect).
- **Stateful, multi-run** ("depth investigation"): results accumulate per
  `session_id` across calls; each run merges into prior state.
- **Evidence → leaf nodes**: nodes with hypothesis-supporting evidence become
  "leaf" nodes; `rescore_evidences` runs a t-test over evidence
  score×confidence to decide whether the support is strong enough to keep.

### Data model
Entity nodes (`identifier`, `type`, `data{...}`, `leaf`, `prob`, `evidence[]`)
and affiliation edges (`src_identifier`, `dst_identifier`, `relations`,
`attributes`). Persisted today as a single JSON file via `coffy.nosql`.

---

## 2. Soundness of the implemented logic

What's **conceptually solid**:
- The chunk → extract → dedup → graph → triangulate arc is a reasonable OSINT
  pipeline; relevance + connectivity dual-filtering is a defensible way to cut
  noise.
- Async fan-out over chunks is the right shape for LLM-bound work.
- Per-session accumulation supports iterative investigation.

What's **fragile or questionable** (candidates for Phase 3 hardening):

| # | Concern | Where | Risk |
|---|---|---|---|
| S1 | **Merge logic is type-dispatch + string-join.** Entity field values get `","`/`":"`-joined into strings; ordering/dtype-dependent. Lossy and hard to reason about. | `dedup.py` merge_list_of_dicts / merge_states / merge_duplicates | Silent data degradation across runs |
| S2 | **Evidence t-test is statistically thin.** `ttest_1samp` over score×confidence with `p<0.3`, special-cased for n==1. Not a sound significance test at these sample sizes. | `operations.py:39` rescore_evidences | Arbitrary leaf-node inclusion |
| S3 | **Identifier clustering only triggers at ≥200 identifiers.** Below that, all identifiers go to one LLM dedup call. | `dedup.py` group_identifiers_for_representative | Large inputs OK; mid inputs send big prompts |
| S4 | **No schema validation on LLM output.** Relies on dspy/pydantic coercion; malformed structures surface as downstream KeyErrors (cf. the C3 bug). | pipeline-wide | Brittle to model drift |
| S5 | **Relevance scores are LLM self-reported**, then thresholded. No calibration. | NamedEntitiesRecognition | Threshold tuning is guesswork |
| S6 | **Not standalone** — hard dependency on the `crewai_mvp`/`tangos_mvp` sibling (`TangosGenericSpecialist`, `Triangulation`, `TangosLogger`). | orchestrator imports | Can't deploy in isolation |

These are *research-grade modeling choices*, not crashes. Whether they matter
depends on how much you trust the output for compliance decisions.

### Prompt / signature logic issues (audit of `llm/signatures.py`)

The signature docstrings *are* the LLM instructions, and (per the data-model
work) they are the data contract. Auditing them surfaced logical problems —
mostly instructions that ask the model for things the output schema can't hold,
or self-contradictory guidance. These are **prompt-engineering fixes**, kept
separate from the data-model refactor; documenting, not changing them yet.

**In the 5 signatures the pipeline actually uses** (`NamedEntitiesRecognition`,
`MostRepresentativeIdentifier`, `GraphEdgesEnrichment`,
`ExtractEvidenceFromJSONText`, `InvestigateEvidenceFromJSONText`):

| # | Issue | Where |
|---|---|---|
| PR1 | Evidence extractors instruct "provide reasoning, confidence, strength, **source and search_url**", but `models.Evidence` has **no `source`/`search_url`** field (only nested `metadata.source`; `search_url` has no home). Model is told to emit data with nowhere to land → dropped/crammed. | signatures.py:57, :81 |
| PR2 | The evidence `score` scale is muddled: titled "relevance" but expressed on a ±1 range with −1 = "strongly irrelevant" (irrelevant should read ~0; negative usually = "contradicts"). And `score` is **never named** in the per-evidence "provide …" list — described but not requested. Downstream `rescore_evidences` filters `score > 0`, so the whole negative half (−0.5/−1) plus 0 collapse to "ignored" → the 5-point scale has 3 effective buckets. | signatures.py:60-65, :84-89; operations.py:39 |
| PR3 | `hypotesis` (bool "supports") and `score` ("relevance") encode two different axes but the prose mixes "support"/"relevance"; relevant-but-contradicting evidence has no clean encoding. (Ties to the data-model `hypotesis` vs entity-flag `hypothesis` distinction.) | signatures.py:51-96 |
| PR4 | `GraphEdgesEnrichment` asks for "search source and search **URL**", but `models.Edge` has only `source` (no `search_url`). It also defines `Relation` as "context, **name of destination entity**, and type" — putting the dest node name into `Relation.name`, **duplicating `Edge.nodeB`** and making `Relation.name`'s meaning ambiguous (relation label vs. endpoint). | signatures.py:109; models.Edge/Relation |
| PR5 | `NamedEntitiesRecognition` is self-contradictory: "Provide complete information for **every** field" vs "**without inference beyond** [context]". The `Entity` schema's `email`/`phone_number`/`address`/`position`/`financial_restrictions` are usually absent from OSINT snippets — "fill every field" + "don't infer" can't both hold → the model guesses or emits filler. | signatures.py:142-143, :151-152 |
| PR6 | Inconsistent score conventions across the pipeline: entity `relevance_score` is **0.00–1.00**, evidence `score` is **−1.0–+1.0**. Two meanings of "score" a reader/threshold-writer must keep straight. | NamedEntitiesRecognition vs Extract/InvestigateEvidence |

**Dead signatures — DELETED** (DATA_MODEL.md step 8): `EvidencesExtractor`,
`FlatJsonToDocument`, `AllLabelsExtraction`, `AnalyzeContradictedEntities`,
`CreateInvestigationExecutionPlan` were exported from `llm/__init__.py` but
unused in the pipeline, so they were removed (along with the orphaned `Label` /
`OrganizationProfile` models). For the record, each had its own rot — e.g.
`EvidencesExtractor`'s single top-level `identifier` output for a
`list[Evidence]`; `CreateInvestigationExecutionPlan`'s embedded, already-stale
copies of the `Entity`/`Evidence`/`Edge` schemas. The sibling `crewai_mvp` keeps
its own copies, unaffected.

→ Remaining: open a separate prompt-fix pass for PR1–PR6 on the **live**
signatures.

---

## 3. Productization roadmap (prioritized)

Tiers by what blocks a real deployment. Each item notes the relevant Phase 2
finding code where applicable. Effort: S<½day · M<2days · L>2days.

### P0 — Blocks any multi-user / real deployment

| Item | Why | Finding | Effort |
|---|---|---|---|
| **Real persistence** (Postgres/SQLite) behind `InvestigationStateRepo` | Today state is one JSON file, wiped on every restart. The repo abstraction is already in place — swap the impl. | M1 | M |
| **Stop wiping state on startup** | `clear_on_start=True` destroys all sessions on restart. Default it off. | M1 | S |
| **Concurrency safety** | `SessionStore` dict + coffy file + global `dspy.configure` are shared, unlocked, across Flask requests. Concurrent same-session calls corrupt state. | M2 | M |
| **AuthN/AuthZ + rate limiting** | Route is fully public; anyone can run unbounded LLM jobs (cost/DoS). Config already carries JWT knobs. | — | M |
| **Production server** | `app.run()` is dev-only. Front with gunicorn (sync workers won't fit `async` routes well) → strongly consider the FastAPI move below. | — | M |

### P1 — Correctness & trust

| Item | Why | Finding | Effort |
|---|---|---|---|
| **Enforce request validation** | `api/schemas.py` pydantic models exist but aren't enforced; bad input silently coerces to defaults. | schemas | S |
| **Stop silent per-chunk data loss** | `extract_entities_from_chunk` returns `[],[],[]` on any exception → chunks vanish with no signal. Surface partial-failure counts in the response. | M3 | S |
| **LLM ret/timeout + output validation** | No retries/timeouts on dspy calls; no validation of structure. Add tenacity + guard rails. | S4 | M |
| **Revisit merge + evidence scoring** | S1/S2 — decide if string-join merge and the t-test are acceptable, or replace with explicit field-merge rules + a defensible confidence aggregation. | S1, S2 | L |

### P2 — Architecture & deployability

| Item | Why | Effort |
|---|---|---|
| **FastAPI migration** | Routes are already `async`; FastAPI gives native async, request validation from the existing schemas, and OpenAPI docs for free. `create_app` factory makes this contained. | L |
| **Decouple from `crewai_mvp`** | Hard sibling-path dependency blocks isolated deploy. Define an interface for the bits actually used (logger, specialist, triangulation) or vendor them. | L |
| **Config hardening** | `config/settings.py` runs `argparse` at import (breaks under pytest/uvicorn workers). Move to `pydantic-settings`. | M |
| **Containerize** | Pin the env that this verification established (Python 3.12 + the dep set incl. `flask[async]`, `sentence_transformers`, `python-Levenshtein`). Dockerfile + lockfile. | M |

### P3 — Functionality & operability

| Item | Why | Effort |
|---|---|---|
| **PDF ingestion** | You named this as core ("get json/pdf"), but the route only accepts JSON/text today. Add a PDF→text/JSON front-end step. | M |
| **Observability** | Replace the `/tmp/graph_nodes_log.csv` append-per-request hack (M4) with structured logging + metrics (latency, LLM cost, nodes/run, error rate). | M4 | S–M |
| **Cost controls** | Each request fans out many gpt-4.1 calls over every chunk. Add caching (dspy disk cache is currently *off*), token budgets, and chunk caps. | M |
| **Graph visualization endpoint** | `visualize_graph` exists but is unwired; expose it (README roadmap item). | S |
| **Regression test suite** | Turn `tests/run_smoke.py` into a proper pytest suite with recorded fixtures + tolerance assertions; fix `evaluate_tangraph_server.py` (L2 trailing-comma + hardcoded `/home/dimas` paths). | M |
| **Lint debt** | `analytics/` carries 27 baseline pyflakes warnings (L3); `dedup.py` uses `type() is` vs `isinstance` (L4). | S |

---

## 4. Suggested sequencing

1. **P0 first** — persistence + no-wipe + concurrency + auth + prod server. Without these it cannot serve real users safely.
2. **P1** alongside — validation, partial-failure visibility, retries. Cheap, high trust-payoff.
3. **P2 FastAPI move** as the anchor refactor once P0/P1 settle — it naturally absorbs validation (P1), the dev-server problem (P0), and config hardening.
4. **P3** opportunistically — PDF ingestion is the biggest *functional* gap vs. your stated goal; the rest is operability.

**Single highest-leverage next step:** P0 persistence + concurrency, because
they're the difference between a single-user research script and a service —
and the `InvestigationStateRepo` seam from Phase 1 already makes the DB swap
local to one file.

---

## 5. Deferred bugs (M1–M4) — status as of v1.0.0

The Phase 2 bug scan flagged four issues labelled M1–M4. None were fixed in
the productization push that tagged `v1.0.0`; they were deferred because they
are deployment-hardening concerns, not pipeline-correctness ones. Each is
already referenced in the prioritized tiers above; this section is a single
place to check their status before pointing real traffic at the service.

| # | Issue | Real-world severity | Bites you when | Tier |
|---|---|---|---|---|
| **M1** | Coffy state wipes on every server restart (`clear_on_start=True`); the JSON-file store has no rotation, no migration, no schema. | **High for production.** Low for dev. | A real user resumes a session after you redeploy → their investigation history is gone. | P0 |
| **M2** | No concurrency control on shared `SessionStore` dict / coffy file / global `dspy.configure(lm=...)` across Flask requests. | **High for multi-user.** Low for single-user. | Two concurrent requests touch the same session, or step on each other's dspy LM config → state corruption, intermittent races. | P0 |
| **M3** | `extract_entities_from_chunk` catches any exception and returns `[], [], []`. The pipeline reports `status: success`. | **Medium — quality, not a crash.** | A chunk fails (timeout, parse error, rate limit) → vanishes silently; F1 quietly degrades; you cannot tell from outside which chunks failed. | P1 |
| **M4** | `_append_csv_audit` appends to `/tmp/graph_nodes_log.csv` per request with no rotation. | **Low — disk hygiene.** | Long-running server → disk fills up. Trivially mitigated (logrotate / delete / replace with structured logging per P3). | P3 |

**Summary** — For the current dev / single-user / research usage these are not
critical (that's why they were deferred). For real deployment with users,
**M1 and M2 are blockers**, M3 is a quality issue worth surfacing in monitoring,
and M4 is hygiene. The `v1.0.0` tag is honest about *pipeline correctness and
clarity* — that's what the productization push earned — not about deployment
hardening, which is the M1+M2 work that still needs to land.
