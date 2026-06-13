"""Unit tests for the index-ified build_graph (DATA_MODEL migration step 5).

Imports tangraph.graph.operations (networkx/pandas/scipy), so run with the
tangos env:

    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_build_graph.py

Locks in the raw->canonical resolution (replacing the old per-affiliation
record scan + relabel_nodes) and confirms the dead junction side-effect is gone.
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import networkx as nx  # noqa: E402

from tangraph.graph.operations import build_graph  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_stages.json.gz"


def _record(identifier, rep=None, labels=None):
    r = {"identifier": identifier, "representative_identifier": rep or identifier}
    if labels is not None:
        r["labels"] = labels
    return r


def _state(*affiliations):
    # one chunk carrying the given affiliations
    return {"chunks": [{"uuid": "c1", "affiliations": list(affiliations)}]}


def _aff(a, b, t="affiliation"):
    return {"entityA": a, "entityB": b, "affiliation_type": t}


def _edges(graph):
    return {(u, v) for u, v in graph.edges()}


def test_resolves_endpoints_by_identifier():
    records = [_record("ACME", rep="ACME CORP"), _record("BOB")]
    edges, _mc, _hi, _lo, g = build_graph(records, _state(_aff("acme", "bob")))
    # "acme" -> ACME -> rep "ACME CORP"; "bob" -> BOB; symmetric -> sorted order
    assert _edges(g) == {("ACME CORP", "BOB")}
    assert edges == [{"nodes": ("ACME CORP", "BOB"), "chunk_id": "c1"}]


def test_resolves_endpoints_by_label():
    records = [_record("ACME CORP", rep="ACME CORP", labels=["ACME", "ACMECO"]), _record("BOB")]
    _e, _mc, _hi, _lo, g = build_graph(records, _state(_aff("acmeco", "bob")))
    assert _edges(g) == {("ACME CORP", "BOB")}   # "acmeco" resolved via label


def test_last_record_wins_on_name_collision():
    # both map raw "X": record1 by identifier, record2 by label -> last (R2) wins
    records = [_record("X", rep="R1"), _record("Y", rep="R2", labels=["X"]), _record("Z")]
    _e, _mc, _hi, _lo, g = build_graph(records, _state(_aff("x", "z")))
    assert g.has_node("R2") and not g.has_node("R1")


def test_self_loop_skipped():
    records = [_record("A", rep="SAME"), _record("B", rep="SAME")]
    edges, _mc, _hi, _lo, g = build_graph(records, _state(_aff("a", "b")))
    assert _edges(g) == set() and edges == []


def test_duplicate_edge_collapsed():
    records = [_record("A"), _record("B")]
    _e, _mc, _hi, _lo, g = build_graph(records, _state(_aff("a", "b"), _aff("a", "b")))
    assert len(g.edges()) == 1


# --- F7: phantom-endpoint drop + representatives enrichment ---------------


def test_phantom_endpoints_dropped():
    # neither name resolves to an extracted entity -> edge dropped (not a raw node)
    edges, _mc, _hi, _lo, g = build_graph([], _state(_aff("foo", "bar")))
    assert _edges(g) == set() and edges == []


def test_one_phantom_endpoint_drops_edge():
    records = [_record("ACME")]
    _e, _mc, _hi, _lo, g = build_graph(records, _state(_aff("acme", "ghost")))
    assert _edges(g) == set()   # "ghost" unresolved -> edge dropped


def test_representatives_enrich_resolution():
    # "AMP" is only known via the representative group, not the entity labels
    records = [_record("ACME CORP", rep="ACME CORP"), _record("BOB")]
    reps = [{"identifier": "ACME CORP", "relevant_identifiers": ["AMP"]}]
    aff = _state(_aff("amp", "bob"))
    # without representatives -> "amp" is a phantom -> dropped
    _e, _mc, _hi, _lo, g0 = build_graph(records, aff)
    assert _edges(g0) == set()
    # with representatives -> "amp" resolves to ACME CORP -> edge kept
    _e, _mc, _hi, _lo, g1 = build_graph(records, aff, reps)
    assert _edges(g1) == {("ACME CORP", "BOB")}


# --- F8: distinct relation types merged into a label list -----------------


def test_multi_relation_types_merged_into_label_list():
    records = [_record("A"), _record("B")]
    state = _state(_aff("a", "b", "affiliation"), _aff("a", "b", "partnership"))
    _e, _mc, _hi, _lo, g = build_graph(records, state)
    assert len(g.edges()) == 1
    assert g.get_edge_data("A", "B")["label"] == ["affiliation", "partnership"]


# --- F9: symmetric collapse vs directional keep ---------------------------


def test_symmetric_relation_collapses_bidirectional():
    records = [_record("A"), _record("B")]
    state = _state(_aff("a", "b", "affiliation"), _aff("b", "a", "affiliation"))
    _e, _mc, _hi, _lo, g = build_graph(records, state)
    assert len(g.edges()) == 1                  # A<->B is one undirected edge


def test_directional_relation_keeps_direction():
    records = [_record("A"), _record("B")]
    # "financial" is directional: B->A asserted stays B->A
    _e, _mc, _hi, _lo, g = build_graph(records, _state(_aff("b", "a", "financial")))
    assert _edges(g) == {("B", "A")}
    # both directions asserted -> two distinct directional edges
    _e, _mc, _hi, _lo, g2 = build_graph(
        records, _state(_aff("a", "b", "financial"), _aff("b", "a", "financial"))
    )
    assert len(g2.edges()) == 2


def test_degree_outputs():
    records = [_record(n) for n in ("A", "B", "C", "HUB")]
    g_edges = (_aff("hub", "a"), _aff("hub", "b"), _aff("hub", "c"))
    _e, most_connected, highest, lowest, g = build_graph(records, _state(*g_edges))
    assert most_connected == "HUB"
    assert "HUB" in highest          # degree 3 > 1
    assert set(lowest) == {"A", "B", "C"}   # degree 1 each


# --- G8: connectivity-to-root (relevance invariant) on the golden ----------


def test_golden_graph_fully_connected_to_root():
    if not GOLDEN.exists():
        return  # golden fixture not shipped in the public repo
    g = json.load(gzip.open(GOLDEN))
    _e, root, _hi, _lo, gr = build_graph(g["dedup_nodes"], {"chunks": g["chunks"]}, g["representatives"])
    u = gr.to_undirected()
    assert root is not None and u.number_of_nodes() > 0
    # every node reachable from root -> a single component (G8: no spurious
    # disconnection; the representatives-enrichment F7 fix is what guarantees it)
    assert nx.number_connected_components(u) == 1, "graph has a component disconnected from root"
    reach = nx.single_source_shortest_path_length(u, root)
    assert len(reach) == u.number_of_nodes(), "some node is unreachable from root"


def test_no_junction_side_effect_written():
    # the dead junction block is gone: build_graph must not write junction_* keys
    state = _state(_aff("a", "b"))
    build_graph([_record("A"), _record("B")], state)
    assert "junction_names" not in state
    assert "junction_nodes" not in state


def test_empty_graph_no_crash():
    edges, most_connected, highest, lowest, g = build_graph([], {"chunks": []})
    assert edges == [] and most_connected is None and highest == [] and lowest == []
    assert len(g.nodes()) == 0


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
