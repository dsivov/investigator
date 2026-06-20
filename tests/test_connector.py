"""Connector-subgraph tests.

Validates that selecting a set of entities yields the relationships + the
intermediary connector nodes that link them, on relationship edges only (the
structural evidence->root hub must NOT create phantom short paths).

Standalone:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_connector.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.graph.connector import connector_subgraph  # noqa: E402


def _nodes(*ids):
    return [{"id": i, "type": "entity"} for i in ids]


def _edge(s, t, type_="affiliation", structural=False):
    return {"source": s, "target": t, "type": type_, "structural": structural}


# Graph:  A - X - D   (X connector),  A - B (direct),  C isolated from the rest,
#         ROOT hub connected to everything via structural evidence edges.
GRAPH_NODES = _nodes("A", "B", "C", "D", "X", "ROOT")
GRAPH_EDGES = [
    _edge("A", "B"),
    _edge("A", "X"),
    _edge("X", "D"),
    # structural hub edges -- must be ignored by pathfinding
    _edge("ROOT", "A", "evidence", structural=True),
    _edge("ROOT", "B", "evidence", structural=True),
    _edge("ROOT", "C", "evidence", structural=True),
    _edge("ROOT", "D", "evidence", structural=True),
]


def test_shortest_path_pulls_in_connector():
    r = connector_subgraph(GRAPH_NODES, GRAPH_EDGES, ["A", "D"])
    ids = {n["id"] for n in r["nodes"]}
    assert ids == {"A", "X", "D"}, ids          # X is the intermediary
    roles = {n["id"]: n["role"] for n in r["nodes"]}
    assert roles["A"] == "selected" and roles["D"] == "selected"
    assert roles["X"] == "connector"
    assert r["connectors"] == ["X"]
    assert r["stats"]["unreachablePairs"] == 0
    # the explicit path A->X->D is returned
    assert r["paths"] == [{"from": "A", "to": "D", "path": ["A", "X", "D"], "hops": 2}]


def test_induced_paths_are_direct_edges():
    r = connector_subgraph(GRAPH_NODES, GRAPH_EDGES, ["A", "B"], mode="induced")
    assert r["paths"] == [{"from": "A", "to": "B", "path": ["A", "B"], "hops": 1}]


def test_structural_hub_does_not_create_short_paths():
    # A and C are only joined via the ROOT structural hub -> unreachable, NOT a
    # 2-hop A-ROOT-C path.
    r = connector_subgraph(GRAPH_NODES, GRAPH_EDGES, ["A", "C"])
    assert ["A", "C"] in r["unreachablePairs"] or ["C", "A"] in r["unreachablePairs"]
    assert "ROOT" not in {n["id"] for n in r["nodes"]}


def test_direct_edge_no_connector():
    r = connector_subgraph(GRAPH_NODES, GRAPH_EDGES, ["A", "B"])
    assert {n["id"] for n in r["nodes"]} == {"A", "B"}
    assert r["connectors"] == []
    assert any({e["source"], e["target"]} == {"A", "B"} for e in r["edges"])


def test_induced_mode_direct_only():
    r = connector_subgraph(GRAPH_NODES, GRAPH_EDGES, ["A", "D"], mode="induced")
    # induced over {A, D}: no direct edge -> both kept, no connector, no edges
    assert {n["id"] for n in r["nodes"]} == {"A", "D"}
    assert r["edges"] == []


def test_max_hops_cap():
    # A..D is 2 hops; cap at 1 makes it unreachable.
    r = connector_subgraph(GRAPH_NODES, GRAPH_EDGES, ["A", "D"], max_hops=1)
    assert ["A", "D"] in r["unreachablePairs"]
    assert "X" not in {n["id"] for n in r["nodes"]}


def test_missing_and_dedup_selection():
    r = connector_subgraph(GRAPH_NODES, GRAPH_EDGES, ["A", "A", "ZZZ"])
    assert r["selected"] == ["A"]
    assert r["missing"] == ["ZZZ"]


def test_returned_edges_exclude_structural():
    r = connector_subgraph(GRAPH_NODES, GRAPH_EDGES, ["A", "B", "D"])
    assert all(not e.get("structural") for e in r["edges"])


def _run_standalone() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
