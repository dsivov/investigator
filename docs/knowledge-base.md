# The cumulative knowledge base

With the analytics engine enabled (`--analytic_engine_enabled`), every finished
investigation's graph accumulates into **one persistent knowledge graph** that
later investigations can draw on, and that you can query directly from the
**Knowledge Base** tab.

It is built **in-code on LightRAG** (no separate server process), and lives
outside the code tree at `~/.local/share/investigator/kg` (override with
`INVESTIGATOR_KG_STORE`). Code: `src/investigator/analytics/`.

## How a graph is merged in

When an investigation finishes, `CumulativeKG.merge_graph(final_graph, source_id)`:

1. **Canonicalizes** every entity name to a stable global canonical (see below).
2. Feeds the canonicalized nodes/edges to LightRAG's `merge_nodes_and_edges` —
   the *merge* path (not `insert_custom_kg`, which overwrites by name). LightRAG
   merges by exact name: unions `source_id`/`file_path`, votes `entity_type`,
   concatenates + LLM-summarizes descriptions, merges edges.
3. Writes the **structured + temporal sidecar** (below), which preserves
   everything LightRAG's fixed schema drops.

All LightRAG work runs on **one dedicated background asyncio loop** in a daemon
thread (Flask runs each request in its own loop, and LightRAG caches asyncio
locks in process globals bound to their creating loop — so a single long-lived
instance must pin all its work to one loop; this also serializes writes).

## Cross-investigation canonicalization

`canonicalizer.py::CanonicalRegistry` maps each incoming entity name to a stable
global canonical, so the same real-world entity does not fragment across runs.
It is **conservative** — cross-investigation merges are sticky and hard to undo:

- **auto-merge** only exact (case-insensitive) and normalized-key (case /
  punctuation / whitespace) matches, e.g. `U.S.` ↔ `US`, `SEOHEE` ↔ `SEO HEE`;
- **fuzzy / structural-subset** matches are written to a **review log** and the
  name is registered as its own canonical — never auto-fused. (The auto-rules
  cannot tell `DEMOCRATIC PARTY` ~ `DEMOCRATS PARTY` [same] from `JAMES COMER` ~
  `JAMES COMEY` [different]; those are deferred, not guessed.)

## What LightRAG keeps — and what it drops

LightRAG's graph stores only a **fixed schema**: per node `name / type /
description / source`; per edge `weight / keywords / description / source`. The
merge has **no text chunks** (we feed a pre-built graph, not article text). So
two things matter:

1. The only substance LightRAG can retrieve on is the **description text** — so
   we fold the high-signal facts into it (see "Descriptions drive retrieval").
2. Every other structured property would be lost, so a **sidecar store**
   preserves it.

### The structured + temporal sidecar — `structured_store.py`

`StructuredStore` (persisted as `structured_store.json` next to the LightRAG
store, keyed by the **same canonical names**) preserves and merges across
investigations everything the KG schema drops:

**Per entity**

- belief scores `prob`, `score`, `posterior_prob`, and `posterior_delta` (the
  belief-propagation impact shift) — kept as the max seen, with a
  per-investigation breakdown;
- the full `evidence` list (reasoning + confidence + strength + polarity +
  source URL), deduped;
- `labels` (aliases), `runs` (which investigations attested it), `themes`,
  source URLs, and structured attributes `position`, `location`, `address`,
  `email`, `phone_number`, `financial_restrictions`;
- a **timeline**: the entity's own `timeline_events` plus the dated events it
  participated in, assembled into one chronology with a first/last-seen range.

**Per edge**

- relation `type` + `context`, the relationship **role** (the nature of the
  link, e.g. "longtime media consultant"), the per-edge citation URLs,
  `is_hypothesis` (TMFG fill-in vs attested), `weight`, runs, investigations;
- **time intervals** (merged across investigations): `observed_dates` — the
  article publication dates that asserted the edge, kept as a *set* (so a later
  consistency pass can spot conflicts); and `active_window` `[start, end]` — the
  global span of the dated events both endpoints share. See
  [data-model.md](data-model.md#5-temporal-layer).

**Temporal layer**

- dated **event records** (with their canonical participants), and the
  **event→event ordering edges** (`event_followed_by` / `event_coincident`) —
  all of which LightRAG strips on merge.

This is the foundation for the monitoring / impact direction (timelines +
`posterior_delta` are exactly what that needs — see
[cep-monitoring-discussion.html](cep-monitoring-discussion.html)).

### Timeline conflicts (consistency check)

Because dates are kept as *sets* across runs, `temporal_conflicts()` (→
`GET /api/kb/conflicts`, surfaced as a **Timeline conflicts** panel on the KB tab)
flags where they disagree: an event whose dates can't be reconciled within
tolerance, or an ordering that contradicts the dates. A disagreement usually means
sources conflict, an extraction erred, or two real-world things were merged into
one canonical — a data-quality lead, not a resolution. See
[data-model.md](data-model.md#5-temporal-layer).

## Querying — the Knowledge Base tab

`POST /api/kb/query {query, mode?, synthesize?}` returns, for a question asked
across **everything** seen in all investigations:

- **structured entities + relationships** (always), each entity joined to its
  full sidecar record — belief score, evidence, sources, investigations, and the
  dated **timeline**;
- an optional **LLM-synthesized answer**.

### Retrieval modes (measured, per endpoint)

LightRAG offers `local` (entity-anchored, queries the entity vector index),
`global` (theme-anchored, queries the relationship index), `hybrid` (both), and
`mix` (+ chunks — empty here). One store, one set of indexes; the mode only
changes *which* index a query hits — there is no separate indexing.

A measurement harness (`research/kg_mode_analysis.py`) over the accumulated store
found:

- chunks are always empty (the in-code merge stores no article text), so `naive`
  is dead and `mix` ≈ churn;
- **`hybrid`** is the broadest, most faithful retrieval and (crucially) is the
  only mode that reliably finds an entity-lookup query ("who is X?") — `global`
  misses the entity's own node.

So the Knowledge Base uses **`hybrid`** for both the structured data and the
answer. (An explicit `mode` in the request overrides it.)

### Descriptions drive retrieval

Because there are no chunks, retrieval and synthesis only see the **description
text** LightRAG embeds. So `cumulative_kg._entity_description` folds the
high-signal structure into it: role/position, location, identifiers (address /
email / phone / financial restrictions), aliases, the dated **timeline**, and the
entity's evidence sentences; edge descriptions include the **role**. This is what
lets a query like *"which organization was banned by Germany in 2024?"* retrieve
**Samidoun** through its embedded timeline, without naming it.

> Heavily-merged entities (present in many investigations) get their concatenated
> descriptions LLM-summarized by LightRAG, which may drop the literal "Timeline:"
> label but keeps the facts. Lightly-merged entities keep the full literal text.

## Pre-seeding new investigations

When a new investigation starts, the orchestrator retrieves what the cumulative
KG already knows about its subject (`retrieve(subject, mode="hybrid")`) and
surfaces it as `prior_context` on the response — read-only, alongside the fresh
findings (it does not perturb the run's own scoring).

## Research / operational tooling

| Script | Purpose |
|---|---|
| `research/kg_retrieval_explore.py` | Load N investigations into a store, validate counts, compare retrieval modes/endpoints. Used to (re-)ingest the store with `--reset`. |
| `research/kg_mode_analysis.py` | Measure retrieval mode × endpoint usefulness (volume, overlap, grounding, latency). |
| `research/kg_merge_prototype.py` | Minimal in-code `merge_nodes_and_edges` validation. |
