# OSINTGraph — Triangulation Core Review

Stage-by-stage logical analysis of the investigation pipeline's core
(`NER → dedup → graph construction → triangulation`), each backed by unit tests
and validated against a pinned golden snapshot of a real run.

> Scope: the `_standard_pipeline` path in `pipeline/orchestrator.py` and the
> functions it calls in `graph/dedup.py` + `graph/operations.py`.

## Methodology (per stage)

1. **Define the target functionality** (collaboratively) — the stage's spec:
   what it must do, its input/output contract, and the invariants it must hold.
2. **Analyse the input and predict the output** that the spec implies — *before*
   looking at what the code actually produces.
3. **Run the stage** on the real golden input (deterministic, via the pinned
   snapshot in §0).
4. **Diff predicted vs actual** — each discrepancy is a finding (a bug, a spec
   gap, or a mis-prediction). Fix the real bugs; record the rest.

The prediction-first step is the point: bugs hide in the gap between "what the
spec says should happen" and "what the code does."

---

## 0. The golden oracle (data validation foundation)

`tests/capture_golden.py` runs the Exampleorg fixture **once** through the live
LLM, monkeypatching every stage function in the orchestrator to record its real
inputs and outputs, and writes `tests/fixtures/golden_stages.json.gz` (~0.8 MB
gzipped). Because the LLM-backed boundaries are now frozen, every *deterministic*
transform downstream (dedup, graph build, register_edges, the triangulation
mapping, merge) can be re-run on the captured inputs and asserted **exactly** —
the non-stochastic oracle the consolidator refactor needs.

Snapshot keys (one Exampleorg run): `chunks` (40), `ner_nodes` (158),
`representatives` (71), `dedup_nodes` (70), `build_graph_*`, `enrichment_edges`
(93), `evidences_extract`/`evidences_investigate`, `consolidator_in_*` /
`consolidator_out_nodes`, `score_in_*` / `score_out_*`, `response` (8 nodes /
10 edges).

Stage counts down the pipeline (this run):

```
40 chunks
  → 158 raw entity records   (NER; only 86 distinct identifiers)
  → 71 representative groups  (representative-identifier resolution)
  → 70 dedup'd nodes          (SemHash grouping/merge)
  → graph: 38 nodes / 93 coarse edges
  → 69 registered edges       (enrichment + endpoint resolution)
  → triangulation → 8 nodes / 10 edges returned
```

Regenerate after intentional pipeline changes:
`PYTHONPATH=.:src:/home/dsivov/Work/tangos_mvp <tangos-python> tests/capture_golden.py`.

---

## 1. Stage 1 — NER (named-entity + affiliation extraction)

**Code:** `named_entities_extractor_task` → `extract_entities_from_chunk` →
`get_entities` (`NamedEntitiesRecognition` signature). Chunking:
`pipeline/chunking.py`.

### 1.1 Target functionality (CONFIRMED 2026-05-29)

