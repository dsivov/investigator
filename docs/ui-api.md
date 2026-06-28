# Investigator UI — Backend API contract

This document defines the REST + SSE contract between the UI frontend
and the Investigator backend (`ui/server.py`).

The backend is a thin Flask service that wraps the pipeline scripts
(`research/cross_event_investigation.py`, `research/build_customer_report.py`,
`research/build_*_prototype.py`, `research/enrichment.py`) and exposes the
analysis + knowledge-base endpoints. It runs on the same host as the pipeline
engine; a single-tenant local-network deployment is assumed (no auth, no
multi-user).

---

## Resources

### Investigation

A single run of the pipeline against one (single-query mode) or
multiple (cross-event mode) GNews queries.

```json
{
  "id": "inv_01J5KCQH7G4ZB7Q5N4HCC3D2VS",
  "title": "Huawei / Toga Networks / Iran-protest surveillance",
  "kind": "multi",                       // "single" | "multi"
  "status": "succeeded",                 // queued | running | succeeded | failed | cancelled
  "domain": "supply_chain_human_rights",
  "period": "1y",
  "threads": [
    { "name": "huawei_toga_israel",         "query": "Huawei Toga Networks Israel R&D acquisition operations" },
    { "name": "huawei_iran_tech",           "query": "Huawei Iran sanctions technology supply equipment" },
    { "name": "iran_protest_surveillance",  "query": "Iran protests surveillance crackdown Chinese technology supplier" }
  ],
  "params": {
    "stage1_articles": 50,
    "stage2_articles_per_entity": 20,
    "top_n_entities": 8,
    "relevance_threshold": 0.55
  },
  "createdAt": "2026-06-06T14:24:01Z",
  "finishedAt": "2026-06-06T15:43:58Z",
  "summary": {
    "fetched": 352,
    "extracted_full_body": 265,
    "extracted_headline_only": 87,
    "nodes": 113,
    "edges": 159,
    "bridges": 6,
    "bridges_all_threads": 0,
    "themes": 67,
    "cross_event_themes": 32,
    "leads": 111,
    "asymmetric_corpus": true,
    "sparse_threads": ["huawei_toga_israel"]
  },
  "artifacts": {
    "raw_json":          "/api/investigations/inv_.../artifacts/raw.json",
    "customer_report":   "/api/investigations/inv_.../artifacts/customer_report.md",
    "analyst_review":    "/api/investigations/inv_.../artifacts/analyst_review.md",
    "graph_prototype":   "/api/investigations/inv_.../artifacts/graph.html",
    "tmfg_prototype":    "/api/investigations/inv_.../artifacts/tmfg.html"
  }
}
```

The `summary` block carries the headline numbers the frontend renders
on the Overview card without needing to load the full JSON.

### Domain

A reusable preset: hypothesis text + relevance threshold.

```json
{
  "id": "dom_sanctions_evasion",
  "name": "Sanctions evasion",
  "isPreset": true,                      // built-in vs user-created
  "hypothesis": "Does the entity engage in or facilitate activities designed to circumvent US, EU, or UN sanctions regimes...",
  "threshold": 0.55,
  "description": "Designed for investigations into sanctions circumvention, dark-fleet activity, dual-use technology trade.",
  "createdAt": "2026-06-01T00:00:00Z",
  "updatedAt": "2026-06-01T00:00:00Z"
}
```

### Artifact

A file produced by an investigation. Always served as a binary
download or rendered HTML; never embedded in JSON responses.

---

## Endpoints

### Investigations

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/investigations`                      | List investigations (paginated, newest first). |
| `POST` | `/api/investigations`                      | Create + enqueue a new investigation. Returns the created object with `status: "queued"`. |
| `GET`  | `/api/investigations/:id`                  | Get one investigation's full record. |
| `DELETE` | `/api/investigations/:id`                | Cancel if running; delete artifacts otherwise. |
| `GET`  | `/api/investigations/:id/stream`           | SSE stream of progress events while running. |
| `GET`  | `/api/investigations/:id/graph`            | Get the Cytoscape-ready payload for the Graph tab (the same shape `build_graph_prototype.py` produces today). |
| `GET`  | `/api/investigations/:id/tmfg`             | Get the Cytoscape-ready payload for the TMFG-themes tab. |
| `GET`  | `/api/investigations/:id/themes`           | Get themes list with members, weight, runs, attesting URLs. |
| `GET`  | `/api/investigations/:id/entities`         | Get entity table for the Data tab (paginated, sortable). |
| `GET`  | `/api/investigations/:id/events`           | Get event table for the Data tab. |
| `GET`  | `/api/investigations/:id/relationships`    | Get relationship table for the Data tab. |
| `GET`  | `/api/investigations/:id/sources`          | Get the bibliography (URLs grouped by publisher). |
| `GET`  | `/api/investigations/:id/artifacts/:name`  | Download a named artifact (raw.json, customer_report.md, graph.html, tmfg.html, analyst_review.md). |
| `GET`  | `/api/investigations/:id/log`              | Download captured subprocess stdout. Available for any investigation that ran on this server, including failed/cancelled ones. |

### Analysis (per investigation)

See [analysis.md](analysis.md).

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/investigations/:id/connect`          | Connector subgraph between selected entities. Body `{entities[], mode?, k?, maxHops?}`; `mode` ∈ `shortest_path` / `hidden` / `induced`. Returns `{nodes, edges, selected, connectors, brokers, paths, ...}`. |
| `POST` | `/api/investigations/:id/connect/analyze`  | LLM summary of the connected subgraph. Body `{entities[], mode?}`. Returns `{report}`. |
| `GET`  | `/api/investigations/:id/key-network`      | Automatic "key network": hidden-connections subgraph seeded with theme + bridge nodes. Returns the connector result + `seed` meta. |

