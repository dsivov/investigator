# The standing monitor (CEP / impact analysis)

Investigator is normally request-driven: you start an investigation and it builds
a graph. The **monitor** turns it into a *standing watch* — a scheduled job that
fetches fresh news, extracts events + actors, intersects them with the cumulative
knowledge graph, and propagates **impact** onto connected nodes (direct + hidden)
— surfacing *what moved in your graph*. Code: `src/investigator/monitor/`.

Phase 1 (current): a scheduled **impact digest**. CEP pattern rules over the
temporal layer are a later phase. The monitor is **read-only** — it never mutates
the KG. Design notes: [cep-monitoring-discussion.html](cep-monitoring-discussion.html).

## The daily pipeline

```
watchlist ─► fetch top-k news ─► extract + canonicalize (engine)
          ─► intersect with global KG (drop noise) ─► impact (connector + local BP)
          ─► ranked, thresholded digest (dated)
```

| Stage | What | Reuses |
|---|---|---|
| **Watchlist** | the entities you watch — the scoping primitive (`watchlist.json` in the KG store) | — |
| **Intake** | GNews per subject → POST to the engine → today's `{nodes, edges}` | `fetch_news`, engine `/api/v1/get_nodes` |
| **Intersect** | keep only events whose actors match a KG canonical (match-only `registry.lookup`, no minting) | `CanonicalRegistry` |
| **Impact** | per event: scope the touched entity's neighbourhood, raise its belief, run a **small local TMFG + junction-tree BP** → posterior **deltas** = the ripple; blend with proximity, recency (event date), watch-relevance | `connector`, `construct_tmfg`, `junction_tree_propagate`, the temporal layer |
| **Digest** | dated JSON under `news_investigations/monitor/`, ranked, with an alert threshold | — |

The impact score per affected node = `|Δposterior| × proximity_decay(hops) ×
event_strength × recency_decay(event_date) × watch_boost`. Edge strength comes
from attestation breadth (distinct citing sources). The local subgraph is capped
(`INVESTIGATOR_MONITOR_MAXLOCAL`, default 60) so a hub doesn't wash the ripple
out; BP falls back to a topological score when the neighbourhood is < 4 nodes.

## CEP pattern rules (Phase 2)

Beyond single-event impact, the monitor detects **multi-event temporal patterns**
— e.g. *"A sanctioned → B linked to A → C transacts with B within 30 days."* Code:
`monitor/patterns.py` + `monitor/rules.py`.

A **rule** is an ordered list of event **steps** + a window + severity, stored as
`rules.json` in the KG store (seeded with built-in defaults). A step matches an
event when `event.type` is in its `types` OR a `keyword` is in the description
(the KG's event types are clean categories: `sanctions`, `financial_crime`,
`indictment`, `military_action`, `diplomatic`, `bribery`, …). A rule matches when
there's a chronological event chain — one per step — where each consecutive pair
is within `windowDays` and **linked**: they share a participant, or a participant
of one is one hop from a participant of the next in the KG (the "B linked to A"
bridge). Linking through a high-degree **hub** (a country, a big agency) is
ignored — `INVESTIGATOR_CEP_HUB_DEGREE`, default 25 — so the bridge is a specific
actor. The digest runs the matcher over the cumulative KG **plus today's fresh
events** (so a new event can *complete* a pattern), scoped to the watchlist and
limited to chains whose final event is recent. `GET /api/monitor/patterns` scans
the whole KG unscoped; `GET/POST /api/monitor/rules` is the rule library.

## Running it

```sh
# manage the watchlist (canonical KG names)
PYTHONPATH=.:src python -m investigator.monitor --add "SAMIDOUN" --add "HAMAS" --show

# one-shot run (the engine must be up on :5003); writes a dated digest
PYTHONPATH=.:src python -m investigator.monitor --once --period 1d --k 8
```

Daily via cron (engine + this process share the env):

```cron
0 7 * * *  cd /path/to/context_graph && PYTHONPATH=.:src /path/to/python -m investigator.monitor --once --period 1d --k 8 >> /tmp/monitor.log 2>&1
```

From the app: the **Monitor** tab edits the watchlist, triggers a run
(`POST /api/monitor/run`, a background subprocess), and shows the dated digest —
events that moved, the impacted ripple (entity · Δ · hops · broker/watched), and
alerts. See [ui-api.md](ui-api.md) for `GET/POST /api/monitor/*`.

## Tuning (env)

| Var | Default | Meaning |
|---|---|---|
| `INVESTIGATOR_MONITOR_RADIUS` | 2 | hops of reach around a touched entity |
| `INVESTIGATOR_MONITOR_MAXLOCAL` | 60 | cap on the local subgraph size |
| `INVESTIGATOR_MONITOR_HALFLIFE` | 30 | recency half-life in days |
| `INVESTIGATOR_MONITOR_BETA` | 0.4 | BP coupling (lower = more discriminating) |
| `INVESTIGATOR_MONITOR_WATCHBOOST` | 1.5 | score multiplier for watched entities |
| `INVESTIGATOR_MONITOR_ALERT` | 0.2 | digest alert threshold |
| `INVESTIGATOR_CEP_HUB_DEGREE` | 25 | nodes above this degree are ignored as pattern bridges |

## Not yet (later phases)

Richer pattern language (variable binding, negation/absence); persisting
fired-pattern state to avoid re-alerting the same chain daily; feeding KG-relevant
monitored events back to grow each entity's timeline; per-watchlist isolation and
push/email delivery.
