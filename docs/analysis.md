# Interactive analysis

Three analysis features sit on top of the graph: **Connections** (manual
relationship discovery), **Key network** (the automatic version), and
**corroboration** (fact-checking). Code: `src/investigator/graph/connector.py`,
`src/investigator/graph/corroboration.py`.

---

## Connections — hidden relationships between selected entities

Select any set of actors/events in the **Data** tab and build a focused subgraph
of how they interconnect (`POST /api/investigations/<id>/connect`). Three modes:

- **Shortest path** — one shortest path between each selected pair, plus the
  intermediary ("connector") entities it passes through.
- **Hidden (indirect)** — up to *k* shortest **simple** paths per pair (Yen's
  algorithm), so non-obvious multi-hop chains surface **even when a direct edge
  already exists**. This is the interesting mode.
- **Direct only** — only the edges directly among the selection.

Two design points make it meaningful:

- **Pathfinding runs on relationship edges only, undirected.** The structural
  `evidence → relevance-root` hub edges are excluded — otherwise every pair would
  connect through the root in ~2 hops and the connector would be meaningless.
- **Brokers.** In every mode the intermediary nodes are ranked by **betweenness**
  within the resulting subgraph; the central ones are flagged `isBroker` — the
  entities that actually bind the selection together.

The endpoint returns the subgraph (reusing the graph payload shape), the
`brokers`, and the computed `paths` (one entry per selected pair, with the
explicit node chain).

> **Example (Netanyahu run).** Select **Arnon Milchan** and **Shaul Elovitch**.
> *Shortest path* shows only `Milchan → Netanyahu → Elovitch`. *Hidden* mode also
> surfaces `… → Bezeq → Elovitch` and `… → Walla! News → Elovitch` (the Case-4000
> entities) and flags **Netanyahu** as the broker.

### Analyse

`POST /api/investigations/<id>/connect/analyze` submits **only the connected
nodes** (actors + events with at least one edge), their evidence, and the
computed paths to the LLM, which writes a short, evidence-grounded report on how
the selected entities interconnect — naming each path/chain and what each broker
bridges. The prompt is steered to the *relationship structure*, not per-entity
summaries.

---

## Key network — the automatic representative subgraph

The **Key network** tab (`GET /api/investigations/<id>/key-network`) runs the
hidden-connections algorithm with **no manual selection**. It seeds the connector
with the investigation's most-relevant nodes:

- **theme members** — the evidence-weighted TMFG 4-clique actors, and
- **bridges** — actors attested in more than one cross-event run,

falling back to top-score entities (single-query runs have no bridges) and capped
for safety. It then surfaces the **brokers** that stitch the otherwise-isolated
themes into one skeleton — the connective tissue the TMFG-themes view does not
show — and offers one-click **Analyse** for a written summary of the whole
investigation's structure.

> On a real Netanyahu run, 21 theme nodes resolve to a single broker — the
> **Attorney-General's Office** — binding the clusters. Quality tracks the
> upstream themes/bridges: a thin run yields a thin skeleton (honest signal).

---

## Corroboration — multi-source fact-checking

Corroboration is surfaced at **two distinct layers**. Keep them separate.

### 1. Entity-breadth confidence boost (pipeline)

`graph.operations.assess_evidence` / `evidence_probability`: after the signed,
confidence-weighted evidence signal, its magnitude is sharpened toward certainty
when **independent sources** attest the same conclusion:

```
factor = 1 + CORRO_GAIN · log2(min(distinct_sources_on_the_winning_side, CORRO_CAP))
```

A single source (or unattributed evidence) leaves the result unchanged; same
source counted once. Defaults `CORRO_GAIN=0.35`, `CORRO_CAP=8` (env-tunable).
This flows into `prob` → `score` → belief-propagation priors.

### 2. Claim-level fact-checking badge (UI postprocessing)

`graph.corroboration.corroborate(evidences)` is computed at **read time** (so it
works on any artifact without re-running). It clusters an entity's evidence into
distinct **claims** by WordLlama similarity (paraphrases count together; exact
match would miss them), counts **independent sources per claim**, and collapses
near-identical / **syndicated** copies to one source (a wire story reprinted by
20 outlets is not 20 confirmations). It reports, per actor *and* per evidence row:

- **weak** (1 independent source), **moderate** (2), **strong** (3+),
- the best-corroborated claim text and how many claims are corroborated.

The **Actors** view shows each actor's best-corroborated claim; the **Evidence**
view shows the corroboration of each individual piece of evidence. Thresholds are
env-tunable (`INVESTIGATOR_CLAIM_SIM` = 0.78, `INVESTIGATOR_SYNDICATION_SIM` =
0.97).

> The two layers answer different questions: layer 1 = "how attested is this
> actor overall" (drives ranking); layer 2 = "how many independent sources
> confirm this specific claim" (the rigorous fact-check shown in the UI). On a
> real run, claim-level "strong" is much stricter than entity-breadth — e.g.
> Netanyahu: 94 mentions but ~22 independent sources on his best claim.
