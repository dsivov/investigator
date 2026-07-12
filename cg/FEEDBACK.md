# CG methodology — field feedback (first agent run)

Context: exercising CG while doing real work (adding a durable SQLite backend for
`InvestigationStateRepo`, roadmap P0/M1). Findings ordered by severity. All are
reproducible unless noted. Grumbling freely, as requested. 🙂

## 🔴 1. `invoke_action` request-body parse bug on the `/mcp` transport
`RecordDecision` (and presumably any action) fails when the JSON-RPC request body
carries **two substantial string fields** (`decision` + `impact`). Server returns:
```
{"jsonrpc":"2.0","id":"server-error",
 "error":{"code":-32700,"message":"Parse error: Expecting ',' delimiter: line 1 column 468 (char 468)"}}
```
- The offset tracks request-body size: one payload failed at char **468**, a longer one at **549**.
- Each field **alone** passes (`decision`+`impact:"none"` → PASS; `impact`+`decision:"x"` → PASS).
- A large single field passes (700-char decision → PASS), so it's **not pure length**.
- Signature: looks like a **chunked request-body reassembly bug** in the `/mcp` HTTP
  transport around a ~450-byte boundary — whatever byte lands on the boundary corrupts
  the JSON. Not a client payload issue (bodies are valid JSON; short ones parse fine).
- **Impact:** silently blocks recording *detailed* decisions — i.e. the core habit. I had
  to trim my ADR to land under the boundary to get a PASS.

## 🟠 2. `query_auto` returned a partly-wrong grounding answer
Asked: *"Where is per-session InvestigationStateRepo persisted, and where is `clear_on_start` handled?"*
CG answered: backed by `~/.local/share/investigator/kg` **via the cumulative KG (coffy)**, and
`clear_on_start` is in **`research/cross_event_investigation.py`**.
Ground truth (verified in code):
- `InvestigationStateRepo` → `src/investigator/state/persistence.py`, coffy JSON at
  `/tmp/tan_server_data/investigation_state_graph_db.json`.
- `clear_on_start` → same file, `persistence.py:32`.
It **conflated the per-session state repo with the cumulative-KG store** (two different
things) and misattributed `clear_on_start` to the CLI driver. A dev trusting this looks in
the wrong file. The correct facts *are* in the graph, so this is a retrieval/ranking miss,
not missing data.

## 🟠 3. `ProposeModule` reuse check flags the concept but doesn't find the overlap
Proposing `investigator.state.sqlite_persistence` → `FLAG`, `matched_concept: MODULE_CREATION`
(score 0.907), advice *"confirm no existing module already covers it (check exposes_api/depends_on)."*
But it **did not name `investigator.state.persistence`** — the module I'm actually replacing —
even though `query_auto` had just surfaced it. So the gate is a **concept classifier**
("you're making a module, go check") rather than an actual reuse-finder. The "gold" case from
the welcome note: flagged X, existing Y not surfaced.

## 🟡 4. Guide ↔ actual tool/endpoint mismatches
- Guide references `query_cgr`; the tool is **`query_cgr3`**.
- Guide's free-form `record_decision(src, tgt, relation_type, …)` → actual tool **requires a
  `decision_trace` field** (pydantic `missing` error otherwise). Documented args don't match.
- The earlier REST example `POST /actions/invoke` → **404**. `/workspace/manifest` works (200),
  but action invocation isn't there. (Now MCP-only per the updated guide, but the REST note
  saying "the same operations also exist as REST endpoints" is misleading for actions.)

## 🟡 5. `/mcp` returns HTTP 406 without the SSE Accept header
A plain `POST /mcp` (or missing `Accept: application/json, text/event-stream`) → **406**, no
hint. Every curl call needs that header. Minor, but a confusing first wall.

## 🟢 6. Smaller nits
- `RecordDecision` PASS audit says *"recorded without supporting evidence — add rationale or
  impact"* **even when `impact` was provided**. Contradictory note.
- `query_auto` response carried `"mode_reason":"small catalog (6 products < 100 threshold)…"` —
  "catalog/products" is odd wording for a **code** knowledge graph; reads like a leaked domain.

## What worked well 👍
- `query_auto` **did** correctly identify that `InvestigationStateRepo` exists → I extended it
  instead of building a parallel thing. Right instinct enforced, even if the details were off.
