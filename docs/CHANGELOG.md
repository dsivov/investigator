# Changelog

Notable changes, most recent first. Each entry notes the commit, roadmap
reference where applicable, and — for this project's Context Graph pilot — the
governing ADR / ChangeRequest recorded in CG.

---

## 2026-06-30 → 2026-07-04 — Platform hardening + product-research support

A push that (a) cleared the top of the productization roadmap (P0 blockers M1/M2
and the P1 correctness/trust items) and (b) added optional product-research
support, all governed through the Context Graph methodology (ADR + ChangeRequest
per change).

### Platform hardening (roadmap P0 / P1)

- **Durable SQLite session state** — `68c3844` · roadmap **M1 / P0** · CG:
  `adr-sqlite-session-state`, `cr-durable-session-state`.
  Replaced the coffy JSON-file `InvestigationStateRepo` (wiped on every restart,
  in `/tmp`) with `SqliteInvestigationStateRepo`: same `find/add/update/get_field`
  interface, durable file under the XDG data dir (`INVESTIGATOR_STATE_DB`), WAL +
  write lock, **no-wipe by default** (`INVESTIGATOR_CLEAR_ON_START` to opt back
  in), best-effort migration from the legacy store. Validated: a live huione run
  persisted a 6.9 MB session that survives a restart.

- **Per-session concurrency lock** — `7846fbd` · roadmap **M2 / P0** · CG:
  `adr-m2-concurrency`, `cr-m2-concurrency`.
  A per-`session_id` `threading.Lock` serialises the stateful pipeline run
  (load → merge → save) so concurrent same-session requests can't clobber each
  other; distinct sessions still run in parallel. `SessionStore` is lock-guarded.
  Scoping note: dspy LM config is already per-call via `dspy.context`, so the
  roadmap's "global dspy race" concern was already mitigated.

- **Request validation** — `85d7c22` · roadmap **P1** · CG: `adr-p1-correctness`,
  `cr-p1-correctness`.
  `POST /get_nodes` now validates the body against the existing `GetNodesRequest`
  schema and returns **400 with field-level errors** instead of silently coercing
  bad input to defaults. Validated as a gate (extras like `run` still pass through).

- **Partial-failure visibility** — `85d7c22` · roadmap **P1 / M3**.
  `extract_entities_from_chunk` swallowed per-chunk exceptions and returned empty,
  so failed chunks vanished while the run reported success. Failures are now
  counted and surfaced as an **`extraction: {chunks, extracted, failed}`** block
  in the response, plus a warning log.

- **LLM resilience** — `1d09662` · roadmap **P1 / S4** · CG: `adr-llm-resilience`,
  `cr-llm-resilience`. *(branch `feat/llm-resilience` — pending merge)*
  `dspy.LM` instances now carry an env-tunable per-call **timeout**
  (`INVESTIGATOR_LLM_TIMEOUT`, default 90s) and **`num_retries`**
  (`INVESTIGATOR_LLM_RETRIES`, default 3; litellm exponential backoff on transient
  429/5xx/network). Bounds hung calls and auto-recovers transient failures that
  used to drop a chunk. The inline per-call LM reuses the resilient `extraction_lm`.

### Product-research support (optional, isolated)

- **`product_research` domain preset** — `9823c47` · CG: backfilled decision.
  A candidate-fit relevance hypothesis ("is this a choosable product that fits the
  stated need/platform/constraints?") that down-ranks retailers, review sites,
  accessories, and chips. A/B on an Android-tablet query moved the top list from
  Apple accessories + retailers to real competing products and trimmed the graph
  ~40% (386 → 237 entities).

- **Brand exclusion for product queries** — `65549c2` · CG: backfilled decision.
  For `product_research` queries asking for "alternatives to X", the pipeline
  re-roots off any excluded brand chosen as the graph root and demotes its
  entities (Apple #1 → #375). **Domain-gated to `product_research` — the OSINT
  platform is byte-for-byte unaffected.** A theme+prob-ranked anchor (v2) was
  tried and **reverted** (it re-rooted onto a chip/phone brand, importing
  carrier/geo noise); lesson: single-node re-rooting imports the anchor's orbit —
  reliable exclusion needs multi-seed relevance (deferred).

### Analysis & docs

- **Hypothesis assessment in analysis reports** — `41617c2` / `8894291`.
  The Connections and Key-network "Analyse" reports now close with a
  **`## Hypothesis assessment`** section: an ICD-203 confidence verdict on whether
  the selected network supports the investigation's domain hypothesis, citing the
  driving edges. Backend-only; both views share the `/connect/analyze` endpoint.

- **"The Hidden Thread" blog** — `37b04c1`.
  A self-contained, illustrated user-facing explainer (`docs/THE_HIDDEN_THREAD.html`)
  of the analysis — bridges, themes, brokers, corroboration, the domain-hypothesis
  verdict — published to GitHub Pages.

### Reproducibility

- **Dependency manifest fixes** — `ac9ccd2`.
  Declared the Stage-1 news-fetch dependencies that were README prerequisites but
  missing from `pyproject.toml` (`gnews`, `newspaper3k`, `googlenewsdecoder`,
  `pymupdf`, `lxml_html_clean`), and pinned `lightrag-hku >=1.4,<1.5` (1.5.x drops
  `lightrag.constants.DEFAULT_HISTORY_TURNS`, which the code imports). A clean
  `pip install -e .` now sets up a working engine.

### Process — Context Graph pilot

First real agent run against the project's Context Graph. Every change above was
worked query-first (check for existing modules) and recorded as an ADR +
ChangeRequest through the governed actions. Field feedback (six issues found, the
real bugs fixed by the CG team, then verified) and a dev-process value assessment
are in [`cg/FEEDBACK.md`](../cg/FEEDBACK.md).

---

## Roadmap status after this push

- ✅ **P0** — M1 (durable state), M2 (concurrency)
- ✅ **P1 correctness/trust** — request validation, partial-failure visibility,
  LLM retry/timeout
- ⏳ **Remaining P0** — auth + rate limiting, production server (gunicorn/FastAPI)
- ⏳ **Remaining P1** — revisit merge + evidence scoring (S1/S2, the larger item)
