# Investigator — documentation

Investigator is an OSINT analysis pipeline that reads a corpus of news (and other
sources) about several separate stories and surfaces the **actors, events, and
relationships that connect them**, with every claim traced to a source URL. It
accumulates findings into a persistent cross-investigation **knowledge graph** you
can query, and provides interactive analysis (hidden-connection discovery,
fact-checking, automatic "key network" skeletons).

Start with the top-level [`README.md`](../README.md) for the product overview and
quick start. The documents here go deeper.

## Current docs

| Doc | What it covers |
|---|---|
| [architecture.md](architecture.md) | The three processes (engine / UI backend / frontend), ports, and how they fit. |
| [pipeline.md](pipeline.md) | How one POST of source material becomes a scored, triangulated graph (the 7 stages + cross-event merge). |
| [data-model.md](data-model.md) | Entity / event / edge records, the cumulative-KG stores, and what each field means. |
| [knowledge-base.md](knowledge-base.md) | The cumulative cross-investigation KG: in-code LightRAG, canonicalization, the structured + temporal sidecar, retrieval, and the Knowledge Base tab. |
| [analysis.md](analysis.md) | Interactive analysis: Connections (shortest-path / hidden / brokers), the automatic Key network, and multi-source corroboration. |
| [monitoring.md](monitoring.md) | The standing monitor (CEP / impact digest): watchlist, daily intake, intersection, the local-TMFG belief-propagation impact model, and the Monitor tab. |
| [sources.md](sources.md) | Configurable search sources (Wikipedia / GDELT / OpenSanctions / web) and entity enrichment (SEC EDGAR / OpenRegistry). |
| [ui-api.md](ui-api.md) | REST + SSE contract between the frontend and the UI backend. |
| [operations.md](operations.md) | Running locally, environment variables, memory / OOM tuning, and troubleshooting. |
| [roadmap.md](roadmap.md) | Functional analysis and where the product is heading. |
| [cep-monitoring-discussion.html](cep-monitoring-discussion.html) | Open design discussion: turning Investigator into a standing CEP / impact monitor. |

## Reference / historical

| Doc | What it covers |
|---|---|
| [blog.md](blog.md) | Narrative walkthrough of the core idea (cross-story bridges). |
| [reviews/triangulation-review.md](reviews/triangulation-review.md) | Stage-by-stage logical review of the triangulation core, validated against a golden snapshot. |

> Note on naming: older design docs use the project's former name **OSINTGraph**;
> it is the same system, now **Investigator** (package `src/investigator`).