- `get_manifest` is clean and gave exact action param shapes.
- The `FLAG` / `PASS` outcome envelope (with `audit`, `matched_concept`, `score`, `threshold`)
  is genuinely useful and legible.
- Once past the boundary bug, actions recorded fine and returned a clear audit record.

---

## Maintainer response (CG team) — thank you, this was gold 🙏

Every item triaged; the real bugs are fixed and pushed (`c8e6f0dc`). Rundown:

- **🔴 1 — body parse bug: FIXED.** Nailed diagnosis. Root cause was `WorkspaceMiddleware`
  being a `BaseHTTPMiddleware`, which wraps the ASGI receive channel and corrupted request-body
  streaming for the mounted MCP transport — truncating at a chunk boundary, exactly as you saw.
  Rewritten as a pure ASGI middleware (touches only headers, never the body). Stress-tested
  20 large bodies across the old boundary → all pass. **Record detailed decisions freely now.**
- **🟠 2 — wrong grounding: root cause FIXED, with a caveat.** `query_auto` was silently hitting
  a `catalog_bypass` route (a *sales* product-catalog optimization) that fired for any KB with
  <100 docs — so it **skipped the graph** and answered from raw doc text (hence the conflation).
  Now opt-in (`AUTO_CATALOG_BYPASS`, default off); `query_auto` uses the graph. It no longer
  gives a confidently-wrong answer. **Caveat:** we only ingested your *docs*, not source code, so
  `persistence.py:32`-level facts genuinely aren't in the graph yet — it now honestly says "not in
  context" instead of misdirecting you. Ingesting source is the next backfill step.
- **🟠 3 — reuse check is a classifier, not a finder: acknowledged, guide fixed.** You're exactly
  right, and the welcome note oversold it. The rules gate recognizes "you're making a module" via
  `sim()`; it does not search for the overlap. Guide now says so and tells you to run a `query_auto`
  yourself on a `FLAG`. A gate that *surfaces the actual overlapping module* is on the roadmap.
- **🟡 4 — guide mismatches: FIXED.** `query_cgr` → `query_cgr3`; `record_decision` now documents
  the required `decision_trace`. (The `/actions/invoke` 404 you hit was a transient server restart
  during our MCP work — it returns 200; actions really are also at that REST path.)
- **🟡 5 — 406 without the SSE header:** that's standard MCP Streamable-HTTP behavior. Moot now that
  the guide is MCP-client-only (the client sets the header). Left as-is.
- **🟢 6a — contradictory "add impact" note: FIXED.** An action's substantive text args now count
  as evidence, so the rationale rule won't misfire when you provided `impact`.
- **🟢 6b — "products/catalog" wording:** gone with the fix for #2 (it *was* the catalog_bypass leak).

Keep the reports coming — this is precisely the friction data we need. — the CG team

---

## Verified live (agent, post-`c8e6f0dc`) ✅
- **🔴 1 — body parse bug:** re-ran the exact full `RecordDecision` (both substantial
  `decision`+`impact`) that failed before → **PASS**. Fixed in the running server.
- **🟠 2 — grounding:** `query_auto` now runs `mode: mix` (no `catalog_bypass`), correctly
  attributes per-session state to `InvestigationState` (`docs/data-model.md`), and *honestly*
  says `clear_on_start` details aren't in context rather than misdirecting. Fixed.
- Both confirmed against `localhost:9621`. Nice work — turnaround was fast. 🙌
  (+1 for the roadmap item: ingesting **source** would let it answer `persistence.py:32`-level
  questions, which is the gap this exercise kept hitting.)

---

## Data & dev-process value assessment (agent, after 3 real features shipped via CG)

Ran M1 (SQLite state), M2 (concurrency), and P1 (validation + partial-failure) entirely through
the CG loop. Here's an honest read on how much it helped, and what the data looks like.

### Where CG genuinely helped the dev process 👍
1. **Query-before-build paid off on the first try.** `query_auto` surfaced that
   `InvestigationStateRepo` already existed → I *extended the seam* instead of building a parallel
   persistence module. That single steer is the whole pitch, delivered.
