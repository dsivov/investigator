"""Corroboration-weighted edge filter for the affiliation graph.

Maps Aste's Information Filtering Networks idea (rank edges by weight, add
greedily under a topology constraint) onto OSINTGraph's setting:

  * "Weight" = ``source_count``: the number of distinct chunks that attest the
    pair (built by ``build_graph``). Symmetric pairs accumulate across either
    direction; directional pairs do not.
  * "Topology constraint" = every node that was reachable from ``root`` in the
    unfiltered affiliation graph must still be reachable in the filtered one.
    (We do not impose planarity or chordality -- root-connectivity is what the
    triangulation relevance metric needs.)

Algorithm (threshold + bridge preservation):
    1. Start with an empty graph carrying the same nodes.
    2. Add every edge whose ``source_count >= min_count``.
    3. For each node that was reachable from root in the original graph but
       is now disconnected, walk its shortest path to root in the original
       and add the directed edges along that path. This restores the minimum
       set of below-threshold edges needed to keep root-connectivity.

Filtering happens on the *clean* affiliation graph (``tangraph``) before
relevance scoring; it does not change which entities survive
(``score_graph_by_connectivity``'s evidence-gated rule), only which edges back
the surviving graph.
"""

from __future__ import annotations

import networkx as nx


def filter_by_corroboration(graph: nx.Graph, root, *, min_count: int = 2) -> nx.Graph:
    """Drop affiliation edges attested by fewer than ``min_count`` chunks,
    while preserving every node's reachability to ``root``.

    Parameters
    ----------
    graph
        The affiliation graph from ``build_graph``. Each edge must carry a
        ``source_count`` attribute (missing values are treated as 1).
    root
        The triangulation root (typically the investigation subject's canonical
        id). Edges along the shortest path from any orphan to root are kept
        as bridges, even if their ``source_count < min_count``.
    min_count
        Minimum corroboration weight to keep an edge unconditionally. Default 2
        (drop one-chunk-only edges).

    Returns
    -------
    A new graph (same type as the input) with the filtered edge set and the
    full node set.
    """
    out = graph.__class__()
    out.add_nodes_from(graph.nodes(data=True))

    if graph.number_of_edges() == 0:
        return out

    # Step 1+2: add strong edges.
    for u, v, data in graph.edges(data=True):
        if data.get("source_count", 1) >= min_count:
            out.add_edge(u, v, **data)

    # If no root, there is no connectivity constraint to enforce.
    if root is None or root not in graph:
        return out

    # Step 3: bridge orphans -- nodes that had a path to root originally
    # but lost it because their connecting edges were below the threshold.
    g_undir = graph.to_undirected(as_view=True)
    paths = nx.single_source_shortest_path(g_undir, root)   # one BFS

    out_undir = out.to_undirected(as_view=True)
    reachable_now = set(nx.single_source_shortest_path_length(out_undir, root)) if root in out_undir else {root}

    for node in graph.nodes():
        if node == root or node in reachable_now:
            continue
        path = paths.get(node)
        if not path:
            continue   # was already disconnected from root in the original
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            if graph.has_edge(u, v) and not out.has_edge(u, v):
                out.add_edge(u, v, **graph[u][v])
            if graph.has_edge(v, u) and not out.has_edge(v, u):
                out.add_edge(v, u, **graph[v][u])
        # update reachable_now incrementally so siblings sharing the same
        # bridge segment don't re-add the same edges
        reachable_now.update(path)

    return out
