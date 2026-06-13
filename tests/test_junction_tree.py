"""Junction-tree belief-propagation tests (Phase 2).

Verify ``propagate``:
  * zero coupling (beta=0) -> posterior == prior (the structure adds no info);
  * single-tetrahedron case (no clique tree, just one belief table);
  * two-tetrahedron chain (verifiable analytically);
  * sum-product result is a valid probability (in [0, 1]) for every entity;
  * marginals are consistent across cliques that share a vertex;
  * golden integration: run BP over the live Globalaid TMFG, check it
    doesn't reorder survivors catastrophically (Phase-2 isn't expected to flip
    rankings -- just adjust posteriors structurally).

    PYTHONPATH=.:src:/home/dsivov/Work/tangos_mvp <tangos-py> tests/test_junction_tree.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tangraph.graph.junction_tree import propagate  # noqa: E402
from tangraph.graph.operations import build_graph  # noqa: E402
from tangraph.graph.tmfg import construct_tmfg  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_stages.json.gz"


def _weighted(edges):
    g = nx.Graph()
    for u, v, w in edges:
        g.add_edge(u, v, weight=w)
    return g


# --- core invariants ------------------------------------------------------


def test_zero_coupling_returns_prior():
    """beta=0 -> Ising factors all equal 1 -> posterior == prior exactly."""
    g = _weighted([
        ("A", "B", 0.9), ("A", "C", 0.9), ("A", "D", 0.9),
        ("B", "C", 0.9), ("B", "D", 0.9), ("C", "D", 0.9),
        ("D", "E", 0.5),
    ])
    tmfg = construct_tmfg(g)
    priors = {"A": 0.8, "B": 0.6, "C": 0.4, "D": 0.7, "E": 0.2}
    r = propagate(tmfg, priors, beta=0.0)
    for v in priors:
        assert abs(r.posterior[v] - priors[v]) < 1e-9, (
            f"beta=0 must preserve prior for {v}: got {r.posterior[v]}, expected {priors[v]}"
        )
        assert abs(r.delta[v]) < 1e-9


def test_single_tetrahedron_no_messages():
    """K_4 input -> exactly one tetrahedron, no clique tree edges."""
    g = _weighted([
        ("A", "B", 1.0), ("A", "C", 1.0), ("A", "D", 1.0),
        ("B", "C", 1.0), ("B", "D", 1.0), ("C", "D", 1.0),
    ])
    tmfg = construct_tmfg(g)
    priors = {"A": 0.8, "B": 0.6, "C": 0.4, "D": 0.7}
    r = propagate(tmfg, priors, beta=1.0)
    for v in priors:
        assert 0.0 <= r.posterior[v] <= 1.0


def test_signed_BP_pulls_both_directions():
    """The whole point of the *signed* extension: BP must propagate both
    implicated (high prior) and cleared (low prior) information.

    Setup: two tetrahedra glued by a bridge separator.
       Tetra 1 = {A_imp(0.9), B_imp(0.9), C(0.5), D(0.5)}
       Tetra 2 = {C(0.5), D(0.5), E(0.5), G_clr(0.1)}    -- but BP builds
                                                            the tetra on its own,
                                                            so we provide a graph
                                                            with the right edges.

    After BP at beta=1.0:
      * Neutral entities adjacent to IMPLICATED clique-mates -> pulled UP
      * Neutral entities adjacent to CLEARED clique-mates    -> pulled DOWN
    """
    g = _weighted([
        # Implicated K4-ish core (A_imp, B_imp, C, D)
        ("A_imp", "B_imp", 1.0), ("A_imp", "C", 1.0), ("A_imp", "D", 1.0),
        ("B_imp", "C", 1.0), ("B_imp", "D", 1.0), ("C", "D", 1.0),
        # Bridge
        ("D", "E", 1.0),
        # Cleared K4-ish core (E, F, G_clr, H_clr)
        ("E", "F", 1.0), ("E", "G_clr", 1.0), ("E", "H_clr", 1.0),
        ("F", "G_clr", 1.0), ("F", "H_clr", 1.0), ("G_clr", "H_clr", 1.0),
    ])
    tmfg = construct_tmfg(g)
    priors = {
        "A_imp": 0.9, "B_imp": 0.9,       # strongly implicated
        "C": 0.5, "D": 0.5,                # neutral, in implicated clique
        "E": 0.5, "F": 0.5,                # neutral, in cleared clique
        "G_clr": 0.1, "H_clr": 0.1,        # strongly cleared
    }
    r = propagate(tmfg, priors, beta=1.0)

    # Neutrals next to implicated entities get pulled UP
    assert r.delta["C"] > 0.05, f"C should be pulled up by implicated neighbours, got delta={r.delta['C']:+.3f}"
    assert r.delta["D"] > 0.05, f"D should be pulled up, got delta={r.delta['D']:+.3f}"
    # Neutrals next to cleared entities get pulled DOWN
    assert r.delta["F"] < -0.05, f"F should be pulled DOWN by cleared neighbours, got delta={r.delta['F']:+.3f}"
    # The actually-implicated entities stay near 1.0
    assert r.posterior["A_imp"] >= 0.85
    # The actually-cleared entities stay near 0.0
    assert r.posterior["G_clr"] <= 0.20


def test_two_clique_chain_propagates_evidence():
    """A--B--C--D form K_4 #1, A added to {B,C,D} as K_4 #1 isn't quite right --
    build a 5-vertex chain where one strong prior on one end should pull the
    other end's posterior up (positive Ising coupling)."""
    g = _weighted([
        ("A", "B", 1.0), ("A", "C", 1.0), ("A", "D", 1.0),
        ("B", "C", 1.0), ("B", "D", 1.0), ("C", "D", 1.0),
        ("D", "E", 1.0),
    ])
    tmfg = construct_tmfg(g)
    # A very-implicated; B,C neutral; D neutral; E neutral
    priors = {"A": 0.95, "B": 0.5, "C": 0.5, "D": 0.5, "E": 0.5}
    r_strong = propagate(tmfg, priors, beta=1.0)
    r_weak = propagate(tmfg, priors, beta=0.1)
    # With strong coupling, the 0.5-prior entities are pulled UP toward A's value.
    assert r_strong.posterior["B"] > 0.5
    assert r_strong.posterior["E"] > 0.5
    # Weaker coupling pulls less.
    assert r_strong.posterior["E"] >= r_weak.posterior["E"] - 1e-9


