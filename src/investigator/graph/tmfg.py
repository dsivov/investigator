"""Triangulated Maximally Filtered Graph (TMFG) construction.

Aste's TMFG algorithm (`Section 5.3 of arxiv:2505.03812`): build a chordal,
planar graph from a weighted input graph by greedy local clique extension.

Construction:
  1. Pick the four highest-total-weight vertices -> initial K_4 (tetrahedron).
  2. Track the 4 triangular faces of the K_4 as the active "frontier".
  3. Iteratively, for every remaining vertex v and every active face (a, b, c),
     compute the gain w(v, a) + w(v, b) + w(v, c). Pick the (v, face) pair
     with the largest gain. Insert v, add three new edges (v,a) (v,b) (v,c),
     and replace face (a, b, c) by three new faces (v, a, b) (v, a, c) (v, b, c).
  4. Done. Result has exactly 3p - 6 edges (planar bound, achieved exactly),
     decomposes into (p - 3) tetrahedra glued by (p - 4) triangular separators,
     and is chordal by construction (a clique tree exists).

For OSINTGraph the input is the affiliation graph from ``build_graph`` with the
``weight`` attribute (= mean_strength * mean_confidence with ``source_count``
fallback). Edges absent from the input contribute 0 to the gain -- TMFG may
*add* them as "fill-in" edges to satisfy chordality; we tag those so they can
be presented as structural-hypothesis edges rather than LLM-attested ones.

Not on by default. Wired in ``_standard_pipeline`` behind ``INVESTIGATOR_TMFG=1``
as a parallel layer to ``investigator`` -- it does not replace the existing
relevance / triangulation pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx


@dataclass
class TMFGResult:
    graph: nx.Graph                                   # the TMFG itself (3p - 6 edges, chordal+planar)
    tetrahedra: list[set]                             # (p - 3) maximal 4-cliques, in insertion order
    separators: list[tuple[int, int, frozenset]]      # (tetra_a_idx, tetra_b_idx, shared 3-vertex face)
    clique_tree: nx.Graph                             # nodes = tetrahedra indices, edges = separators
    fill_in_edges: set[frozenset] = field(default_factory=set)  # in TMFG but NOT in input -- hypothesis edges


def _edge_w(graph: nx.Graph, u, v, weight_attr: str) -> float:
    """Weight of edge (u, v) in ``graph``; 0 if no edge or no attribute."""
    if not graph.has_edge(u, v):
        return 0.0
    return float(graph[u][v].get(weight_attr, 0.0))


def construct_tmfg(graph: nx.Graph, *, weight_attr: str = "weight") -> TMFGResult:
    """Build the TMFG of ``graph``.

    ``graph`` is treated as undirected for the purposes of TMFG (the affiliation
    graph is directed but symmetric relations are direction-agnostic; for
    TMFG-construction we collapse to undirected). Weights come from
    ``edge_data[weight_attr]`` (default ``"weight"``); missing weights are 0.
    """
    g = graph.to_undirected(as_view=False) if graph.is_directed() else graph.copy()
    nodes = list(g.nodes())
    n = len(nodes)

    if n < 4:
        # Not enough vertices for a tetrahedron -- return the input as-is, no cliques.
        return TMFGResult(graph=g, tetrahedra=[], separators=[], clique_tree=nx.Graph())

    # Step 1: pick the four nodes with the highest total incident weight.
    total = {v: sum(_edge_w(g, v, u, weight_attr) for u in g.neighbors(v)) for v in nodes}
    seed = sorted(nodes, key=lambda v: (-total[v], str(v)))[:4]

    tmfg = nx.Graph()
    tmfg.add_nodes_from(g.nodes(data=True))

    fill_in: set[frozenset] = set()

    def _add_edge(u, v):
        present = g.has_edge(u, v)
        w = _edge_w(g, u, v, weight_attr) if present else 0.0
        tmfg.add_edge(u, v, weight=w, fill_in=not present)
        if not present:
            fill_in.add(frozenset((u, v)))

    # K_4 edges
    for i in range(4):
        for j in range(i + 1, 4):
            _add_edge(seed[i], seed[j])

    tetrahedra: list[set] = [set(seed)]
    # initial 4 triangular faces of the K_4
    faces: set[frozenset] = set()
    face_to_tetra: dict[frozenset, int] = {}
    for i in range(4):
        for j in range(i + 1, 4):
            for k in range(j + 1, 4):
                f = frozenset((seed[i], seed[j], seed[k]))
                faces.add(f)
                face_to_tetra[f] = 0

    separators: list[tuple[int, int, frozenset]] = []
    remaining = [v for v in nodes if v not in set(seed)]

    # Step 3: insert each remaining vertex into the best face.
    while remaining:
        best_score = -float("inf")
        best_v = None
        best_face: frozenset | None = None
        for v in remaining:
            for face in faces:
                a, b, c = tuple(face)
                score = _edge_w(g, v, a, weight_attr) + _edge_w(g, v, b, weight_attr) + _edge_w(g, v, c, weight_attr)
                # Tie-break deterministically on (v, face) ordering to make the
                # output reproducible across runs.
                if score > best_score or (
                    score == best_score and (str(v), tuple(sorted(map(str, face)))) <
                    (str(best_v), tuple(sorted(map(str, best_face or frozenset()))))
                ):
                    best_score = score
                    best_v = v
                    best_face = face

        a, b, c = tuple(best_face)
        for u in (a, b, c):
            if not tmfg.has_edge(best_v, u):
                _add_edge(best_v, u)

        new_idx = len(tetrahedra)
        tetrahedra.append({best_v, a, b, c})
        separators.append((face_to_tetra[best_face], new_idx, best_face))

        # replace the consumed face by three new ones (the surface of the new tetra)
        faces.discard(best_face)
        del face_to_tetra[best_face]
        for triangle in (
            frozenset((best_v, a, b)),
            frozenset((best_v, a, c)),
            frozenset((best_v, b, c)),
        ):
            faces.add(triangle)
            face_to_tetra[triangle] = new_idx

        remaining.remove(best_v)

    # Clique tree: a node per tetrahedron, an edge per separator (= shared triangle).
    clique_tree = nx.Graph()
    for i, members in enumerate(tetrahedra):
        clique_tree.add_node(i, members=members)
    for (a_idx, b_idx, sep) in separators:
        clique_tree.add_edge(a_idx, b_idx, separator=sep)

    return TMFGResult(
        graph=tmfg,
        tetrahedra=tetrahedra,
        separators=separators,
        clique_tree=clique_tree,
        fill_in_edges=fill_in,
    )


def tetrahedron_weight(tmfg_graph: nx.Graph, members: set, *, weight_attr: str = "weight") -> float:
    """Sum of edge weights inside a 4-clique. Useful for ranking themes."""
    members = list(members)
    return sum(
        _edge_w(tmfg_graph, members[i], members[j], weight_attr)
        for i in range(len(members)) for j in range(i + 1, len(members))
    )
