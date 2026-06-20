"""Connector subgraph: how a chosen set of entities/events interconnect.

Given the UI graph payload (nodes + edges) and a set of selected node ids,
build a focused subgraph that reveals the relationships AND the intermediary
("connector") entities linking the selection -- for relationship analysis.

Modes:
  * ``shortest_path`` -- one shortest path per selected pair (the thinnest, most
    obvious route). Good default; misses indirect chains.
  * ``hidden`` -- up to ``k`` shortest *simple* paths per pair (Yen's algorithm),
    so non-obvious multi-hop relationships surface even when a direct edge
    exists. This is the "find hidden actor/event relationships" mode.
  * ``induced`` -- only the selected nodes and the edges directly between them.

In every mode the intermediary (connector) nodes are ranked by BETWEENNESS
within the resulting subgraph; the central ones are flagged as ``isBroker`` --
the key hidden connectors that bridge the selection (cf. structural-hole /
broker analysis).

Pathfinding runs UNDIRECTED on the semantic relationship edges only -- the
``structural`` (evidence -> relevance-root) hub edges are excluded, else every
pair would connect through the root in ~2 hops and the connector is meaningless.
"""
from __future__ import annotations

import networkx as nx

_DEFAULT_EXCLUDE_TYPES = frozenset({"evidence"})


def _relationship_graph(nodes, edges, exclude_types) -> tuple[nx.Graph, dict]:
    by_id = {n["id"]: n for n in nodes if "id" in n}
    g = nx.Graph()
    g.add_nodes_from(by_id)
    for e in edges:
        if e.get("structural") or e.get("type") in exclude_types:
            continue
        s, t = e.get("source"), e.get("target")
        if s and t and s != t and s in by_id and t in by_id:
            g.add_edge(s, t)
    return g, by_id


def connector_subgraph(
    nodes: list[dict],
    edges: list[dict],
    selected: list[str],
    *,
    mode: str = "shortest_path",
    max_hops: int = 4,
    k: int = 3,
    exclude_types=_DEFAULT_EXCLUDE_TYPES,
) -> dict:
    """Return ``{nodes, edges, selected, connectors, brokers, paths, ...}``.

    ``nodes``/``edges`` reuse the input payload shapes (so the UI can render them
    with the existing graph view); each returned node gains ``role`` (selected /
    connector), ``betweenness`` and ``isBroker``. ``k`` bounds the paths-per-pair
    in ``hidden`` mode.
    """
    g, by_id = _relationship_graph(nodes, edges, exclude_types)
    sel = [s for s in dict.fromkeys(selected) if s in by_id]   # dedupe, keep present, order-stable
    missing = [s for s in selected if s not in by_id]

    keep: set[str] = set(sel)
    unreachable: list[list[str]] = []
    paths: list[dict] = []   # explicit path(s) per selected pair

    if mode in ("shortest_path", "hidden"):
        per_pair = max(1, k) if mode == "hidden" else 1
        for i in range(len(sel)):
            for j in range(i + 1, len(sel)):
                u, v = sel[i], sel[j]
                found = 0
                try:
                    # shortest_simple_paths yields paths in non-decreasing length
                    for path in nx.shortest_simple_paths(g, u, v):
                        if max_hops and (len(path) - 1) > max_hops:
                            break   # all later paths are at least this long
                        keep.update(path)
                        paths.append({"from": u, "to": v, "path": path, "hops": len(path) - 1})
                        found += 1
                        if found >= per_pair:
                            break
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    pass
                if found == 0:
                    unreachable.append([u, v])
    elif mode == "induced":
        present = set(sel)
        seen: set[frozenset] = set()
        for e in edges:
            if e.get("structural") or e.get("type") in exclude_types:
                continue
            s, t = e.get("source"), e.get("target")
            if s in present and t in present and s != t and frozenset((s, t)) not in seen:
                seen.add(frozenset((s, t)))
                paths.append({"from": s, "to": t, "path": [s, t], "hops": 1})
    else:
        raise ValueError(f"unknown connector mode: {mode!r}")

    sel_set = set(sel)
    connectors = [i for i in keep if i not in sel_set]

    # Brokerage: betweenness within the resulting subgraph flags which connector
    # nodes are the key hidden bridges (>= half as central as the top broker).
    sub = g.subgraph(keep)
    bc = nx.betweenness_centrality(sub) if sub.number_of_nodes() > 2 else {n: 0.0 for n in keep}
    top = max((bc.get(c, 0.0) for c in connectors), default=0.0)
    broker_set = {c for c in connectors if top > 0 and bc.get(c, 0.0) >= 0.5 * top}
    brokers = sorted(broker_set, key=lambda c: bc.get(c, 0.0), reverse=True)

    out_nodes = [
        {
            **by_id[i],
            "role": "selected" if i in sel_set else "connector",
            "betweenness": round(bc.get(i, 0.0), 4),
            "isBroker": i in broker_set,
        }
        for i in keep
    ]
    out_edges = [
        e for e in edges
        if not (e.get("structural") or e.get("type") in exclude_types)
        and e.get("source") in keep and e.get("target") in keep
        and e.get("source") != e.get("target")
    ]
    return {
        "nodes": out_nodes,
        "edges": out_edges,
        "selected": sel,
        "connectors": connectors,
        "brokers": brokers,
        "missing": missing,
        "paths": paths,
        "unreachablePairs": unreachable,
        "stats": {
            "selectedCount": len(sel),
            "connectorCount": len(connectors),
            "brokerCount": len(brokers),
            "edgeCount": len(out_edges),
            "pathCount": len(paths),
            "unreachablePairs": len(unreachable),
        },
    }
