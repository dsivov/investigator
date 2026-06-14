# OSINTGraph — Data Model & Data-Flow Rework (design)

> Phase 3, priority 1 (data model) + 2 (loops). Behaviour-*changing* refactor —
> verified structurally via `tests/run_smoke.py`, not by exact parity
> (gpt-4.1 is non-deterministic). Each step ships as its own smoke-tested commit.

---

## 1. Current data flow (the mess)

### Three overlapping "state" representations
| object | lifetime | mutated how | holds |
|---|---|---|---|
| `local_investigation_state` (31 refs) | one request | **side-effect** appends inside step fns | nodes, edges, chunks, dirty_node_names, representative_identifiers, junction_* |
| `current_investigation_state` (6 refs) | one request | read-only snapshot from DB | nodes, edges, representative_identifiers |
| persisted record via `state_repo` (13 refs) | across requests | `.add()` / `.update()` at 3 points | same fields |

Same logical fields live in all three, copied between them by hand.

### `_standard_pipeline` flow today
```
local = {"nodes":[], "edges":[]}
current = state_repo.find(id)  (or add fresh)
step1 named_entities_extractor_task(.., local, ..)   # MUTATES local; returns (nodes, affiliations, is_input)
step2 dirty = local[dirty] + repo.get_field(dirty);  repo.update(dirty);  rep_ids = task(dirty);  local[rep_ids]=rep_ids
step3 dedup, _, _ = find_and_group_duplicates_for_entity(nodes, rep_ids)
step4 edges, _, top_degrees, _, graph = build_graph(dedup, local)   # MUTATES local[junction_*]; returns 5-tuple
step5 enrich = edges_enrichment_task(..); dedup = add_relations_to_nodes(dedup, enrich); enrich = register_edges(dedup, enrich, ..)
step6 dedup,_,_,graph = node_and_evidence_consolidator(local, dedup, top_degrees, rep_ids, graph, ..)
      dedup, enrich, _ = score_graph_by_connectivity(investigator, enrich, dedup)   # MUTATES dedup & enrich in place AND returns
merge saved_nodes = current[nodes].copy(); enrich, dedup, saved_edges, saved_nodes = merge_states(..)  # all in-place + returned
step7 repo.update(id, {nodes: saved_nodes+dedup, edges: saved_edges+enrich, ..})
return {nodes: saved_nodes+dedup, edges: saved_edges+enrich}
```

Three mutation contracts coexist: **side-effect-only** (step1), **mutate+return-tuple** (step4), **mutate-in-place+return** (step6/merge). Callers can't tell which from the signature.

### ID-field proliferation
Entity record: `identifier`, `representative_identifier`, `graph_identifier` (**dead — set once, never read**), `unique_identifier`.
Edge record: starts `nodeA`/`nodeB`, re-mapped in `register_edges` to `src_identifier`/`dst_identifier` + `src_unique_identifier`/`dst_unique_identifier`. Evidence uses `related_node`.

### Why the loops blow up (priority 2 — same root cause)
No `id → record` index exists, so every step linear-scans lists:
- `build_graph`: O(chunks²) junctions + `chunk × affiliation × record` with `nx.relabel_nodes` (O(V+E)) inside — the inner `for record` only maps raw id → canonical id by scanning all records.
- `register_edges`: `edge['unique_identifier']=uuid4()` runs inside `node × edge` → regenerated N_nodes× per edge, final value non-deterministic.
- `node_and_evidence_consolidator`: `nx.shortest_path` inside `top_degrees × evidence × nodes`.
- `add_relations_to_nodes` / `merge_states`: O(nodes·edges), O(nodes·saved).

### Graph-construction bugs (correctness, not just perf — surfaced during audit)
The nested-loop graph building isn't only slow; several constructions are
**wrong or dead**. The index/`EdgeRecord` rework must fix these, not just speed
them up:

1. **`build_graph` junction map overwrites** (`operations.py:145`): `junction_nodes[chunkA["uuid"]] = {...}` is keyed by `chunkA` only, so for a chunk that shares entities with several others, **each matching `chunkB` overwrites the last** — only one neighbour survives per chunk. Junction detection silently drops pairings. → fix in step 5 (per-pair / shared-entity-set keying).
2. **`score_graph_by_connectivity` synthesizes edges then deletes them all** (`operations.py:262` vs `:305`): the `for edge in investigator.edges` block appends edges carrying `src_identifier/dst_identifier/unique_identifier` but **no `src_unique_identifier/dst_unique_identifier`**; the later cleanup removes every edge missing those keys → 100% of the synthesized edges are discarded. The enriched_graph build is the only useful part; the append is dead work. This is a direct symptom of the inconsistent edge id model. → fix in step 6 (`EdgeRecord` with one consistent field set).
3. **`build_graph` dead dedup guard** (`operations.py:135,205`): `fully_connected_edges_` is declared and membership-tested every affiliation iteration but **never appended to** — the guard is always-empty/no-op; real dedup rides only on `graph.has_edge`. → remove in step 5.
4. **`chunk_exists` never reset** (`operations.py:137`): set `True` on the first chunk pair with overlap and never cleared, so the "No shared entities" debug log is effectively dead after the first hit. Cosmetic, fix opportunistically in step 5.
5. **`GraphBuilder`, `build_full_coarse_grained_graph`, `find_junction_nodes` are unused** (kept as scaffold in Phase 1): not called anywhere in the pipeline; both functions repeat the O(chunks²)+relabel pattern and a no-op `continue` (`operations.py:84`, meant to be `break`). → decide in step 8: delete, or fold into the live path. (Like `graph_identifier`, dead-but-kept.)

---

## 2. Target model

### One state object
```python
@dataclass
class InvestigationState:
    session_id: str
    nodes: list[EntityRecord] = field(default_factory=list)   # target: EntityRecord objects (step 2b)
    edges: list[EdgeRecord] = field(default_factory=list)     # target: EdgeRecord objects (step 5)
    chunks: list[dict] = field(default_factory=list)
    representative_identifiers: list[dict] = field(default_factory=list)
    dirty_node_names: list[list[str]] = field(default_factory=list)
    runs_number: int = 0
    investigation_query: str | None = None      # persisted record metadata
    investigation_subject: str | None = None     # persisted record metadata
    _index: dict[str, EntityRecord] = field(default_factory=dict, repr=False)  # canonical_id -> EntityRecord

    # --- index ---
    def reindex(self) -> None: ...                 # rebuild _index from nodes
    def node(self, cid: str) -> EntityRecord | None: ...  # O(1) lookup
    def add_nodes(self, nodes: list[EntityRecord]) -> None: ...

    # --- persistence (collapses the 3 reps) ---
    @classmethod
    def load(cls, repo, session_id) -> "InvestigationState": ...   # from DB or fresh
    def save(self, repo) -> None: ...                               # upsert to DB
```
This replaces `local_*` + `current_*` + ad-hoc repo dict-juggling with **one** object that knows how to load/save itself and offers an index.

### Signatures are the data contract (dspy)

A DSPy **Signature** is *"the declarative contract between your program and the
language model: the input fields it accepts, the output fields it produces, and
the instructions"*. The `OutputField` pydantic models in `llm/models.py` are
therefore the **canonical shapes** of everything the LLM hands us — our storage
records must be faithful *projections* of them, not a parallel invented shape:

| signature (`llm/signatures.py`) | output field → model (`llm/models.py`) | becomes |
|---|---|---|
| `NamedEntitiesRecognition` | `entities: list[Entity]`, `affiliations: list[Affiliation]` | `EntityRecord.data` (the `Entity` blob) + chunk affiliations |
| `GraphEdgesEnrichment` | `edges: list[Edge]` (`nodeA/nodeB/relations/attributes/source`) | `EdgeRecord` (after endpoint resolution) |
| `ExtractEvidenceFromJSONText` / `InvestigateEvidenceFromJSONText` | `evidences: list[Evidence]` | evidence sub-records (+ derived graph fields) |
| `MostRepresentativeIdentifier` | `representative_identifiers: list[Identifier]` | `state.representative_identifiers` |

Three rules this imposes on the data model:

1. **Don't shadow the contract names.** `Entity` and `Edge` already exist in
   `llm/models.py` as the *signature* models. Our storage dataclasses are named
   **`EntityRecord` / `EdgeRecord`** to keep the LLM contract and the stored
   record distinguishable.
2. **The conversion functions are the boundary adapters** — `from_extraction`
   consumes a `models.Entity`, `from_enrichment` consumes a `models.Edge`.
   That's the *only* place a signature model becomes a storage record.
