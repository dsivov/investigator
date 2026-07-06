# Working with Context Graph — investigator dev agent

Context Graph (CG) is this project's **shared, decision-aware memory**. It runs at
`http://localhost:9621`, workspace **`investigator`**. It already knows the
project's modules, commits, and architecture (backfilled from git + docs), and it
records *why* things were decided so a later session inherits the reasoning.

**One surface: MCP** (already wired via `.mcp.json`). Everything — querying,
recording decisions, discovering what you can do, and invoking governed actions —
is an MCP tool. (The same operations also exist as REST endpoints if you ever
want to `curl` them, but you shouldn't need to.)

## The three habits (in order of importance)

### 1. Query before you build
Never assume something doesn't exist. Ask CG first — it saves you rebuilding and
grounds you in the real codebase.

- `query_auto("Is there already a module that does X? What handles Y?")` — auto-routes.
- `query_cgr3("Why was the triangulation rooted on entities, not headlines?")` — multi-hop "why".
- `search_precedents("waiving …")` / `get_entity_context("src")` — precedent + a module's edges.

> **Which tool finds what:** `query_auto` (and `/query` in `mix` mode) now **blends recorded
> decisions into its answer** — it searches the decision store *alongside* the code/doc index,
> so `query_auto("explain adr-m2-concurrency")` or `query_auto("why threading.Lock for same-session
> concurrency")` returns the recorded ADR/CR with its full rationale, even by opaque slug. This
> closes the read-your-writes gap: a decision you just recorded is queryable right away.
> `query_cgr3` multi-hop still retrieves only **extracted** entities/doc-code chunks (not the
> decision store) — for a pure decision recall prefer `query_auto`, or `search_precedents("<topic>")`
> (semantic) / `get_entity_context("<its-name>")` (exact) as before.

If a query returns nothing useful, that's itself a finding — **report it** (see the welcome note).

### 2. Record the decision, not the keystroke
When you make a choice worth remembering — a design decision, a tech pick, an API
contract, a rejected option — capture the **why**. This is the whole point.

- `record_decision(src, tgt, relation_type, decision_trace, ...)` via MCP for a free-form
  decision — `decision_trace` (the *why*) is **required**; the rest is optional — **or**
- a typed **action** (below) for the standard operations.

Filter: *if you can't say who decided it and why, it's telemetry, not memory* — skip it.

### 3. Use the governed actions for standard moves (MCP)
Discover what you can do with the **`get_manifest`** tool — it returns the actions
you may invoke, the object types, guardrails, and lifecycle. Then use the
**`invoke_action`** tool — each call is validated, may be flagged by the
methodology gate, and is written to the graph as an audit record:

```
invoke_action(action="ProposeModule", object_ref="<module>", args={"name": "<module>"})
invoke_action(action="AdvanceTask",   object_ref="<task>",   args={"to": "completed"})
```

| Action | Use it when |
|--------|-------------|
| `ProposeModule` | Before adding a new module/component — **reuse-checked**, may return `FLAG` |
| `RecordDecision` | Recording an ADR — `{decision, impact}` |
| `CreateChangeRequest` | Opening a CR — `{title, description}` |
| `AdvanceTask` | Moving a task `pending → completed` — `{to}` |
| `AdvanceChangeRequest` | CR `open → in progress → closed` — `{to}` |

## Read the signals
- **`FLAG`** (e.g. ProposeModule → "confirm reuse") — *advice*, not a block, and **not a
  reuse-finder**: it recognizes that you're creating a module and tells you to check — it does
  **not** name the overlapping one. So when you see it, run a `query_auto`/`get_entity_context`
  yourself to find any existing module that already covers it, then proceed (reusing, or with a
  reason if it's genuinely new). *(A gate that actually surfaces the overlap is on the roadmap.)*
- **`409` illegal transition** — the task/CR state machine refused an illegal jump
  (e.g. a completed task can't go back to pending). That's the guardrail working.
- **`200 PASS`** — recorded, nothing to review.

## What you don't need to worry about
This is a **single-agent** setup: no roles, no permissions to fight — every action is
open to you. RBAC only appears if this project later grows to multiple agents.