### Knowledge base (cumulative cross-investigation KG)

See [knowledge-base.md](knowledge-base.md). Requires the analytics engine.

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/kb/stats`  | `{available, store, entities, edges, canonicals}`. |
| `POST` | `/api/kb/query`  | Query across all investigations. Body `{query, mode?, synthesize?, asOf?}`. Returns `{answer?, entities[], relationships[]}`; each entity carries its `structured` record (beliefs, evidence, sources, timeline, firstSeen/lastSeen); each relationship carries `firstSeen`/`activeWindow`. `asOf=YYYY-MM-DD` drops relationships not yet asserted by that date. |

### Sources & enrichment

See [sources.md](sources.md).

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/search-sources`              | Available search sources `{id, label, description, requiresKey, available}`. |
| `GET`  | `/api/investigations/:id/enrich`   | Enrichment status `{running, hasEnriched, recordCount}`. |
| `POST` | `/api/investigations/:id/enrich`   | Run EDGAR + OpenRegistry enrichment on the top company entities. Body `{topN?}`. |
| `GET`  | `/api/investigations/:id/enrichment` | The external records `{items[], total, ...}`. |

### Integrations (OpenRegistry login)

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/integrations/openregistry`          | Status `{connected, method, loginInProgress, authorizeUrl}`. |
| `POST` | `/api/integrations/openregistry/login`    | Start the one-time browser OAuth (spawns the login subprocess). |
| `POST` | `/api/integrations/openregistry/complete` | Finish a login the browser couldn't auto-redirect: body `{redirectUrl}`. |
| `POST` | `/api/integrations/openregistry/logout`   | Delete the stored token. |

#### `POST /api/investigations` request body

```json
{
  "kind": "multi",
  "domain": "supply_chain_human_rights",     // domain id, or null + supply `hypothesis` directly
  "hypothesisOverride": null,                // optional: override the preset's hypothesis text
  "thresholdOverride": null,                 // optional: override the preset's threshold
  "period": "1y",
  "threads": [
    { "name": "huawei_toga_israel",         "query": "Huawei Toga Networks Israel R&D acquisition operations" },
    { "name": "huawei_iran_tech",           "query": "Huawei Iran sanctions technology supply equipment" },
    { "name": "iran_protest_surveillance",  "query": "Iran protests surveillance crackdown Chinese technology supplier" }
  ],
  "advanced": {                              // all optional, sane defaults applied
    "stage1Articles": 50,
    "stage2ArticlesPerEntity": 20,
    "topNEntities": 8,
    "enhancedRetrieval": false,              // LLM query-expansion + rerank + entity-deepening
    "retrievalDepth": 2,
    "retrievalExpansions": 4
  },
  "gnewsEnabled": true,                       // search Google News
  "sources": ["wikipedia", "gdelt", "websearch"],  // additional search sources (see sources.md)
  "extraSources": { "urls": [], "pdfs": [] }  // your own sources; pdfs are upload ids from POST /api/uploads
}
```

Single-query mode: `kind: "single"` and `threads` has exactly one element. With
`gnewsEnabled: false` you must supply at least one `sources` entry, URL, or PDF.

#### `GET /api/investigations/:id/stream` (Server-Sent Events)

The progress stream emits one event per phase change. Each event has a
type and a JSON `data` field.

```
event: started
data: {"investigationId": "inv_...", "threads": ["huawei_toga_israel", ...]}

event: thread_progress
data: {"thread": "huawei_toga_israel", "phase": "fetch", "current": 12, "total": 50}

event: thread_progress
data: {"thread": "huawei_toga_israel", "phase": "extract", "current": 33, "total": 45}

event: thread_progress
data: {"thread": "huawei_toga_israel", "phase": "post_stage1", "stage": "running"}