3. **Preserve signature field names across the boundary** (the dspy docs note
   field names "anchor the program's interface and data contracts" and survive
   optimization). In particular the signature spells it **`hypotesis`**
   (on `Evidence`); the entity-level boolean flag is **`hypothesis`**. These are
   **two different fields** — the evidence one mirrors the LLM contract, the
   entity one is a derived rollup. Do **not** unify or "fix the typo": that
   would silently break the evidence↔signature mapping.

> The signature *prompts* themselves have logical issues (instructions asking
> for fields the schema can't hold, muddled score scales, a self-contradictory
> "fill every field / don't infer" in NER). These are prompt-engineering fixes,
> tracked separately in **PRODUCTIZATION.md §2 → "Prompt / signature logic
> issues"** (PR1–PR6) — not part of this data-model migration.

### Records: `EntityRecord` / `EdgeRecord` dataclasses

> **Decision (revised 2026-05-29):** records become **dataclasses**, not plain
> dicts. (This reverses the earlier "keep dicts" non-goal.) The earlier worry —
> rippling into dspy / semhash / coffy — is handled by converting **only at the
> boundaries**, not everywhere:
>
> | boundary | direction | conversion |
> |---|---|---|
> | dspy extraction (`models.Entity`) | in | `EntityRecord.from_extraction(model, chunk_id)` |
> | dspy enrichment (`models.Edge`) | in | `EdgeRecord.from_enrichment(model, src, dst)` |
> | semhash (`SemHash.from_records`, `columns=[...]`) | out/in | `[e.to_dict() for e in ...]` → wrap results back via `from_dict` |
> | coffy persist (`InvestigationState.load/save`) | out/in | `to_dict()` on save, `from_dict()` on load |
> | HTTP response | out | `to_dict()` |
>
> **Where records live (revised — see step 6b decision in §3):** the typed
> records are the **persistence + API boundary** model — `state.nodes` /
> `state.edges` hold them, and `load`/`save`/HTTP convert at the edge. The
> **working set inside the graph pipeline stays dicts**, because the steps it
> flows through (`SemHash.from_records`, `merge_list_of_dicts`, dspy
> `model_dump()`) are dict-centric; converting to record objects there would be
> churn or a risky rewrite for little gain. The win is still real: one typed
> place that knows a record's shape, lossless round-trip, and `canonical_id` as
> a method instead of an open-coded
> `node.get("representative_identifier", node["identifier"]).upper()`.

```python
@dataclass
class EntityRecord:
    unique_identifier: str               # stable per-record UUID (provenance / edge endpoints)
    identifier: str                      # working name (UPPER) from extraction
    representative_identifier: str = ""   # canonical name after dedup ("" until dedup runs)
    type: str = "entity"
    source: str = "unknown"
    chunk_uuid: str | None = None
    data: dict = field(default_factory=dict)   # == models.Entity.model_dump() (the extraction
                                                # contract: name, type, location, address, email,
                                                # phone_number, position, timeline_events,
                                                # financial_restrictions, relevance_score,
                                                # search_source, search_url) + dedup-added
                                                # relevant_entities / relations
    labels: list[str] = field(default_factory=list)
    most_significant_labels: list = field(default_factory=list)
    triangulated: bool = False
    hypothesis: bool = False              # derived rollup flag — NOT the evidence `hypotesis`
    leaf: bool = False
    prob: float = 0.0
    evidence: list[dict] = field(default_factory=list)   # evidence sub-records (see below)
    evidence_count: int = 0
    self_evidence: dict = field(default_factory=dict)

    @property
    def canonical_id(self) -> str:        # representative_identifier or identifier, UPPER
        return (self.representative_identifier or self.identifier or "").upper()

    @classmethod
    def from_extraction(cls, model: "models.Entity", chunk_id: str) -> "EntityRecord": ...
    @classmethod
    def from_dict(cls, d: dict) -> "EntityRecord": ...
    def to_dict(self) -> dict: ...
```

`data` is an **open dict** because it mirrors the extraction signature's `Entity`
output and then accretes dedup-added keys — `EntityRecord` types the *structural*
envelope (ids, flags, evidence), not the LLM blob. Its shape is owned by the
`NamedEntitiesRecognition.Entity` signature, not by us.