def test_posteriors_in_unit_interval():
    g = _weighted([
        ("A", "B", 0.7), ("A", "C", 0.7), ("B", "C", 0.7),
        ("A", "D", 0.3), ("B", "D", 0.3), ("C", "D", 0.3),
        ("D", "E", 0.5),
    ])
    tmfg = construct_tmfg(g)
    priors = {"A": 0.3, "B": 0.8, "C": 0.4, "D": 0.9, "E": 0.1}
    r = propagate(tmfg, priors, beta=1.0)
    for v in priors:
        assert 0.0 <= r.posterior[v] <= 1.0


def test_marginal_consistency_across_shared_cliques():
    """A node that appears in multiple cliques must have the same posterior
    irrespective of which clique we marginalise from -- the junction-tree
    algorithm gives EXACT marginals on a tree."""
    g = _weighted([
        ("A", "B", 1.0), ("A", "C", 1.0), ("A", "D", 1.0),
        ("B", "C", 1.0), ("B", "D", 1.0), ("C", "D", 1.0),
        ("D", "E", 1.0), ("E", "F", 1.0),
    ])
    tmfg = construct_tmfg(g)
    priors = {"A": 0.6, "B": 0.5, "C": 0.7, "D": 0.4, "E": 0.5, "F": 0.3}
    r = propagate(tmfg, priors, beta=1.0)
    # Verify each clique's marginal for a shared vertex matches the reported posterior.
    for ci, members in enumerate(r.tetrahedra):
        belief = r.clique_beliefs[ci]
        for axis, v in enumerate(members):
            sum_axes = tuple(i for i in range(4) if i != axis)
            m = belief.sum(axis=sum_axes)
            local = float(m[1] / m.sum())
            assert abs(local - r.posterior[v]) < 1e-6, (
                f"clique {ci} marginal for {v} ({local}) disagrees with reported posterior ({r.posterior[v]})"
            )


def test_delta_field_matches_difference():
    g = _weighted([
        ("A", "B", 1.0), ("A", "C", 1.0), ("A", "D", 1.0),
        ("B", "C", 1.0), ("B", "D", 1.0), ("C", "D", 1.0),
    ])
    tmfg = construct_tmfg(g)
    priors = {"A": 0.8, "B": 0.2, "C": 0.5, "D": 0.5}
    r = propagate(tmfg, priors, beta=1.0)
    for v in priors:
        assert abs(r.delta[v] - (r.posterior[v] - priors[v])) < 1e-9


# --- golden integration --------------------------------------------------


def test_golden_propagate_runs_and_produces_valid_marginals():
    if not GOLDEN.exists():
        return  # golden fixture not shipped in the public repo
    g = json.load(gzip.open(GOLDEN))
    _e, _mc, _td, _ld, graph = build_graph(g["dedup_nodes"], {"chunks": g["chunks"]}, g["representatives"])
    tmfg = construct_tmfg(graph)
    # synthesize priors from `data.relevance_score` since the captured nodes
    # don't carry `prob` (consolidator output isn't in the affiliation-graph
    # nodes). Phase 2 in the orchestrator pulls real `prob` after consolidator.
    priors = {n["identifier"]: float(n.get("data", {}).get("relevance_score") or 0.5)
              for n in g["dedup_nodes"]}
    r = propagate(tmfg, priors, beta=1.0)
    for v in priors:
        assert 0.0 <= r.posterior.get(v, 0.5) <= 1.0
    # at least one entity should have moved more than 0.01 (otherwise BP is a no-op)
    moved = sum(1 for v in priors if abs(r.delta.get(v, 0.0)) > 0.01)
    assert moved > 0, "BP must reorder at least some entities on the golden TMFG"
    print(f"  [golden] {len(priors)} entities; {moved} moved >0.01")
    # Show the 5 biggest posterior-raise and -lower deltas (BP signal).
    ranked = sorted(r.delta.items(), key=lambda kv: -kv[1])
    print("  top-5 RAISED:   ", [(k, f"{v:+.3f}") for k, v in ranked[:5]])
    print("  top-5 LOWERED:  ", [(k, f"{v:+.3f}") for k, v in ranked[-5:]])


def _run_standalone() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} junction-tree tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
