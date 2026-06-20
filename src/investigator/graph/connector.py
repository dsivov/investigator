"""Connector subgraph: how a chosen set of entities/events interconnect.

Given the UI graph payload (nodes + edges) and a set of selected node ids,
build a focused subgraph that reveals the relationships AND the intermediary
("connector") entities linking the selection -- for relationship analysis.

Default mode ``shortest_path``: for every pair of selected nodes take a shortest
path and union them. The induced subgraph over those nodes is returned, so all
direct relationships among the selected+connector set show, not just the path
edges. ``induced`` mode keeps only the selected nodes and the edges directly
between them.

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
    exclude_types=_DEFAULT_EXCLUDE_TYPES,
) -> dict:
    """Return ``{nodes, edges, selected, connectors, unreachablePairs, stats}``.

    ``nodes``/``edges`` reuse the input payload shapes (so the UI can render them
    with the existing graph view); each returned node gains ``role`` =
    ``"selected"`` or ``"connector"``.
    """
    g, by_id = _relationship_graph(nodes, edges, exclude_types)
    sel = [s for s in dict.fromkeys(selected) if s in by_id]   # dedupe, keep present, order-stable
    missing = [s for s in selected if s not in by_id]

    keep: set[str] = set(sel)
    unreachable: list[list[str]] = []
    paths: list[dict] = []   # explicit shortest path per selected pair

    if mode == "shortest_path":
        for i in range(len(sel)):
            for j in range(i + 1, len(sel)):
                u, v = sel[i], sel[j]
                try:
                    path = nx.shortest_path(g, u, v)
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    unreachable.append([u, v])
                    continue
                if max_hops and (len(path) - 1) > max_hops:
                    unreachable.append([u, v])
                    continue
                keep.update(path)
                paths.append({"from": u, "to": v, "path": path, "hops": len(path) - 1})
    elif mode == "induced":
        # direct links among the selection are the "paths" in this mode
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
    out_nodes = [
        {**by_id[i], "role": "selected" if i in sel_set else "connector"}
        for i in keep
    ]
    out_edges = [
        e for e in edges
        if not (e.get("structural") or e.get("type") in exclude_types)
        and e.get("source") in keep and e.get("target") in keep
        and e.get("source") != e.get("target")
    ]
    connectors = [i for i in keep if i not in sel_set]
    return {
        "nodes": out_nodes,
        "edges": out_edges,
        "selected": sel,
        "connectors": connectors,
        "missing": missing,
        "paths": paths,
        "unreachablePairs": unreachable,
        "stats": {
            "selectedCount": len(sel),
            "connectorCount": len(connectors),
            "edgeCount": len(out_edges),
            "unreachablePairs": len(unreachable),
        },
    }