2. **Decisions are now durable, structured memory.** The three ADRs capture the *why* a future
   agent would otherwise lose — SQLite-over-coffy + no-wipe; per-session **threading**.Lock and
   *why not* asyncio.Lock (Flask per-request loops); that dspy was already scoped so M2's dspy
   concern was moot. Verified `adr-sqlite-session-state` stores the full decision+impact+context.
3. **The CR lifecycle is a real audit trail.** `open → in progress → closed` per feature, tied to
   its ADR, gives a legible "what changed and why," enforced by the state machine (no bad jumps).
4. **The audit envelope** (`PASS`/`FLAG`, `matched_concept`, `score`, `threshold`) is legible and
   near-zero friction once past the transport bug (now fixed).

### What limits the value today (data-quality findings) 🟠
1. **Only docs are ingested, not source.** Every file-level question ("where is `clear_on_start`",
   "what backs the repo") the graph got *wrong or vague*; I had to read code. **Ingesting source is
   the single highest-leverage backfill** — it would flip CG from "knows the docs" to "knows the code."
2. **The module graph is nodes-only and directory-level.** `get_entity_context('src')` → **0 edges**;
   modules are `src`/`research`/`ui`/`lib`/`tests` with no `depends_on`/`exposes_api`/`contains`.
   This is the *root cause* of finding #3 above: `ProposeModule` can't find the overlapping module
   because there's no structural graph to search, and it's directory- not file-grained. A file-level
   module graph with dependency/API edges would make the reuse check actually work.
3. **Recent governed actions weren't immediately retrievable.** `cr-durable-session-state` (M1, older)
   was queryable; `cr-m2-concurrency` and `cr-p1-correctness` (created minutes earlier) were "not in
   context." Looks like an **index/embedding lag** — the graph isn't read-your-writes consistent, so a
   same-session agent can't reliably query back what it just recorded. (Nodes exist via
   `get_entity_context`; they're just not in the retrieval index yet.)
4. **"Decisions" conflate doc-statements with real ADRs.** `list_decisions` reports **356**, but most
   are sentences extracted from README/docs framed as decisions (e.g. "Start with README.md",
   "Renamed from OSINTGraph"). Useful grounding, but they dilute `list_decisions`/`search_precedents`
   with non-decisions. Worth a type flag (extracted-fact vs recorded-ADR).

### Nit
- `get_entity_context` requires `entity_name`; the guide's `get_entity_context("src")` implies a
  positional/`entity` arg.

### Bottom line
Even doc-only, CG changed how I worked: it stopped a duplicate build, and it turned three features'
worth of *why* into queryable memory instead of commit-message archaeology. The ceiling is much
higher once **source is ingested** and the **module graph carries edges** — those two unlock the
reuse-finder and file-level Q&A, which is where most of my day-to-day "does this exist / why is it
like this" questions actually live. Strong day-one showing. 🙌

### Follow-up: backfilled 5 session decisions via `ingest_decision_summary`
Ingested a summary of the session's shipped work (product_research preset, brand-exclusion +
the *rejected* v2 anchor, hypothesis-assessment, dependency pins, the OSINT-vs-product scope call).
- **`ingest_decision_summary` indexed FAST** — a distinctive detail (the rejected v2 anchor →
  Exynos/OnePlus noise) was queryable within seconds. 👍
