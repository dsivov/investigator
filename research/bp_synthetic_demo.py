"""Synthetic demonstration of signed junction-tree belief propagation.

Builds a tiny investigation-shaped graph with KNOWN ground truth:
  * 2 strongly implicated entities (prior 0.9) + 2 neutral neighbours
  * 2 strongly cleared entities (prior 0.1) + 2 neutral neighbours
  * a bridge between the two clusters

Runs Phase-2 BP and renders three viz HTMLs so the bidirectional propagation
(neutrals pulled UP toward the implicated cluster, neutrals pulled DOWN toward
the cleared cluster) is directly visible -- the thing the live Globalaid data
can't show because it has zero contradicting evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tangraph.graph.junction_tree import propagate
from tangraph.graph.tmfg import construct_tmfg
from tangraph.graph.visualize import (
    _delta_color,
    _posterior_color,
    visualize_clique_tree,
    visualize_graph,
)

OUT_DIR = Path("debug_output/viz")


def main() -> int:
    # Construct the synthetic graph (same shape as test_signed_BP_pulls_both_directions).
    g = nx.Graph()
    edges = [
        # IMPLICATED 4-clique (A_imp, B_imp, C, D)
        ("A_imp", "B_imp", 1.0), ("A_imp", "C", 1.0), ("A_imp", "D", 1.0),
        ("B_imp", "C", 1.0), ("B_imp", "D", 1.0), ("C", "D", 1.0),
        # Bridge from the implicated cluster to the cleared cluster
        ("D", "E", 1.0),
        # CLEARED 4-clique (E, F, G_clr, H_clr)
        ("E", "F", 1.0), ("E", "G_clr", 1.0), ("E", "H_clr", 1.0),
        ("F", "G_clr", 1.0), ("F", "H_clr", 1.0), ("G_clr", "H_clr", 1.0),
    ]
    for u, v, w in edges:
        g.add_edge(u, v, weight=w)

    priors = {
        "A_imp": 0.9, "B_imp": 0.9,
        "C": 0.5, "D": 0.5, "E": 0.5, "F": 0.5,
        "G_clr": 0.1, "H_clr": 0.1,
    }

    tmfg = construct_tmfg(g)
    print(f"TMFG: {g.number_of_edges()} -> {tmfg.graph.number_of_edges()} edges  "
          f"({len(tmfg.tetrahedra)} tetrahedra)")

    r = propagate(tmfg, priors, beta=1.0)
    print(f"\nBP result (beta=1.0):")
    print(f"{'entity':10s} {'prior':>7s} {'posterior':>10s} {'delta':>8s}  direction")
    print("-" * 56)
    for v in priors:
        d = r.delta[v]
        arrow = "↑↑" if d > 0.1 else ("↑" if d > 0.01 else ("↓↓" if d < -0.1 else ("↓" if d < -0.01 else "·")))
        print(f"{v:10s} {priors[v]:>7.2f} {r.posterior[v]:>10.3f} {d:>+8.3f}  {arrow}")

    # --- Three viz HTMLs --------------------------------------------------
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    def _build(color_fn, mode):
        gv = nx.DiGraph()
        for v in priors:
            p = r.posterior[v]
            d = r.delta[v]
            color = color_fn(p if mode == "post" else d)
            gv.add_node(
                v,
                title=f"<b>{v}</b><br>prior: {priors[v]:.2f}<br><b>posterior: {p:.3f}</b><br>delta: {d:+.3f}",
                value=2.0, color=color,
            )
        for u, w, _ in edges:
            gv.add_edge(u, w, color="#95a5a6")
        return gv

    p1 = OUT_DIR / "bp_synthetic_posterior.html"
    p2 = OUT_DIR / "bp_synthetic_delta.html"
    p3 = OUT_DIR / "bp_synthetic_clique_tree.html"

    visualize_graph(
        _build(_posterior_color, "post"), str(p1),
        title="Synthetic BP — posterior   ·   red=implicated · grey=neutral · green=cleared   ·   FULL [0,1] colormap visible",
    )
    visualize_graph(
        _build(_delta_color, "delta"), str(p2),
        title="Synthetic BP — delta (posterior - prior)   ·   blue=BP raised · grey=unchanged · red=BP lowered",
    )
    visualize_clique_tree(
        tmfg, str(p3),
        posterior={v: r.posterior[v] for v in priors},
        title="Synthetic BP — clique tree  ·  tetrahedra coloured by mean posterior",
    )
    print(f"\nwrote: {p1}\n       {p2}\n       {p3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
