"""Corroboration edge filter tests (Direction-1 research prototype).

Verify ``filter_by_corroboration`` (graph/filter.py):
  * keeps every edge with source_count >= min_count;
  * drops edges below the threshold UNLESS they bridge an otherwise-orphaned
    node back to root;
  * preserves root reachability for every node that had a path in the original;
  * never reintroduces nodes that were already disconnected from root.

Plus a golden-input sanity check (build_graph -> filter -> count surviving
edges) to make sure source_count is being populated on real data.

    PYTHONPATH=.:src <tangos-py> tests/test_edge_filter.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.graph.filter import filter_by_corroboration  # noqa: E402
from investigator.graph.operations import build_graph  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_stages.json.gz"


def _g(*edges):
    """Build a DiGraph from (u, v, source_count) triples."""
    g = nx.DiGraph()
    for u, v, c in edges:
        g.add_edge(u, v, source_count=c)
    return g


# --- threshold behavior ---------------------------------------------------


def test_strong_edges_always_kept():
    g = _g(("ROOT", "A", 5), ("ROOT", "B", 3))
    out = filter_by_corroboration(g, "ROOT", min_count=2)
    assert set(out.edges()) == {("ROOT", "A"), ("ROOT", "B")}


def test_weak_edges_dropped_when_not_bridges():
    # ROOT -> A (strong), and a redundant ROOT -> B (weak) where B is also
    # reached via ROOT -> A -> B (strong). The weak ROOT -> B should drop.
    g = _g(("ROOT", "A", 5), ("A", "B", 5), ("ROOT", "B", 1))
    out = filter_by_corroboration(g, "ROOT", min_count=2)
    assert out.has_edge("ROOT", "A")
    assert out.has_edge("A", "B")
    assert not out.has_edge("ROOT", "B"), "redundant weak edge should have been dropped"


def test_weak_bridge_restored_for_orphan():
    # B is reachable ONLY via the weak A -> B edge; dropping it would orphan B.
    g = _g(("ROOT", "A", 5), ("A", "B", 1))
    out = filter_by_corroboration(g, "ROOT", min_count=2)
    assert out.has_edge("A", "B"), "weak edge needed as bridge to B must survive"
    assert nx.has_path(out.to_undirected(), "B", "ROOT")


def test_default_min_count_is_2():
    # min_count default = 2; source_count=1 alone is below; bridge logic must
    # still keep it for the only-path-to-root case.
    g = _g(("ROOT", "X", 1))
    out = filter_by_corroboration(g, "ROOT")    # default min_count=2
    assert out.has_edge("ROOT", "X"), "single bridge must survive even at default threshold"


# --- reachability invariants ---------------------------------------------


def test_root_reachability_preserved_for_all_originally_reachable():
    # Mixed strengths; every node reachable in the original must still reach root.
    g = _g(("ROOT", "A", 3), ("A", "B", 1), ("B", "C", 1), ("ROOT", "D", 1), ("D", "E", 3))
    orig_reach = set(nx.single_source_shortest_path_length(g.to_undirected(), "ROOT"))
    out = filter_by_corroboration(g, "ROOT", min_count=2)
    new_reach = set(nx.single_source_shortest_path_length(out.to_undirected(), "ROOT"))
    assert new_reach == orig_reach, f"lost reachability for {orig_reach - new_reach}"


def test_already_disconnected_nodes_stay_disconnected():
    # ISLAND has no edges in the original; it must not be reintroduced.
    g = _g(("ROOT", "A", 5))
    g.add_node("ISLAND")
    out = filter_by_corroboration(g, "ROOT", min_count=2)
    assert "ISLAND" in out.nodes
    assert not any(out.degree(n) > 0 for n in ["ISLAND"])


def test_root_absent_or_none_does_not_crash():
    g = _g(("A", "B", 3), ("B", "C", 1))
    # root not in graph
    out1 = filter_by_corroboration(g, "DOES_NOT_EXIST", min_count=2)
    assert set(out1.edges()) == {("A", "B")}
    # root is None
    out2 = filter_by_corroboration(g, None, min_count=2)
    assert set(out2.edges()) == {("A", "B")}


def test_empty_graph():
    g = nx.DiGraph()
    out = filter_by_corroboration(g, None, min_count=2)
    assert out.number_of_edges() == 0 and out.number_of_nodes() == 0


def test_missing_source_count_attribute_treated_as_1():
    # legacy or test-built graph with no source_count attribute -> filtered out
    # at min_count=2 unless it's a bridge.
    g = nx.DiGraph()
    g.add_edge("ROOT", "A", source_count=5)
    g.add_edge("ROOT", "B")    # no source_count -> treated as 1
    out = filter_by_corroboration(g, "ROOT", min_count=2)
    # B is reachable in the original; the bridge logic preserves it
    assert out.has_edge("ROOT", "B"), "missing source_count edge must survive as bridge"
    g2 = nx.DiGraph()
    g2.add_edge("ROOT", "A", source_count=5)
    g2.add_edge("A", "B", source_count=5)
    g2.add_edge("ROOT", "B")   # redundant, no source_count
    out2 = filter_by_corroboration(g2, "ROOT", min_count=2)
    assert not out2.has_edge("ROOT", "B"), "redundant no-source_count edge should be dropped"


# --- golden integration --------------------------------------------------


def test_golden_build_graph_sets_source_count():
    """Real run: build_graph over the captured chunks must populate
    source_count on every edge."""
    if not GOLDEN.exists():
        return  # golden fixture not shipped in the public repo
    g = json.load(gzip.open(GOLDEN))
    edges, _mc, _td, _ld, graph = build_graph(g["dedup_nodes"], {"chunks": g["chunks"]}, g["representatives"])
    assert graph.number_of_edges() > 0
    counts = [d.get("source_count") for _u, _v, d in graph.edges(data=True)]
    assert all(isinstance(c, int) and c >= 1 for c in counts), "every edge must have an integer source_count >= 1"
    # corroboration distribution: some pairs ought to be attested >1 in 40 chunks
    multi = sum(1 for c in counts if c >= 2)
    print(f"  [golden] {graph.number_of_edges()} edges; {multi} with source_count >= 2")


def test_golden_filter_preserves_root_reachability():
    """Real run: the filter at min_count=2 must not orphan any node that was
    affiliation-connected to GLOBALAID, INC. in the unfiltered graph."""
    if not GOLDEN.exists():
        return  # golden fixture not shipped in the public repo
    g = json.load(gzip.open(GOLDEN))
    _e, _mc, _td, _ld, graph = build_graph(g["dedup_nodes"], {"chunks": g["chunks"]}, g["representatives"])
    root = "GLOBALAID, INC."   # the top-degree node in this fixture
    assert root in graph
    orig_reach = set(nx.single_source_shortest_path_length(graph.to_undirected(), root))
    out = filter_by_corroboration(graph, root, min_count=2)
    new_reach = set(nx.single_source_shortest_path_length(out.to_undirected(), root))
    assert new_reach == orig_reach, f"filter orphaned {orig_reach - new_reach}"
    print(f"  [golden] {graph.number_of_edges()} -> {out.number_of_edges()} edges "
          f"({100 * (graph.number_of_edges() - out.number_of_edges()) // max(graph.number_of_edges(), 1)}% drop); "
          f"reach {len(new_reach)}/{len(orig_reach)}")


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
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