**Purpose.** Turn raw investigation JSON into a flat set of *named* entity
records + explicit affiliations, each **grounded only in the source text**. NER
must **extract ALL named PERSON/ORG entities — no relevance filtering at this
stage** (relevance gating is the *triangulator's* job, Stage 4). `relevance_score`
is *computed and carried* for Stage 4 but must never cause an entity to be
dropped in NER **or** dedup. NER must not invent facts.

**Input.** `text` (investigation JSON), `investigation_query`. Flattened +
chunked before extraction.

**Output contract.**
- *Entity record:* `identifier` (entity name, UPPER, non-empty), unique
  `unique_identifier`, `type = "entity"`, `chunk_uuid`, `source` (provenance from
  the chunk), and `data` = the LLM `Entity` (name; `type ∈ {PERSON, ORG}`;
  location/address/email/phone/position/timeline/financial; `relevance_score ∈
  [0,1]`; search_source/url).
- *Affiliation:* `(entityA, entityB, affiliation_type ∈ {affiliation, partnership,
  ownership, non_direct})`, per chunk.

**Invariants (what we will check predicted-vs-actual).**
- I1. Every `identifier` non-empty and upper-cased; no `""/"None"/"N/A"`.
- I2. `data.type ∈ {PERSON, ORG}` (prompt restricts to these two).
- I3. `relevance_score ∈ [0, 1]`.
- I4. **No fabricated fields** — attributes absent from the chunk text are empty
  / "Not found", never invented (esp. email/phone/address).
- I5. **Grounding** — every entity traceable to its `chunk_uuid`'s text; no
  world-knowledge entities.
- I6. Affiliation endpoints reference names that were extracted as entities.
- I7. Chunking is lossless — every input record's text reaches some chunk.
- I8. **No relevance filtering** — NER (and dedup) must keep every extracted
  entity regardless of `relevance_score`; only Stage 4 may drop on relevance.

### 1.2 Predicted output (from spec, before revealing golden)

For the Exampleorg input the spec implies: more records than distinct names
(same org recurs across chunks); **I1** all identifiers non-empty/upper; **I2**
every `data.type ∈ {PERSON, ORG}`; **I3** every `relevance_score ∈ [0,1]`;
**I4** PII (`email`/`phone`/`address`) mostly empty/"Not found" *if not
fabricated* (an OSINT snippet rarely lists an org's email) — high fill-rates
would signal fabrication; **I6** ~all affiliation endpoints are extracted
entities; **I8** NER drops nothing on relevance.

### 1.3 Actual (golden snapshot)

| invariant | result |
|---|---|
| I1 identifiers | **PASS** — 158 records, 0 empty/`None`, 0 not-upper |
| I2 type ∈ {PERSON, ORG} | **PASS** — 112 ORG + 46 PERSON, 0 others |
| I3 relevance ∈ [0,1] | **PASS** — range [0.0, 0.8], 0 out of range |
| I4 no fabrication | **PASS (empirically)** — fill rates: email 1/158, phone 3/158, address 10/158, location 40/158, position 40/158 → the model left PII empty rather than inventing it |
| I6 affiliation grounding | **mostly** — 207/210 endpoints (99%) are extracted entities; 3/210 (1%) are not |
| I8 no relevance drop in NER | **PASS** — all 158 kept; **but** 142/158 (**89%**) score `<0.5` |

Relevance histogram: `{0.0: 8, 0.1: 113, 0.3: 21, 0.5: 15, 0.8: 1}`.

### 1.4 Discrepancies → findings

- **NER meets its target functionality** (I1–I3, I8 pass; I4 holds empirically).
  The recall-first, extract-everything contract is honoured.
- **F1 (downgrade PR5).** The predicted fabrication risk did **not** materialise:
  the model left email/phone/address empty far more often than it filled them.
  PR5 (the "fill every field / don't infer" prompt conflict) is a *latent* risk,
  not an active one on this data — lower severity than first flagged.
- **F2 (minor, I6).** 3/210 affiliation endpoints name entities NER didn't emit
  as records (e.g. `SHEIKH YUSUF QARADAWI`, `GAZA'S GENEROSITY ASSOCIATION`).
  In Stage 3 these become **phantom graph nodes** (no entity record behind them).
  Low frequency; log for Stage 3.
- **F3 (HEADLINE — cross-stage, lands in Stage 2).** NER correctly carries
  `relevance_score` without filtering, **but 89% of entities score `<0.5`** and
  `dedup`'s `merge_list_of_dicts` *skips any record-data with
  `relevance_score < 0.5`* when merging duplicate groups. So the bulk of
  extracted entity **data is silently gutted during dedup** — a direct violation
  of I8 ("no relevance filtering before triangulation"). NER is not at fault; the
  fix belongs to Stage 2. This is the first headline bug the methodology found.

### 1.5 Test

`tests/test_stage1_ner.py`: (a) deterministic assembly — `extract_entities_from_chunk`
with `get_entities` mocked, asserting record shape, name filtering, source
resolution, `chunk`/`dirty_node_names` bookkeeping; (b) invariant checks I1–I3
+ I6 frozen against the golden `ner_nodes`.

---

## 2. Stage 2 — Dedup / representative resolution

**Code:** `get_representative_identifiers_task` (→ `MostRepresentativeIdentifier`
LLM) then `find_and_group_duplicates_for_entity` → `merge_duplicates_into_one_records`
→ `merge_list_of_dicts` (`graph/dedup.py`).

### 2.1 Target functionality (CONFIRMED 2026-05-29)

**Purpose.** Collapse records that denote the **same real-world entity** (name
variants / cross-chunk repeats) into **one node**, preserving information — never
filtering on relevance (Stage 4's job, I8).

**Grouping.** Variant names map to a `representative_identifier` (canonical name);
the node carries `labels` = the absorbed variant names.

**Merge policy (per-field, user-defined):**
- M1 **No drop.** Every grouped entity survives as one node; `relevance_score`
  becomes the **max** over the group; nothing is discarded for low relevance.
- M2 **Prefer-best for single-valued facts.** When sources conflict on a
  single-valued field, keep the value from the **higher-relevance / more-complete**
  source.
- M3 **Union for legitimately multi-valued facts.** Fields an entity can really
  have several of — e.g. an ORG's `address`, `phone_number` — keep **all distinct
  values**.
- M4 **Query-relative fields.** A person's `position`/role is only meaningful
  w.r.t. the investigation query; retain accordingly (don't blindly union noise).

### 2.2 Predicted output (current code vs target)

Reading the code: `merge_list_of_dicts` **skips any source `data` with
`relevance_score < 0.5`**, then list-valued fields are `dedupe + ","/":" join`
(no field-awareness, no prefer-best). So predicted **discrepancies vs target**:
- **M1 violated:** with 89% of entities `<0.5` (Stage 1), most duplicate-group
  merges should drop the primary record's data → **gutted/sparse `data`** on
  merged nodes (worst case `{}` when every group member is `<0.5`).
- **M2 violated:** no relevance/completeness preference — first-non-skipped wins
  or values are blindly joined.
- **M3 partial:** list fields are joined into a delimited **string**, not kept as
  a clean multi-value set; scalar-valued duplicates aren't unioned.
- Grouping itself: predict ~70 nodes from 158 records (matches `representatives`≈71).

### 2.3 Actual (golden + direct probe)

Grouping: 158 raw → **70 nodes** (≈71 representatives) — as predicted.

The M1-gutting prediction was **only partly right**, and the diff forced a direct
probe of `merge_list_of_dicts`:
- Golden dedup nodes: **0 empty-data, 0 near-gutted** — every node has rich data.
- But `merge_list_of_dicts([six GLOBALAID data dicts all relevance 0.1]) == {}`,
  and `merge_list_of_dicts([{0.8}, {0.1}]) == {only the 0.8 dict}`.

Reconciliation: the `<0.5` skip **does** drop data, but a node only goes *empty*
when **every** member of its duplicate group is `<0.5`. In this run each merged
group contained ≥1 member ≑0.5, so its data survived and the `0.1` variants were
silently dropped — invisible in node counts, real in lost attributes.

M3: **34/70** nodes have `","`/`":"`-joined string fields, e.g.
`location = "Texas, USA; Gaza,U.S.,United States,Texas"` (note the residual
duplicate "Texas").

### 2.4 Discrepancies → findings

- **F3 (CONFIRMED, headline).** `merge_list_of_dicts` **drops every source with
  `relevance_score < 0.5`** (probe-proven). This is a relevance filter inside
  dedup → violates **M1** (no-drop). Failure modes: (a) a duplicate group that is
  entirely `<0.5` collapses to `data = {}` (mechanism proven; didn't surface in
  this run by data-luck); (b) mixed groups silently lose the low-relevance
  variants' unique attributes. It is a crude hard-threshold *approximation* of
  **M2** (prefer-best) that overshoots into discarding.
- **F4 (CONFIRMED, corollary of F3).** Edge `attributes` dicts carry **no**
  `relevance_score`, so `merge_list_of_dicts(attributes)` skips them all and
  **always returns `{}`** — edge-attribute merging in `merge_states` is dead
  (matches the step-7 observation). One fix (drop the relevance filter) repairs
  both F3 and F4.
- **F5 (M3/M4).** Merge is field-blind: list values are deduped+joined into a
  delimited **string** (residual dups, ambiguous separators) rather than a clean
  multi-value set; there is no "ORG address/phone → union" vs "person position →
  query-relative" distinction.

### 2.5 Fix — APPLIED (full M1–M3 redesign)

1. `merge_list_of_dicts`: **removed the `relevance_score < 0.5` skip** → no source
   data dropped (M1); fixes F3 and F4 (edge attributes now merge instead of `{}`).
2. `merge_duplicates_into_one_records`: `relevance_score` = **max** (M1);
   single-valued fields (`type`) take the best source's value (M2); all other
   scalar fields become a **clean distinct list** (M3), replacing the SemHash
   `","`-join (F5). `merge_states` aligned to the same union policy for resume.

3. **F6 (data-quality, found while showing samples):** the LLM-armed-search
   layer emits filler for absent attributes (`"Not specified in provided data"`,
   `"Not available…"`, `"unknown"`). These leaked into the merged unions. Added
   `_is_empty_value` (case-insensitive exact set + prefix match) and applied it
   in both merge sites → filler dropped, real values kept. Golden re-run: **0
   residual filler items** across all dedup nodes.

**Validation.** Re-running dedup on the golden NER input: 70 nodes (unchanged
grouping), **0 empty-data nodes**, `relevance_score` numeric-max, multi-valued
fields now clean lists (e.g. `phone_number = ['(972) 257-2564']`).
`tests/test_stage2_dedup.py` (7 policy tests + golden regression) green; live
smoke fresh + resume `status=success`, list-valued `data` round-trips. The
residual M4 (`position`/role query-relativity) is left to a prompt/Stage-4 pass.

### 2.6 Naming suggestions (dedup stage)

Research-code names that hurt readability → proposed:

| current | proposed | why |
|---|---|---|
| `merge_list_of_dicts` | `merge_data_fields` | it merges entity `data` field-by-field, not arbitrary dicts |
| `merge_duplicates_into_one_records` | `merge_duplicate_group` | grammatical; it collapses one duplicate group |
| `find_and_group_duplicates_for_entity` | `deduplicate_entities` | that's the whole job |
| `group_identifiers_for_representative` | `cluster_identifiers` | it clusters id strings (only >200) |
| `add_relations_to_nodes` | `attach_relations_to_nodes` | clearer verb |
| `register_edges` | `resolve_edge_endpoints` | what it does (nodeA/B → src/dst + uuid) |
| `merge_states` | `merge_run_into_saved` | direction is otherwise unclear |
| `deduplicated_and_merged_nodes` (var) | `entities` | the working entity list; the long name adds nothing |
| `records_group_by_name` (var) | `entity_groups` | — |
| `local_investigation_state` (var) | `working_state` | distinguishes from the persisted `state` |
| `dedup` (var, the group obj) | `group` | — |
| `hypotesis` (everywhere) | `hypothesis` | typo in the data contract |

## 3. Stage 3 — Graph construction

**Code:** `build_graph(entity_groups, working_state)` (`graph/operations.py`),
already index-ified in migration step 5 (`tests/test_build_graph.py`, 9 tests).

### 3.1 Target functionality (PROPOSED — for confirmation)

**Purpose.** Build the coarse-grained **relationship graph** that triangulation
(Stage 4) runs on: nodes = canonical entities, directed edges = affiliations
asserted between them in the source chunks. Stage 4 uses this graph's
connectivity to find the investigation "core" (most-connected node) and to
decide which entities are connected vs isolated.

**Input.** `entity_groups` (deduped entities, each with `identifier` /
`representative_identifier` / `labels`); `working_state.chunks[*].affiliations`
(`entityA`, `entityB`, `affiliation_type`).

**Output.** a `DiGraph` (edges keyed by canonical id, carrying `chunk_id` +
`label`), the `coarse_grained_edges` list, and degree rankings
(`most_connected_node`, `highest_degrees` deg>1, `lowest_degrees` deg≤1).

**Invariants (predicted-vs-actual):**
- G1. **Canonical nodes.** Every graph node is a canonical id (some entity's
  `representative_identifier`); affiliation endpoints resolve via identifier or
  label. *(Open: unresolved endpoints — Stage-1 F2 phantoms.)*
- G2. **Edges ⊆ affiliations.** Every edge corresponds to a chunk affiliation; no
  invented edges.
- G3. **Endpoint resolution.** `raw_to_canonical` maps every variant/label name to
  the right `representative_identifier` (consistent with Stage-2 grouping).
- G4. **No self-loops** (A→A dropped).
- G5. **Edge dedup** — at most one edge per ordered (A,B) pair.
- G6. **Degree ranking** identifies the core: `most_connected_node` = max degree;
  `highest_degrees` = deg>1.
- G7. **Direction.** Edge A→B encodes the asserted affiliation direction (Stage 4
  re-derives an *undirected* graph for connectivity, so direction is for the
  output/report, not for triangulation reachability).
- G8. **Connectivity to root (relevance invariant).** Every node must be reachable
  from the root (the query's main entity) in the undirected graph — *hops-to-root*
  is a relevance metric. A component disconnected from root is **either a graph
  bug or a genuinely query-irrelevant subgraph**; this stage must not create
  spurious disconnections.

**Confirmed decisions:** (1) **drop** edges with an unresolved endpoint; (2)
**merge** distinct relation types between a pair into a label **list**; (3)
affiliations are directed but **direction needs correcting** (open — proposal in
3.4).

### 3.2 Actual (golden affiliation analysis)

105 affiliations → 210 endpoints. `affiliation_type` distribution:
`affiliation 70, partnership 18, financial 6, non_direct 5, coalition 4,
contractor 1, funding 1`.

### 3.3 / 3.4 Discrepancies → findings

- **F7 (headline — resolution gap).** `build_graph`'s `raw_to_canonical` is built
  from `entity_groups` (`identifier` + `labels`) only. But affiliation endpoints
  frequently use *representative variant* names that live in
  `representatives.relevant_identifiers`, **not** in a node's `labels`. Result:
  **13% (29/210)** of endpoints look unresolved with the current map, but only
  **1% (3/210)** are truly unresolved once the map is enriched with
  `representatives`. So the **map is incomplete** — and naively applying decision
  (1) "drop phantoms" on the *current* map would discard **26 real affiliations**
  (e.g. to `AMERICAN MUSLIMS FOR PALESTINE`, `ISLAMIC RELIEF`).
  **Fix:** enrich `raw_to_canonical` with `representatives.relevant_identifiers →
  representative id`, *then* drop the 3 true phantoms.
- **F8 (decision 2).** 3 resolved pairs carry >1 relation type (e.g.
  `GLOBALAID, INC. → HAMAS = {affiliation, partnership}`); current code keeps
  only the first. **Fix:** edge `label` = sorted list of distinct types.
- **F9 (decision 3 — direction).** 5 unordered pairs appear in **both**
  directions (A→B and B→A), all with the *same* relation type
  (4×affiliation, 1×financial) — i.e. one symmetric relationship double-counted
  as two directed edges (the dedup guard is directed, so `has_edge(A,B)` misses
  `(B,A)`). **Proposed correction:** classify `affiliation_type` as **symmetric**
  (affiliation, partnership, coalition, non_direct → canonicalise endpoint order
  so A↔B is one edge) vs **directional** (financial, funding, ownership,
  contractor → keep A→B as asserted). **CONFIRMED + implemented.**

### 3.5 Fix — APPLIED (F7 + F8 + F9) and connectivity validation

- **F7**: `build_graph` now takes `representatives` and enriches `raw_to_canonical`
  with `relevant_identifiers → representative id`; endpoints that still don't
  resolve are **dropped** (decision 1).
- **F8**: one edge per (canonicalised) pair, `label` = sorted **list** of distinct
  relation types.
- **F9**: symmetric types canonicalise endpoint order (A↔B = one edge);
  directional types keep A→B.

**Connectivity validation (G8) — the decisive check.** On the golden graph:

| | nodes | components | reachable from root |
|---|---|---|---|
| **without F7** | 58 | **2** (54 + 4) | a 4-node subgraph **disconnected** |
| **with F7** | 64 | **1** | **all 64** (root = GLOBALAID, INC.) |

The incomplete resolution map (pre-F7) was **spuriously disconnecting relevant
entities from root** — which Stage 4 would mis-score as irrelevant. F7 makes the
graph fully connected; hops-to-root spread `{1:37, 2:15, 3:5, 4:3, 5:1, 6:2}`.
`tests/test_build_graph.py` (14 tests) pins F7/F8/F9 + a golden connectivity
regression (1 component, all reachable from root). Residual: 3 mixed/inconsistent
directional pairs read as bidirectional in the *directed* graph but collapse in
the undirected projection used for connectivity — harmless to G8, logged.

## 4. Stage 4 — Triangulation (evidence consolidation + connectivity scoring)

**Code:** `node_and_evidence_consolidator` (orchestrator) + `score_graph_by_connectivity`
(`graph/operations.py`). Golden oracle: `consolidator_in_*` / `score_in_*` +
captured `evidences_extract`/`evidences_investigate` (mock the in-consolidator LLM
calls to test deterministically).

### 4.1 Target functionality (PROPOSED — for confirmation)

**Purpose.** Produce the *triangulated* subgraph: keep the entities that are
**both relevant to the query AND connected to the root** (the query's main
entity), each annotated with supporting evidence and a probability. This is the
final answer; everything else is noise to drop.

**4A — Evidence consolidation** (`node_and_evidence_consolidator`):
- For each entity, gather LLM evidence (does it support the hypothesis /
  investigation subject?); map each evidence's `related_node` to its entity.
- An entity with supporting evidence (`hypotesis=true`) → mark `hypothesis=True`,
  `leaf=True`, attach the evidence node; wire the evidence into the graph.
- Propagate evidence to entities along affiliation paths toward root; compute
  per-entity `prob` from its evidence (`rescore_evidences`).

**4B — Connectivity scoring** (`score_graph_by_connectivity`):
- Drop **low-relevance** non-evidenced nodes (this is the *legitimate* place for
  relevance gating — Stage-1/2's job was only to carry the score).
- Drop nodes **disconnected from root** (your G8 relevance criterion).
- Drop **orphan edges** (an endpoint was dropped).

**Survival rule (CONFIRMED 2026-05-29):** an entity **survives iff it has
supporting evidence**. No evidence → **drop** (relevance is *not* the gate). An
evidenced entity that has no affiliation path to root is **wired directly to root
via an `evidence`-type edge** (low relevance is acceptable). **Any** connectivity
to root suffices — but **hops-to-root feeds the relevance score** (closer ⇒ more
relevant). The exact relevance bar/score formula is under review (§4.2a).

**Target invariants (T-series):**
- T1. **Evidence→entity mapping** uses the *same* canonical resolution as Stage 3
  (identifier + labels + **representative variants** — cf. F7); no evidence lost
  to a variant-name miss.
- T2. Entity with ≥1 supporting evidence → `leaf=True`, `hypothesis=True`,
  evidence attached, and **connected to root** (affiliation path or `evidence`
  edge).
- T3. **Survival = evidence.** Every surviving entity has evidence; every
  no-evidence entity is dropped.
- T4. **G8 on the output** — every survivor is connected to root.
- T5. `relevance_score` of a survivor is updated from its **hops-to-root**.
- T6. Output edges connect two surviving entities; orphans removed.

### 4.2 Findings (verified on the golden, deterministic)

- **F10 (CONFIRMED + FIXED — the evidence-strength scorer was broken).** The old
  `rescore_evidences` was not a usable `prob`:
  1. **confidence cancelled** — `Σ(score·conf)/Σconf` divides confidence straight
     back out; a single (score 1, conf 0.1) and (score 1, conf 1.0) both gave
     `prob 1.0`;
  2. **contradicting evidence dropped** — the `score>0` filter excluded
     negatives, so `[+strong, −strong] → 1.0` (a contradiction can't lower it);
  3. **floored at 0.5** — `(x+1)/2` over positives-only; real golden output was
     only `{0.75: 17, 1.0: 15}` (just a remap of `score`);
  4. **meaningless t-test** — one-sample t-test against 0 over all-positive
     products always passes (`add_to_leaf=True` on all 38 nodes); could spuriously
     fail tiny evidence sets by low power.
  **Fix:** new `evidence_probability(evidence)` — signed (`hypotesis` = ±),
  `strength` = magnitude, `confidence` = weight, `prob=(signal+1)/2 ∈ [0,1]`,
  no t-test, single-evidence works. Golden: distinct `prob` values **2 → 21**;
  live smoke survivor probs now a real spread (`0.85…0.965`). Contradiction-ready
  (signs by `hypotesis`) though no contradicting evidence flows until the
  bidirectional-extraction prompt change.
- Consolidator final loop now **evidence-gated**: evidenced → `prob` (any count),
  `leaf=True`, `hypothesis = prob≥0.5`; no-evidence → `prob 0`, `leaf=False`.

### 4.3 `score_graph_by_connectivity` rework — DONE

Replaced the two broken filters with the confirmed model:
- **Survival = credible evidence** (`prob > 0`); no-evidence nodes dropped. The
  old relevance-threshold filter (drop <0.6 / leaf <0.3) was removed — it had
  been dropping **30 of 38 evidenced entities** because the LLM relevance is
  noisy (mostly 0/0.1). The **no-op connectivity filter** (only removed in-graph
  degree-0 nodes — contradictory → never fired) is gone. `root` (the
  investigation main entity) is always kept as the anchor.
- **`relevance_score = 0.7 ** hops_to_root`** over the clean affiliation graph
  (`tangraph`); evidenced-but-unaffiliated entities pay `_EVIDENCE_HOP_COST = 2`.
- **`score = relevance_score × prob`** (Decision C).
- **Orphan edges dropped**; any survivor not connected to root via surviving
  edges is wired to it with a typed **`evidence`** edge → **G8 holds on the
  output**. `score_graph` takes `root` (was `relevance_threshold`).

**Validation.** Golden: **38 survivors** (= all evidenced) vs the old filter's 8;
G8 holds. Live smoke: **38 nodes / 46 edges**, *all* evidence-backed, 2 `evidence`
edges, `score ∈ [0.257, 1.0]`, **38/38 reachable from root**, recall 0.38 (was
far lower), 0 errors. `tests/test_stage4_score.py` (7 tests + golden G8/evidence
regression).

### 4.4 Bidirectional extraction + `hypotesis → hypothesis` rename — DONE

- **Rename** `hypotesis → hypothesis` everywhere (the `Evidence` OutputField, the
  `hypothesis` InputField, dict keys, `return_hypothesis_for_domain`); the
  distinct `hypotests` payload key is left alone. Golden regenerated, so the
  fixtures now use `hypothesis`.
- **Bidirectional prompts:** `ExtractEvidenceFromJSONText` /
  `InvestigateEvidenceFromJSONText` now ask for evidence that **supports or
  contradicts** (set `hypothesis` true/false; `score` sign agrees), with strict
  grounding.
- **Consolidator gate lifted:** the old `if is_proved:` attached only supporting
  evidence; now **both polarities are attached** (append, not overwrite) so the
  signed `prob` nets them; graph-wiring to root stays supporting-only. `leaf`/
  `hypothesis` are derived from `prob` in the final loop (resolves the old
  `leaf`-default-`True`).
- **Empirical result:** even bidirectional, the LLM returned **213/213 supporting,
  0 contradicting** on the Exampleorg dossier — the source is one-sided, so there's
  no exonerating evidence to surface. The contradiction path is *ready and tested*
  (`tests/test_stage4_consolidator.py` mocks both polarities → both attached,
  contradiction lowers `prob`) but unexercised by this fixture.
- Verified: golden regen + all golden suites pass; server smoke 58 nodes / 74
  edges, all evidence-gated, evidence carries `hypothesis` (no stale `hypotesis`),
  G8 58/58 reachable from root, 0 errors.

Still open (minor): the F7-style evidence→entity resolution in the consolidator
(it pre-maps via `representative_identifiers`, so likely fine); the unused
`relevance_threshold` request param (harmless).
