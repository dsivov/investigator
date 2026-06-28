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

## Not yet (later phases)

CEP pattern rules (multi-event temporal patterns → alerts); feeding KG-relevant
monitored events back to grow each entity's timeline; per-watchlist isolation and
push/email delivery; de-duping repeated daily alerts.