`graph_identifier` is **dropped** — it was set once in dedup and never read.

The current `ids.py::canonical_id(node: dict)` (shipped in step 1) stays as the
dict-level helper used at the boundaries; `EntityRecord.canonical_id` is the
in-memory form. Both compute the same value.

> **Open sub-decision — evidence records.** Each entity carries an `evidence`
> list of evidence-node dicts: the `Evidence` signature fields (`related_node`,
> `evidence`, `confidence`, `strength`, `score`, `metadata`, `reasoning`,
> **`hypotesis`**) plus derived graph fields (`identifier`, `doc_id`,
> `relations`). Option (a) leave these as nested dicts; (b) add an
> `EvidenceRecord` dataclass too. Proposed: **(a) for now** — they're leaf data,
> not indexed or looked up, so the dataclass win is smaller. Revisit if step 6
> gets messy.

### Target `EdgeRecord` schema (define-before-step-5)

The enrichment signature (`GraphEdgesEnrichment`) hands us `models.Edge`
(`nodeA`, `nodeB`, `relations`, `attributes`, `source`). Today `register_edges`
then accretes **7** id-ish keys: `nodeA`/`nodeB` → `src_identifier`/`dst_identifier`
+ `src_unique_identifier`/`dst_unique_identifier`, plus the edge's own
`unique_identifier`. `nodeA`/`nodeB` are transient and popped.

Target final record (5 id-ish fields, `nodeA`/`nodeB` resolved away, uuid assigned **once**):

```python
@dataclass
class EdgeRecord:
    unique_identifier: str                 # the edge's own UUID — assigned ONCE
    src_identifier: str                    # canonical_id of source entity (== graph node key)
    dst_identifier: str                    # canonical_id of dest entity
    src_unique_identifier: str = ""         # source EntityRecord.unique_identifier (provenance join)
    dst_unique_identifier: str = ""         # dest EntityRecord.unique_identifier
    type: str = "affiliation"
    relations: str = "[]"                  # JSON string (kept: register_edges json.dumps today)
    attributes: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    source: str = ""

    @classmethod
    def from_enrichment(cls, model: "models.Edge", src: "EntityRecord", dst: "EntityRecord") -> "EdgeRecord": ...
    @classmethod
    def from_dict(cls, d: dict) -> "EdgeRecord": ...
    def to_dict(self) -> dict: ...
```

Decision points baked in:
- **Endpoints are `canonical_id` (names), `*_unique_identifier` kept as a stable
  join** — `merge_states` rewrites endpoints by uuid when canonical names shift
  across runs, so the uuids earn their place. (Open: if cross-run rename turns
  out not to happen, they can be dropped — flag for step 6.)
- **`relations` stays a JSON string** to preserve the current persisted shape;
  revisit once nothing downstream relies on the string form. (Note: the signature
  `models.Edge.relations` is a single `Relation`; `from_enrichment` is where that
  becomes the stored JSON string.)

### ID scheme (consolidated)
Entity keeps three id fields with documented roles (**`graph_identifier` deleted**):
- `unique_identifier` — stable per-record UUID (provenance / edge endpoints)
- `identifier` — the entity's working name
- `representative_identifier` — canonical name after dedup. **`EntityRecord.canonical_id`
  = representative_identifier or identifier (UPPER)** is the single key used for
  indexing, graph nodes, and edge endpoints.

> Not collapsed to a bare `id` + `canonical_id` pair (the lighter original
> sketch): `identifier` (raw working name) and `representative_identifier`
> (post-dedup canonical) carry distinct meaning and the dedup / build_graph /
> merge_states branches read both. Collapsing them is a separate, riskier rename
> deferred out of this migration.

### Step contract
Every pipeline step becomes an explicit transform over the state, e.g.:
```python
state = extract_entities(state, text, query)      # was named_entities_extractor_task (+ side effects)
rep_ids = resolve_representatives(state)
state = dedup_entities(state, rep_ids)
graph  = build_graph(state)                        # reads state, returns graph artifacts
state = enrich_edges(state, graph)
state = map_evidence(state, graph, hypothesis, subject)
state = triangulate(state, graph, threshold)
state.save(repo)
```
No hidden side-effects on a separate dict; the state object is threaded explicitly and owns its mutations.

