"""TMFG construction tests (Aste 2025, §5.3) — Phase 1 of the research branch.

Verify ``construct_tmfg``:
  * the result has exactly 3p - 6 edges for any p >= 4 (planar bound, achieved
    by TMFG by construction);
  * is chordal (every cycle of length >= 4 has a chord);
  * decomposes into (p - 3) tetrahedra connected by (p - 4) triangular
    separators;
  * the seed tetrahedron is the 4 highest-total-weight vertices;
  * non-edges of the input that appear in the TMFG are tagged as
    ``fill_in=True`` (hypothesis edges) and surfaced in
    ``result.fill_in_edges``.

Plus a golden integration: TMFG over the recaptured Globalaid affiliation
graph. We do not assert specific tetrahedra (those depend on LLM strength
values), only the structural invariants and that the tetrahedra are
interpretable (no degenerate seed, fill-in ratio is sane).

    PYTHONPATH=.:src <tangos-py> tests/test_tmfg.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.graph.operations import build_graph  # noqa: E402
from investigator.graph.tmfg import construct_tmfg, tetrahedron_weight  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_stages.json.gz"


def _weighted(edges: list[tuple]):
    g = nx.Graph()
    for u, v, w in edges:
        g.add_edge(u, v, weight=w)
    return g


# --- core invariants -----------------------------------------------------


def test_fewer_than_4_nodes_returns_input_no_tetrahedra():
    g = _weighted([("A", "B", 1.0), ("B", "C", 1.0)])
    r = construct_tmfg(g)
    assert r.tetrahedra == []
    assert r.separators == []
    assert r.clique_tree.number_of_nodes() == 0


def test_exact_k4_input_is_its_own_tmfg():
    # K_4 has 6 edges = 3*4 - 6 -- the result must equal the input (no extra work).
    g = _weighted([
        ("A", "B", 1.0), ("A", "C", 1.0), ("A", "D", 1.0),
        ("B", "C", 1.0), ("B", "D", 1.0), ("C", "D", 1.0),
    ])
    r = construct_tmfg(g)
    assert r.graph.number_of_edges() == 6
    assert len(r.tetrahedra) == 1
    assert r.tetrahedra[0] == {"A", "B", "C", "D"}
    assert len(r.separators) == 0
    assert r.fill_in_edges == set()


def test_edge_count_equals_3p_minus_6():
    # Build a non-trivial connected weighted graph; TMFG must hit the planar bound.
    g = _weighted([
        ("A", "B", 5.0), ("B", "C", 4.0), ("C", "D", 3.0),
        ("D", "E", 2.0), ("A", "C", 5.0), ("B", "D", 4.0),
        ("A", "E", 1.0), ("C", "E", 1.0),
    ])
    p = g.number_of_nodes()
    r = construct_tmfg(g)
    assert r.graph.number_of_edges() == 3 * p - 6, (
        f"expected {3*p-6} TMFG edges for p={p}, got {r.graph.number_of_edges()}"
    )


def test_tmfg_is_chordal():
    g = _weighted([
        ("A", "B", 5.0), ("B", "C", 4.0), ("C", "D", 3.0), ("D", "E", 2.0),
        ("A", "C", 5.0), ("B", "D", 4.0), ("A", "E", 1.0), ("C", "E", 1.0),
        ("E", "F", 3.0), ("D", "F", 2.0),
    ])
    r = construct_tmfg(g)
    assert nx.is_chordal(r.graph), "TMFG must be chordal by construction"


def test_decomposition_counts_p_minus_3_tetrahedra_p_minus_4_separators():
    g = _weighted([
        ("A", "B", 5.0), ("B", "C", 4.0), ("C", "D", 3.0), ("D", "E", 2.0),
        ("A", "C", 5.0), ("B", "D", 4.0), ("E", "F", 1.0), ("F", "A", 1.0),
    ])
    p = g.number_of_nodes()
    r = construct_tmfg(g)
    assert len(r.tetrahedra) == p - 3
    assert len(r.separators) == p - 4
    assert r.clique_tree.number_of_nodes() == len(r.tetrahedra)
    assert r.clique_tree.number_of_edges() == len(r.separators)


def test_seed_tetrahedron_is_top4_by_total_weight():
    # H, U, B, X each have edges to multiple others; M is a leaf.
    g = _weighted([
        ("H", "U", 10.0), ("H", "B", 10.0), ("H", "X", 10.0),
        ("U", "B", 9.0), ("U", "X", 9.0), ("B", "X", 9.0),
        ("M", "H", 1.0),
    ])
    r = construct_tmfg(g)
    # H, U, B, X have totals around 30/27/28/28; M has 1. Top-4 = {H, U, B, X}.
    assert r.tetrahedra[0] == {"H", "U", "B", "X"}


def test_fill_in_edges_recorded_for_non_input_edges():
    # 5 nodes; A, B, C, D form a clique; E is connected only to A.
    # TMFG will add fill-ins to connect E to the rest.
    g = _weighted([
        ("A", "B", 1.0), ("A", "C", 1.0), ("A", "D", 1.0),
        ("B", "C", 1.0), ("B", "D", 1.0), ("C", "D", 1.0),
        ("A", "E", 1.0),
    ])
    r = construct_tmfg(g)
    # 3*5 - 6 = 9 edges; input has 7; so 2 edges are fill-in.
    assert r.graph.number_of_edges() == 9
    assert len(r.fill_in_edges) == 2
    # E gets 3 edges; two of (E,B), (E,C), (E,D) must be marked fill_in.
    for f in r.fill_in_edges:
        assert "E" in f, f"unexpected fill-in edge {f}"


def test_construction_is_deterministic():
    g = _weighted([
        ("A", "B", 5.0), ("B", "C", 4.0), ("C", "D", 3.0), ("D", "E", 2.0),
        ("A", "C", 5.0), ("B", "D", 4.0), ("A", "E", 1.0), ("C", "E", 1.0),
    ])
    r1 = construct_tmfg(g)
    r2 = construct_tmfg(g)
    assert set(map(frozenset, r1.tetrahedra)) == set(map(frozenset, r2.tetrahedra))
    assert set(r1.graph.edges()) == set(r2.graph.edges())


# --- golden integration --------------------------------------------------


def test_golden_tmfg_invariants():
    """TMFG over the recaptured Globalaid affiliation graph: structural checks."""
    if not GOLDEN.exists():
        return  # golden fixture not shipped in the public repo
    g = json.load(gzip.open(GOLDEN))
    _e, _mc, _td, _ld, graph = build_graph(g["dedup_nodes"], {"chunks": g["chunks"]}, g["representatives"])
    p = graph.number_of_nodes()
    r = construct_tmfg(graph)
    assert r.graph.number_of_edges() == 3 * p - 6
    assert len(r.tetrahedra) == p - 3
    assert len(r.separators) == p - 4
    assert nx.is_chordal(r.graph)
    # Seed must include GLOBALAID (highest total weight in Globalaid-centric data).
    print(f"\n  [golden] p={p}, edges {graph.number_of_edges()} -> TMFG {r.graph.number_of_edges()}, "
          f"fill_in {len(r.fill_in_edges)} ({100*len(r.fill_in_edges)//max(r.graph.number_of_edges(),1)}%)")
    print(f"  seed tetrahedron: {r.tetrahedra[0]}")
    # rank the top-5 tetrahedra by total internal weight
    ranked = sorted(
        ((i, members, tetrahedron_weight(r.graph, members))
         for i, members in enumerate(r.tetrahedra)),
        key=lambda t: -t[2],
    )
    print("  top-5 tetrahedra by internal weight:")
    for idx, members, w in ranked[:5]:
        print(f"    [{idx}] w={w:.2f}  {sorted(members)}")


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
    print(f"\n{len(tests) - failures}/{len(tests)} TMFG tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
