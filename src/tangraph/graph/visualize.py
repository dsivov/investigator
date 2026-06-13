"""Interactive graph visualization via pyvis (debugging aid)."""

from __future__ import annotations

import os

import networkx as nx
from pyvis.network import Network


def _posterior_color(p: float) -> str:
    """Divergent red <-> grey <-> green palette for posterior probabilities.

    p >= 0.7 = strong implication (red), 0.5..0.7 = mild (pinkish), 0.3..0.5 =
    neutral (grey-ish), <= 0.3 = cleared (green).
    """
    p = max(0.0, min(1.0, float(p)))
    if p >= 0.5:
        # Interpolate grey (#aaaaaa, p=0.5) -> red (#c0392b, p=1.0)
        t = (p - 0.5) / 0.5
        r = int(0xaa + (0xc0 - 0xaa) * t)
        g = int(0xaa + (0x39 - 0xaa) * t)
        b = int(0xaa + (0x2b - 0xaa) * t)
    else:
        # grey (#aaaaaa, p=0.5) -> green (#27ae60, p=0.0)
        t = (0.5 - p) / 0.5
        r = int(0xaa + (0x27 - 0xaa) * t)
        g = int(0xaa + (0xae - 0xaa) * t)
        b = int(0xaa + (0x60 - 0xaa) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


def _delta_color(delta: float, scale: float = 0.3) -> str:
    """Diverging colormap for BP delta: blue (raised) <-> grey <-> red (lowered).

    `scale` is the absolute-delta value at which we saturate the colour.
    """
    d = max(-scale, min(scale, float(delta))) / scale
    if d >= 0:
        # grey -> blue
        r = int(0xaa + (0x2c - 0xaa) * d)
        g = int(0xaa + (0x76 - 0xaa) * d)
        b = int(0xaa + (0xe0 - 0xaa) * d)
    else:
        # grey -> red
        r = int(0xaa + (0xc0 - 0xaa) * (-d))
        g = int(0xaa + (0x39 - 0xaa) * (-d))
        b = int(0xaa + (0x2b - 0xaa) * (-d))
    return f"#{r:02x}{g:02x}{b:02x}"


def visualize_graph(graph: nx.Graph, output_path: str, *, title: str = "Investigation graph") -> str:
    """Render ``graph`` as a standalone interactive HTML file at ``output_path``.

    Node/edge attributes set by the caller are passed through to pyvis
    (``title`` for hover text, ``color``, ``value`` for sizing). Returns the
    path written. Used for debugging the triangulated graph; the pipeline calls
    it only when debug visualization is enabled.
    """
    net = Network(height="900px", width="100%", notebook=False, directed=True, heading=title)
    net.from_nx(graph)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    net.save_graph(output_path)
    # pyvis (current version) writes the `heading` as an <h1> in two places:
    # once inside <body> and once inside a container div. The duplicate is
    # visually confusing; strip the second occurrence so the title appears once.
    if title:
        target = f"<h1>{title}</h1>"
        with open(output_path, encoding="utf-8") as f:
            html = f.read()
        first = html.find(target)
        if first != -1:
            second = html.find(target, first + len(target))
            if second != -1:
                html = html[:second] + html[second + len(target):]
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write(html)
    return output_path


def visualize_clique_tree(tmfg_result, output_path: str, *,
                          posterior: dict | None = None,
                          title: str = "TMFG clique tree") -> str:
    """Render the junction tree of a TMFG: tetrahedra as nodes, triangular
    separators as edges.

    If ``posterior`` (entity_id -> P(implicated)) is provided, each tetra
    node is coloured by the MEAN posterior of its 4 members and sized by the
    total internal edge weight. Hover shows the 4 member names + individual
    posteriors.
    """
    g = nx.Graph()
    for ci, members in enumerate(tmfg_result.tetrahedra):
        members_list = sorted(members)
        # internal weight = sum of 6 pairwise edge weights inside the clique
        from itertools import combinations
        wsum = 0.0
        for u, v in combinations(members_list, 2):
            if tmfg_result.graph.has_edge(u, v):
                wsum += float(tmfg_result.graph[u][v].get("weight", 0.0))
        member_lines = []
        for m in members_list:
            line = m
            if posterior is not None and m in posterior:
                line = f"{m} (p={posterior[m]:.2f})"
            member_lines.append(line)
        node_title = (
            f"<b>tetrahedron #{ci}</b><br>internal weight: {wsum:.2f}<br><br>" + "<br>".join(member_lines)
        )
        if posterior is not None:
            mean_p = sum(posterior.get(m, 0.5) for m in members_list) / 4
            color = _posterior_color(mean_p)
            label = f"#{ci}\n{wsum:.1f}\np̄={mean_p:.2f}"
        else:
            color = "#3498db"
            label = f"#{ci}\n{wsum:.1f}"
        g.add_node(ci, title=node_title, label=label, value=max(wsum, 0.3), color=color)
    for (a, b, sep) in tmfg_result.separators:
        sep_label = " · ".join(sorted(sep))
        g.add_edge(a, b, title=f"separator: {sep_label}", label="")

    return visualize_graph(g, output_path, title=title)
