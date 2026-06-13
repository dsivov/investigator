"""Stage 4 — score_graph_by_connectivity rework — TRIANGULATION_REVIEW.md §4.

Survival = credible evidence; relevance = 0.7^hops_to_root; score = relevance ×
prob; G8 = every survivor connected to root (evidence edge if needed).

Run with the tangos env:
    PYTHONPATH=.:src:/home/dsivov/Work/tangos_mvp <tangos-python> tests/test_stage4_score.py
"""

from __future__ import annotations

import copy
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import networkx as nx  # noqa: E402

from tangraph.graph.operations import (  # noqa: E402
    build_graph,
    evidence_probability,
    score_graph_by_connectivity,
)

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_stages.json.gz"


def _node(ident, prob=0.9, evidence=True):
    return {
        "identifier": ident,
        "unique_identifier": "u-" + ident,
        "data": {},
        "evidence": [{"strength": 1, "confidence": 1, "hypothesis": True}] if evidence else [],
        "prob": prob,
    }


def _tangraph(*edges):
    g = nx.DiGraph()
    g.add_edges_from(edges)
    return g


def _reachable(ids, edges, root):
    og = nx.Graph()
    og.add_nodes_from(ids)
    og.add_edges_from((e["src_identifier"], e["dst_identifier"]) for e in edges)
    return set(nx.single_source_shortest_path_length(og, root)) if root in og else set()


def test_survival_by_evidence():
    g = _tangraph(("ROOT", "A"), ("ROOT", "B"))
    nodes = [_node("ROOT"), _node("A"), _node("B", prob=0.0, evidence=False)]
    _s, _e, ids = score_graph_by_connectivity(g, [], nodes, root="ROOT")
    assert set(ids) == {"ROOT", "A"}        # B (no evidence) dropped


def test_root_always_kept_even_without_evidence():
    g = _tangraph(("ROOT", "A"))
    nodes = [_node("ROOT", prob=0.0, evidence=False), _node("A")]
    _s, _e, ids = score_graph_by_connectivity(g, [], nodes, root="ROOT")
    assert "ROOT" in ids


def test_relevance_decays_with_hops():
    g = _tangraph(("ROOT", "A"), ("A", "B"))          # B is 2 hops from root
    surv, _e, _i = score_graph_by_connectivity(g, [], [_node("ROOT"), _node("A"), _node("B")], root="ROOT")
    relv = {n["identifier"]: n["data"]["relevance_score"] for n in surv}
    assert relv["ROOT"] == 1.0
    assert abs(relv["A"] - 0.7) < 1e-9
    assert abs(relv["B"] - 0.49) < 1e-9


def test_score_is_relevance_times_prob():
    g = _tangraph(("ROOT", "A"))
    surv, _e, _i = score_graph_by_connectivity(g, [], [_node("ROOT"), _node("A", prob=0.8)], root="ROOT")
    a = next(n for n in surv if n["identifier"] == "A")
    assert abs(a["score"] - 0.7 * 0.8) < 1e-9


def test_evidence_only_node_wired_to_root():
    g = _tangraph(("ROOT", "A"))                      # C is not in the affiliation graph
    surv, edges, ids = score_graph_by_connectivity(g, [], [_node("ROOT"), _node("A"), _node("C")], root="ROOT")
    assert "C" in ids
    assert any(e["type"] == "evidence" and e["src_identifier"] == "C" and e["dst_identifier"] == "ROOT" for e in edges)
    c = next(n for n in surv if n["identifier"] == "C")
    assert abs(c["data"]["relevance_score"] - 0.49) < 1e-9   # evidence-hop-cost = 2


def test_orphan_edges_dropped():
    g = _tangraph(("ROOT", "A"))
    edges_in = [
        {"src_identifier": "ROOT", "dst_identifier": "A", "type": "affiliation"},
        {"src_identifier": "A", "dst_identifier": "GHOST", "type": "affiliation"},
    ]
    _s, edges, ids = score_graph_by_connectivity(g, edges_in, [_node("ROOT"), _node("A")], root="ROOT")
    assert all(e["src_identifier"] in ids and e["dst_identifier"] in ids for e in edges)


def test_G8_every_survivor_connected_to_root():
    g = _tangraph(("ROOT", "A"), ("A", "B"))
    nodes = [_node("ROOT"), _node("A"), _node("B"), _node("LONE")]   # LONE disconnected
    _s, edges, ids = score_graph_by_connectivity(g, [], nodes, root="ROOT")
    assert _reachable(ids, edges, "ROOT") == set(ids)


def _run_golden_validation() -> None:
    if not GOLDEN.exists():
        return  # golden fixture not shipped in the public repo
    g = json.load(gzip.open(GOLDEN))
    _e, root, _hi, _lo, tg = build_graph(g["dedup_nodes"], {"chunks": g["chunks"]}, g["representatives"])
    nodes = copy.deepcopy(g["consolidator_out_nodes"])
    for n in nodes:                                   # apply the new prob (golden was pre-fix)
        n["prob"] = evidence_probability(n.get("evidence", []))
    surv, edges, ids = score_graph_by_connectivity(tg, copy.deepcopy(g["registered_edges"]), nodes, root=root)
    evidenced = sum(1 for n in g["consolidator_out_nodes"] if n.get("evidence"))
    assert _reachable(ids, edges, root) == set(ids), "G8 violated: a survivor is disconnected from root"
    assert all(n.get("evidence") for n in surv if n["identifier"] != root), "a no-evidence survivor slipped through"
    print(f"golden: {len(surv)} survivors (evidenced={evidenced}; old relevance-filter kept 8); G8 holds; root={root}")


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
    _run_golden_validation()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