### Loops, after the index exists
- `build_graph`: precompute `raw_id → canonical_id` once (dict) → inner `for record` scan becomes O(1) lookup; junctions can use a set of shared entities per chunk-pair without the O(chunks²) re-scan of all entities.
- `register_edges`: assign `unique_identifier` once per edge (single pass), endpoints resolved via `state.node(canonical_id)`.
- `add_relations_to_nodes` / `merge_states`: index lookups replace nested scans.
- `node_and_evidence_consolidator`: resolve evidence→node via index; keep shortest-path but over the (smaller) indexed node set.

---

## 3. Migration plan (each = its own smoke-tested commit)

1. ~~**Add `InvestigationState` + `ids.py`** (canonical_id helper), no call-site changes yet. Unit-test the index + load/save.~~ **DONE** (commit 46d77d3).
2. ~~**Adopt it in `_standard_pipeline`** for load/save + the local/current consolidation (behaviour-preserving translation of the dict juggling).~~ **DONE** — `current_*` snapshot + scattered `find/add/update/get_field` collapsed to one `load`/`save`; smoke-verified new-session + resume (runs 1→2, 5 nodes/5 edges, identical persisted fields). `local_investigation_state` dict still feeds the step fns (threaded in step 3).
3. **Add `EntityRecord` dataclass, no wiring** — `from_extraction`/`from_dict`/
   `to_dict` + `canonical_id` property; round-trip-equal to today's entity dict.
   Unit-test only (mirrors step 1: purely additive, pipeline unchanged).
4. ~~**Re-type the state**~~ **DONE** — `InvestigationState.nodes` is now
   `list[EntityRecord]` (index → `canonical_id → EntityRecord`); `load` parses
   persisted dicts via `from_dict`, `save`/HTTP/final-merge convert via
   `to_dict`. Extraction + the working node-set still use dicts (threaded in
   step 5). Smoke-verified: new-session + resume, 9 nodes/8 edges, persisted
   record shape intact (incl. `graph_identifier` preserved in `extra`).
5. **Index-ify `build_graph` + graph-bug cleanup** — **DONE.** Deleted the dead
   junction-detection block (its `junction_*` output is never read → bugs 1 & 4
   were in dead code; removed the O(chunks²) loop); replaced the per-affiliation
   linear record-scan + `nx.relabel_nodes` churn with a single precomputed
   `raw→canonical` map (endpoints resolved up front, no relabel); dropped the
   dead `fully_connected_edges_` guard (bug 3). `sim_model` no longer needed by
   `build_graph`. 9 unit tests; smoke-verified new + resume (status=success,
   8 nodes/10 edges). *Deferred to step 5b:* threading `EntityRecord` through
   extraction (emit records, drop the side-effect dict) + index-ifying dedup —
   the working set stays dicts for now; those land with the register_edges /
   consolidator work that actually consumes a `canonical_id → EntityRecord`
   index.
6. **Fix `register_edges` + dissolve bug 2** — **DONE.** `register_edges` now
   assigns `unique_identifier` exactly once per edge (was regenerated N_nodes×
   inside the per-node loop) and resolves endpoints through a node index keyed
   by identifier + representative_identifier (first-node-wins), replacing the
   O(nodes×edges) double loop; dropped the unused `coarse_grained_edges` param.
   Bug 2: removed the dead synthesized-edge append in
   `score_graph_by_connectivity` (those edges carried no `*_unique_identifier`
   and were always deleted by the orphan-edge cleanup) while keeping the
   `enriched_graph` build for shortest-path. **Decision: output-preserving** — I
   did *not* make the synthesized structural edges survive, because that would
   change what the API returns (a functional change for analysts, not a
   refactor). 7 unit tests; smoke-verified new + resume (status=success, 6/6,
   edges carry the clean field set, no leftover `nodeA/nodeB`).
   *Open functional question:* should structural (graph-only) affiliations
   appear in the output edge list? Tracked separately, not in this migration.