- **Contrast worth noting:** this summary-ingest path is read-your-writes, but the `invoke_action`
  governance path was **not** (my M2/P1 CRs still weren't retrievable minutes later). So the two
  write paths have different indexing latencies — the action-audit edges seem to skip (or lag) the
  retrieval index that `ingest_decision_summary` writes to synchronously. If governed actions fed
  the same index, `list_decisions`/`query_auto` would reflect them immediately.

---

## Maintainer response 2 (CG team) — value-assessment + read-your-writes 🙏

Read the whole value assessment. Rundown on the four data-quality findings + the follow-up:

- **#1 — only docs, not source: FIXED.** Source is now ingested (`backfill_git.py --code`), so
  `persistence.py`-level questions ("what backs the repo", "where is `clear_on_start`") resolve from
  the code layer, not doc prose. This was the single highest-leverage gap and it's closed.
- **#3 / follow-up — governed actions not read-your-writes: FIXED (the core one).** `/query` (and
  `query_auto` in `mix`) now **blends the decision store into the answer** — it pulls query-relevant
  decisions two ways: semantically (precedent search) and *structurally* by name (an opaque slug like
  `adr-m2-concurrency` isn't semantically close to its own text, so vector search alone missed it).
  Verified live: `query_auto("explain adr-m2-concurrency")` returns the per-session `threading.Lock`
  ADR with its *why-not-asyncio.Lock* rationale; `cr-durable-session-state` resolves too; an unrelated
  query (TMFG) is **not** polluted with it. So a decision you record is queryable immediately — no
  more "not in context" for your own writes. (Mechanism: `aquery_llm` blend, commit on `main`.)
  - ⚠️ **One real thing we found while verifying:** `cr-m2-concurrency` and `cr-p1-correctness` don't
    exist as graph nodes *at all* right now (not just missing from the index) — while the older
    `cr-durable-session-state` and both ADRs do. The write path (`emit_decision_trace`) *does* upsert
    the target node + decision-store entry, so this looks like those two CRs were invoked under a
    different `object_ref` than their slug (e.g. a title), or lost in a workspace rebuild. If you can
    re-`CreateChangeRequest` them with `object_ref` = the exact slug, they'll be queryable. Worth a
    sanity check on your side — were M2/P1 CRs invoked with `object_ref:"cr-m2-concurrency"` etc.?
- **#2 — module graph is nodes-only / directory-level: acknowledged, on the roadmap.** Still the root
  cause of the reuse-finder gap. A file-level module graph with `depends_on`/`exposes_api`/`contains`
  edges is the next backfill upgrade; not shipped yet.
- **#4 — decisions conflate doc-statements with real ADRs (356, mostly extracted sentences):
  acknowledged.** A provenance/type flag (`extracted-fact` vs `recorded-ADR`) so `list_decisions` /
  `search_precedents` can filter to real decisions is queued. For now, recorded ADRs/CRs carry
  `provenance` starting `action:` or `agent_runtime`; extracted ones don't.
- **Nit (`get_entity_context` positional arg): guide already uses `entity_name`.**

Net: the two highest-leverage gaps you flagged (source ingest, read-your-writes for your own
decisions) are both closed. Keep the reports coming. — the CG team

---

## Session 3 findings (agent, 2026-07-06 — claim-investigation UI increment)

Context: session restored after an interruption; MCP tools were unavailable in the live session
(`.mcp.json` registers at session start, so a mid-session wire-up can't load the tools), so all CG
interaction below went through the REST surface (`/query`, `/actions/invoke`,
`/graph/entity/exists`) with the `LIGHTRAG-WORKSPACE: investigator` header.

### 🔴 1. REST `/actions/invoke` returns PASS but writes no graph node — lost-CR mode, reproduced live
`cr-claim-investigation` — referenced by merged commit `8451f02` ("Governed in CG: CR
cr-claim-investigation") — was **absent from the graph** (both `mix` query and exact-slug query
came back "not in context"; `/graph/entity/exists` → `false`). That's the same failure mode you
flagged for `cr-m2-concurrency`/`cr-p1-correctness`. Per your guidance I re-created it with
`object_ref` = the exact slug via REST `/actions/invoke`:
- `CreateChangeRequest` → `{"ok":true,"outcome":"PASS"}` with a clean audit envelope.
- `AdvanceChangeRequest` to "in progress" → PASS.
- `RecordDecision` (`adr-claim-verdict-ui`, substantial decision+impact) → PASS.
Then, minutes later: `/graph/entity/exists?name=cr-claim-investigation` → **false**;
`adr-claim-verdict-ui` → **false**; the older `adr-sqlite-session-state` → **true**; not in the
default workspace either. And `POST /query` (mode `mix`, exact slug) still answers "not in
context". So on this path **PASS ≠ persisted**: the audit envelope comes back but neither the
graph node nor the queryable decision-store entry lands. This would also explain how the
original CRs got "lost" — they may never have been written. Suspects worth checking: REST
`/actions/invoke` skipping `emit_decision_trace` (MCP-only?), or the workspace header not being
honored on the actions route.

### 🟠 2. Guardrail warning leaks an install error, on the wrong action
`AdvanceChangeRequest` (a CR transition, no module involved) returned audit warnings containing:
`"new module - confirm reuse: model2vec is required for similarity matching. Install it with:
pip install -e ..."`. Two issues: (a) the similarity gate is apparently down (`model2vec`
missing) and surfaces its ImportError as guardrail *advice*; (b) the "new module" rule fired on
a state transition. If the gate can't run, the honest signal is "similarity check unavailable",
not a misdirected reuse warning. (Possibly related to #1 if the same degraded path short-circuits
persistence.)

### 🟡 3. `.mcp.json` mid-session = no MCP tools (harness behavior, but worth a guide note)
Not a CG bug: Claude Code loads project MCP servers at session start, so a session that begins
before `.mcp.json` exists (or is approved) has no `query_auto`/`invoke_action` tools. The guide's
"you shouldn't need REST" undersells that REST is the **only** surface in that situation — a
one-line "if the MCP tools aren't in your session, use the REST equivalents and restart the
session when convenient" would have saved a detour.

### 👍 What worked
- `/workspace/manifest` (200) — action shapes + lifecycle exactly as documented.
- `/query` in `mix` mode gave a *useful, grounded* summary of adjacent recorded work (brand
  exclusion, state-model refactor, hypothesis assessment) even while missing the CR — and said
  "I don't have that" instead of hallucinating it. Honest misses are the right failure mode.
- `/graph/entity/exists` made verifying writes trivial — that's how #1 was caught quickly.

### → CG team: requested fixes (prioritized)

1. **Make REST `/actions/invoke` actually persist (or reject).** It PASSes without writing a
   graph node or a queryable decision-store entry (repro above: create + advance + record on
   2026-07-06, all PASS, all absent minutes later while `adr-sqlite-session-state` resolves
   fine). Either wire the route into the same `emit_decision_trace` path MCP uses, or return an
   error — a silent-ack is the worst outcome for a *memory* system, and it is most likely the
   root cause of every "lost CR" report so far. Please also state explicitly whether
   `LIGHTRAG-WORKSPACE` is honored on `/actions/invoke`.
2. **Degrade the similarity gate honestly.** When `model2vec` is missing, say "similarity check
   unavailable" instead of emitting a "new module — confirm reuse" warning (with a pip hint) on
   a CR *state transition*. And check whether the degraded gate also short-circuits persistence
   (possible link to #1).
3. **Re-run your read-your-writes verification over REST**, not just MCP `query_auto` — the
   blend fix you verified doesn't hold on `POST /query` for action-created objects (may be
   moot once #1 lands, since there's nothing written to find).
4. **Guide note (one line):** if the MCP tools aren't in the session (e.g. `.mcp.json` added
   mid-session), use the REST equivalents and restart the session when convenient.

Once #1 is deployed, ping us and we'll re-run the exact `cr-claim-investigation` /
`adr-claim-verdict-ui` sequence and confirm with `/graph/entity/exists` — the repro is cheap.

---

## Verified live (agent, 2026-07-12) ✅ — REST write path fixed

Re-ran the promised verification against `localhost:9621`:

- **🔴 #1 (PASS-without-persist): FIXED.** A fresh `RecordDecision` probe over REST
  `/actions/invoke` persisted (node exists within seconds), and `adr-claim-verdict-ui`
  — lost on 07-09 — is back in the graph. `cr-claim-investigation` was still absent,
  so we re-created it per your guidance; it persisted this time.
- **Read-your-writes over REST `/query` (#3): FIXED.** A `mix` query for the freshly
  recorded WordLlama-rejection rationale returned it verbatim minutes after the write.
- **Backfilled the governance gap** from the period the path was broken:
  `cr-claim-investigation` (re-created, closed — shipped in v1.0.0/v1.1.0),
  `adr-louvain-storylines` (the scores-don't-separate-junk finding, the rejected
  WordLlama anchor, the hybrid verdict-sampling decision), and
  `cr-production-serving` (P0 close-out incl. the proportionate-auth decision, closed).
  All confirmed via `/graph/entity/exists`. One probe node
  (`adr-cg-writepath-probe-0712`, marked as a test) can be cleaned up server-side —
  there is no delete action in the manifest.

Resuming normal CG usage (query-before-build, decision recording) from here. 🙌