event: thread_completed
data: {"thread": "huawei_toga_israel", "nodes": 3, "edges": 3}

event: cross_event_analytics
data: {"bridges": 6, "themes": 67, "leads": 111}

event: artifacts_ready
data: {"customer_report": "/api/...md", "graph_prototype": "/api/...html", ...}

event: succeeded
data: {"investigationId": "inv_...", "summary": { /* full summary block */ }}
```

Allowed phase values per thread:
`fetch` → `extract` → `post_stage1` → `pick_stage2_entities` → `fetch_stage2` → `extract_stage2` → `post_stage2` → `completed`.

Failure path:
```
event: thread_failed
data: {"thread": "...", "reason": "..."}

event: failed
data: {"investigationId": "...", "reason": "...", "partial": true}
```

Cancellation: client closes the stream and calls `DELETE /api/investigations/:id`.

#### `GET /api/investigations/:id/graph` response

Identical to today's `build_graph_prototype.py` `_payload(...)` shape.
Documented separately because the frontend Graph tab will hit this
directly instead of the prototype's inlined `PAYLOAD` variable.

```json
{
  "title": "Huawei / Toga / Iran-protest surveillance",
  "runs": ["huawei_toga_israel", "huawei_iran_tech", "iran_protest_surveillance"],
  "domain": "supply_chain_human_rights",
  "period": "1y",
  "bridges": [
    { "id": "IRAN", "runs": ["huawei_iran_tech", "iran_protest_surveillance"],
      "posterior": 0.998, "score": 0.609 }
  ],
  "nodes": [
    { "id": "IRAN", "label": "IRAN", "type": "entity",
      "runs": ["huawei_iran_tech", "iran_protest_surveillance"],
      "isBridge": true, "labels": ["TEHRAN", "IRAN INTERNATIONAL"],
      "evidenceCount": 47, "posterior": 0.998, "score": 0.609,
      "firstSeen": "2024-02-11", "lastSeen": "2025-09-30",
      "data": { "position": null, "location": null, "type": "GPE" } }
  ],
  "edges": [
    { "id": "0", "source": "IRAN", "target": "CHINA",
      "type": "affiliation", "rtype": "partnership",
      "context": "China and Iran are accelerating their move...",
      "url": "https://cnbc.com/...", "publisher": "CNBC",
      "firstSeen": "2024-03-02", "activeWindow": ["2024-03-02", "2025-01-15"] }
  ]
}
```

##### Temporal "as of" reconstruction

`GET /graph`, `/key-network`, and `/connect` accept optional query params that
reconstruct the graph as it was **known by** a date (observed-time semantics):

| Param | Meaning |
|---|---|
| `?asOf=YYYY-MM-DD` | Keep only what was asserted on/before this date. |
| `?from=YYYY-MM-DD&to=YYYY-MM-DD` | Windowed variant (elements active within the window). |

Per node/edge the payload now carries two timestamps (see
[data-model.md](data-model.md#temporal-layer)): `firstSeen` (observed time — the
earliest article publication date attesting it) and, for edges, `activeWindow`
(valid time — `[start, end]` inferred from the dated events both endpoints take
part in; `null` if none). Events expose their own date as `firstSeen`/`lastSeen`.
Filtering rules: events dated after the cutoff are dropped (with their
participation edges); edges are dropped if their earliest asserted date is after
the cutoff; **undated** edges/entities are kept; entities left with only
structural hub edges are pruned. Invalid or absent params return the full graph.
The frontend Graph tab does the same filtering client-side via an **As of**
slider, so positions stay stable while relationships appear over time.

#### `GET /api/investigations/:id/tmfg` response

Same shape as `build_tmfg_prototype.py` `_payload(...)`. Carries themes
+ member nodes + (attested, fillin) edges + per-theme attesting URLs.

#### `GET /api/investigations/:id/entities`

Paginated table for the Data tab.

Query params: `?page=1&pageSize=50&sort=evidenceCount&order=desc&search=huawei&threads=...&minEvidence=2&isBridge=true`

```json
{
  "page": 1, "pageSize": 50, "total": 113,
  "rows": [
    { "id": "IRAN", "type": "Actor", "threads": ["huawei_iran_tech", "iran_protest_surveillance"],
      "evidenceCount": 47, "confidence": "Likely",
      "topRelationship": "→ CHINA (partnership)" }
  ]
}
```

`events` and `relationships` follow the same shape, different column set.

#### `GET /api/investigations/:id/sources`

```json
{
  "publisherCount": 157,
  "topConcentration": 0.12,                 // top-3 share of total citations
  "publishers": [
    { "publisher": "reuters.com", "count": 41,
      "urls": [
        { "url": "https://reuters.com/...", "backsEntity": "IRAN", "backsEdgeType": "affiliation" }
      ]
    }
  ]
}
```

---

### Domains

| Method | Path | Purpose |
|---|---|---|
| `GET`    | `/api/domains`         | List all domains (presets + user-created). |
| `GET`    | `/api/domains/:id`     | Get one domain. |
| `POST`   | `/api/domains`         | Create a new user domain. Body: `{name, hypothesis, threshold, description}`. |
| `PATCH`  | `/api/domains/:id`     | Update a user domain. Cannot modify presets (returns 403). |
| `DELETE` | `/api/domains/:id`     | Delete a user domain. Cannot delete presets. |

#### Per-investigation hypothesis override

Investigations can take a hypothesis override at creation time even
when pointing at a preset. The override is recorded on the
investigation record so historical runs are self-documenting; the
underlying domain preset is not mutated.

---

## Error model

All errors share one shape. HTTP status carries the broad class; the
`code` is the specific failure.

```json
{
  "code": "thread_validation_failed",
  "message": "Thread name 'huawei toga israel' must be a snake_case identifier (letters, digits, underscores).",
  "field": "threads[0].name"
}
```

Codes the frontend should know about:

| Code | When |
|---|---|
| `validation_failed`          | Generic input shape error. |
| `thread_validation_failed`   | Thread name / query / quantity error. |
| `domain_not_found`           | Referenced domain id does not exist. |
| `domain_is_preset`           | Tried to PATCH or DELETE a preset domain. |
| `investigation_not_found`    | Investigation id does not exist. |
| `investigation_not_finished` | Tried to fetch an artifact while the run is still active. |
| `investigation_failed`       | Tried to fetch artifacts of a failed run. Some artifacts may still be partial. |
| `pipeline_unavailable`       | Underlying OSINTGraph server is down. The investigation is queued but cannot start. |
| `gnews_quota_exceeded`       | GNews returned a rate-limit error during fetch. |
| `internal`                   | Catch-all 500. |

---

## Versioning and headers

- Every JSON response carries `X-OSINTGraph-Schema: 1`. Increment when
  a breaking change to any response shape lands; minor additive
  changes (new optional fields) do not bump the version.
- `GET` endpoints are idempotent and safe to cache for the lifetime
  of a finished investigation. Frontend should set `If-None-Match`
  using the artifact mtime-derived ETag the backend returns.
- `POST /api/investigations` is _not_ idempotent. If the frontend
  needs to deduplicate retries, it should supply an
  `Idempotency-Key: <uuid>` header; the backend will return the
  existing investigation if one was created with the same key in the
  past 24 hours. When the key replays, the response carries
  `idempotent_replay: true` and the current job status; this lets
  the client distinguish "I just created it" from "this was already
  in flight".

## Concurrency, persistence, and recovery

- The backend processes investigations through a FIFO worker pool
  (`INVESTIGATOR_UI_MAX_CONCURRENT`, default `1`). Each investigation
  consumes the underlying OSINTGraph server's LLM-rate budget; running
  two in parallel does not improve wall-clock time, so the default
  cap is intentional. Raise the env var if you give the backing
  server more headroom.
- `POST /api/investigations` returns immediately with status
  `queued` and a `queuePosition` field. The status flips to `running`
  when the worker pulls the job; the `/stream` SSE feed carries the
  transition.
- Every job persists a recovery record to
  `news_investigations/jobs/<id>.json` on each status change. Stdout
  is mirrored to `<id>.log`. On server restart, in-flight jobs are
  marked `failed` (the subprocess is dead) but their records remain
  in the `/investigations` list and their logs remain downloadable.

---

## Auth (deferred)

v1 is single-tenant local-network. No auth. A future tenant-isolation
pass will add Bearer-token auth and per-investigation ACLs; the
endpoint paths and shapes above are designed to be tenant-prefixable
without breaking.

---

## Open questions

1. **Investigation comparison**. The frontend will eventually want to
   diff two investigations (e.g., Russia run before vs after the
   title-only fallback). This is out of scope for v1 but the resource
   model already supports it (no breaking change required to add a
   `GET /api/investigations/:a/compare/:b` later).

2. **Streaming partial results**. Today the cross-event pipeline does
   not emit per-thread partial graphs until Stage 2 finishes. Once it
   does, the SSE stream can carry partial graph snapshots; the
   frontend can pre-render the Stage-1 sub-graph while Stage 2 runs.
   The contract should be additive (`event: partial_graph`).

3. **Re-running with edits**. The frontend lets the analyst tweak
   queries / domain / period from an existing investigation and
   re-launch. This is a `POST /api/investigations` with the prior
   record as the body. The backend will set `parentId: "inv_..."` on
   the new run so the UI can show "branched from".