6b. **Add `EdgeRecord` + re-type `state.edges`** — **DONE.** `EdgeRecord` mirrors
   `EntityRecord` (None-sentinel optionals + `extra` bag, lossless round-trip);
   `state.edges` is now `list[EdgeRecord]` with `load`/`save`/HTTP/final-merge
   converting at the boundary (mirrors step 4). 4 new unit tests; smoke-verified
   new + resume (status=success, 7/7, runs 1→2).

   > **Architectural decision (revised):** `EntityRecord`/`EdgeRecord` are the
   > **persistence + API boundary** model, and the **working set stays dicts.**
   > Threading record *objects* through `build_graph`/dedup/consolidator/
   > `merge_states` is net-negative: those steps run on `SemHash.from_records`,
   > `merge_list_of_dicts`, and dspy `model_dump()` — all dict-centric — so
   > records would convert back to dicts at every boundary (churn) or force an
   > attribute-access rewrite of every graph function (risk) for little gain.
   > So the original step-5b/§2 aspiration ("the whole pipeline passes
   > record objects") is **dropped**: dicts are the right working-set form; the
   > typed records live at the edges (load/save/response), which is where they
   > earn their keep. Where the working set needs O(1) lookup (step 7), index
   > the **dicts** by `canonical_id` — no object conversion needed.
7. **Index-ify `add_relations_to_nodes` + `merge_states`** — **DONE.** Both now
   build a `canonical_id → record` index (and `merge_states` also indexes
   `saved_edges` by src/dst and by (src,dst) pair) instead of nested scans.
   `merge_states` was the real payoff — `saved_nodes`/`saved_edges` accumulate
   across runs, so its O(dedup×saved) scans degraded over a session's lifetime;
   now O(dedup+new). Marginal accepted shift: `add_relations_to_nodes` now
   resolves edges in a fixed nodeA-first order, so a relation's `related_node`
   (descriptive metadata) may be the canonical rep vs the raw name in the rare
   both-endpoints-match case — structural attachment + `triangulated` unchanged.
   10 unit tests; smoke-verified new + resume (status=success, 11/15, runs 1→2).
   **Evidence sub-record decision: keep nested dicts** (option a) — leaf data,
   not indexed/looked up; an `EvidenceRecord` would add ceremony for no win.

   > **Deferred (not done): `node_and_evidence_consolidator` + the
   > `score_graph_by_connectivity` removal loops.** These are the triangulation
   > *core* (which nodes end up `leaf`/`hypothesis`/proved). The consolidator's
   > inner node-scans are interleaved with order-dependent mutations (e.g. a
   > blanket `leaf=False` default) and an `nx.shortest_path` whose call count —
   > not just the inner scan — is the real cost; reducing it is algorithmic
   > surgery, not a mechanical index swap. With gpt-4.1 nondeterminism, the smoke
   > harness is too weak an oracle to catch a subtle triangulation regression
   > here. Recommend a dedicated pass with a fixed-fixture / mocked-LLM oracle
   > before touching it. Low current payoff (per-request, small N) reinforces
   > deferring.
8. **Dead-code sweep** — **DONE.** Deleted: the dead `graph_identifier` write
   (`dedup.py`); the unused `GraphBuilder` / `build_full_coarse_grained_graph` /
   `find_junction_nodes` scaffold (bug 5); the vestigial
   `InvestigationState.junction_names` / `junction_nodes` fields; the 5 dead
   dspy signatures (`EvidencesExtractor`, `FlatJsonToDocument`,
   `AllLabelsExtraction`, `AnalyzeContradictedEntities`,
   `CreateInvestigationExecutionPlan`) and the now-orphaned models (`Label`,
   `OrganizationProfile`). All ruff/pyflakes clean; graph + state + records unit
   suites green; smoke-verified (status=success, 10 nodes/13 edges). The sibling
   `crewai_mvp` keeps its own copies of these signatures, so deletion here is
   isolated. Id scheme is finalised: `unique_identifier` + `identifier` +
   `representative_identifier`, with `canonical_id` derived (see §2).

Smoke-test (`tests/run_smoke.py`) after each: assert `status=success`, 0
swallowed errors, valid graph, sane node/edge counts. Roll back the single
commit if a step regresses. Steps 1–2 already shipped (see above); this list is
re-sequenced from the original 7-step plan to introduce the dataclasses (the
dspy-signature-aligned records) before the index/loop work that depends on them.

---

## 4. Risk

Behaviour *will* shift at the margins (id consolidation changes how records
merge/dedupe). That's acceptable and intended — but it means no exact-parity
oracle. Mitigation: small commits, structural smoke assertions, and the
`master` baseline + Phase-1 tag remain available for A/B if a question arises.
