"""Unit tests for build_network_analysis_payload -- cross-run focus.

Mocks TMFGResult + BeliefPropagationResult directly (rather than running
construct_tmfg + propagate end-to-end) so the tests stay focused on the
response-builder logic for runs_in_session / bridging_entities /
is_cross_investigation / runs_spanned.

Runs under pytest *or* standalone:

    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_response_builder.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import networkx as nx  # noqa: E402

from tangraph.graph.tmfg import TMFGResult  # noqa: E402
from tangraph.graph.junction_tree import BeliefPropagationResult  # noqa: E402
from tangraph.pipeline.response_builder import build_network_analysis_payload  # noqa: E402


def _mock_tmfg(members: list[str], *, fill_in_pairs: list[tuple] = ()) -> TMFGResult:
    """Build a minimal TMFGResult containing one tetrahedron of `members`."""
    g = nx.Graph()
    for i, a in enumerate(members):
        for b in members[i + 1:]:
            g.add_edge(a, b, weight=1.0)
    fill_in = {frozenset(p) for p in fill_in_pairs}
    return TMFGResult(
        graph=g,
        tetrahedra=[set(members)],
        separators=[],
        clique_tree=nx.Graph(),
        fill_in_edges=fill_in,
    )


def _mock_bp(members: list[str], posterior_map: dict[str, float]) -> BeliefPropagationResult:
    posterior = {m: posterior_map.get(m, 0.5) for m in members}
    prior = {m: 0.5 for m in members}
    delta = {m: posterior[m] - 0.5 for m in members}
    return BeliefPropagationResult(posterior=posterior, prior=prior, delta=delta)


def _node(ident: str, *, runs=None, score=0.5, **kw) -> dict:
    n = {"identifier": ident, "data": {}, "prob": 0.5, "score": score}
    if runs is not None:
        n["runs"] = runs
    n.update(kw)
    return n


# --- legacy (no runs anywhere) ---------------------------------------------

def test_legacy_no_runs_field_anywhere_emits_empty_lists():
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {})
    entities = [_node(m) for m in members]
    payload = build_network_analysis_payload(tmfg, bp, entities, [])
    assert payload["runs_in_session"] == []
    assert payload["bridging_entities"] == []
    # No theme should claim cross_investigation when nothing has runs.
    for t in payload["themes"]:
        assert t["is_cross_investigation"] is False
        assert "runs_spanned" not in t
    # Same for hypothesis edges
    for h in payload["hypothesis_edges"]:
        assert h["is_cross_investigation"] is False


# --- single run across all entities ----------------------------------------

def test_single_run_no_bridges_no_cross_flag():
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {})
    entities = [_node(m, runs=["only_run"]) for m in members]
    payload = build_network_analysis_payload(tmfg, bp, entities, [])
    assert payload["runs_in_session"] == ["only_run"]
    # Every entity is in exactly 1 run => no bridges
    assert payload["bridging_entities"] == []
    # Theme spans only 1 run => is_cross_investigation = false
    for t in payload["themes"]:
        assert t["is_cross_investigation"] is False
        assert t["runs_spanned"] == ["only_run"]


# --- two-run scenario with bridge ------------------------------------------

def test_two_run_bridge_surfaces_in_payload():
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members, fill_in_pairs=[("A", "C"), ("B", "D")])
    bp = _mock_bp(members, {"A": 0.95, "B": 0.5, "C": 0.5, "D": 0.5})
    entities = [
        _node("A", runs=["evA", "evB"], score=0.9),  # bridge: in both runs
        _node("B", runs=["evA"]),
        _node("C", runs=["evB"]),
        _node("D", runs=["evA"]),
    ]
    payload = build_network_analysis_payload(tmfg, bp, entities, [])
    assert payload["runs_in_session"] == ["evA", "evB"]
    # A is the only bridging entity (runs=["evA","evB"])
    assert len(payload["bridging_entities"]) == 1
    b = payload["bridging_entities"][0]
    assert b["identifier"] == "A"
    assert b["runs"] == ["evA", "evB"]
    assert b["n_runs"] == 2
    # Theme containing A,B,C,D spans both runs.
    theme = payload["themes"][0]
    assert theme["is_cross_investigation"] is True
    assert theme["runs_spanned"] == ["evA", "evB"]
    # Hypothesis edge A-C spans both runs.
    h = next((x for x in payload["hypothesis_edges"]
              if x["endpoints"] == ["A", "C"]), None)
    assert h is not None
    assert h["is_cross_investigation"] is True
    assert h["runs_spanned"] == ["evA", "evB"]


# --- three-run scenario with multi-run bridge ------------------------------

def test_three_runs_bridge_ordered_by_n_runs_desc():
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {})
    entities = [
        _node("A", runs=["e1", "e2", "e3"]),   # in all 3
        _node("B", runs=["e1", "e2"]),         # in 2
        _node("C", runs=["e1"]),               # in 1
        _node("D", runs=["e3"]),               # in 1
    ]
    payload = build_network_analysis_payload(tmfg, bp, entities, [])
    assert payload["runs_in_session"] == ["e1", "e2", "e3"]
    bridges = payload["bridging_entities"]
    # Sorted by n_runs desc -> A (3), then B (2). C and D drop (1 run each).
    assert [b["identifier"] for b in bridges] == ["A", "B"]
    assert bridges[0]["n_runs"] == 3
    assert bridges[1]["n_runs"] == 2


# --- mixed entities (some with runs, some legacy) --------------------------

def test_mixed_runs_only_runful_appear_in_bridge_calc():
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {})
    entities = [
        _node("A", runs=["e1", "e2"]),         # bridge
        _node("B"),                            # legacy
        _node("C", runs=["e1"]),
        _node("D"),                            # legacy
    ]
    payload = build_network_analysis_payload(tmfg, bp, entities, [])
    assert payload["runs_in_session"] == ["e1", "e2"]
    assert len(payload["bridging_entities"]) == 1
    assert payload["bridging_entities"][0]["identifier"] == "A"
    # Theme contains A (e1,e2) + B (none) + C (e1) + D (none)
    # => runs_spanned = {e1, e2} => is_cross_investigation = true
    theme = payload["themes"][0]
    assert theme["is_cross_investigation"] is True
    assert theme["runs_spanned"] == ["e1", "e2"]


# --- cross_event_leads ------------------------------------------------------

def _edge(src, dst, **kw):
    e = {"src_identifier": src, "dst_identifier": dst,
         "type": "affiliation", "is_hypothesis": False}
    e.update(kw)
    return e


def test_cross_event_lead_via_single_bridge():
    # A (runA), B (runB), C (in both) -- attested edges C-A and C-B.
    # No direct A-B edge. Expect one cross_event_lead.
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {"C": 0.9})
    entities = [
        _node("A", runs=["runA"], score=0.5),
        _node("B", runs=["runB"], score=0.5),
        _node("C", runs=["runA", "runB"], score=0.9),
        _node("D", runs=["runA"], score=0.4),
    ]
    edges = [_edge("C", "A"), _edge("C", "B")]
    payload = build_network_analysis_payload(tmfg, bp, entities, edges)
    leads = payload["cross_event_leads"]
    assert len(leads) == 1, f"expected 1 cross-event lead, got {leads}"
    lead = leads[0]
    assert sorted(lead["endpoints"]) == ["A", "B"]
    assert lead["bridges"] == ["C"]
    assert lead["runs_spanned"] == ["runA", "runB"]
    assert lead["score"] == round(0.9, 3)


def test_cross_event_lead_score_sums_multiple_bridges():
    members = ["A", "B", "C1", "C2"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {"C1": 0.8, "C2": 0.7})
    entities = [
        _node("A", runs=["runA"]),
        _node("B", runs=["runB"]),
        _node("C1", runs=["runA", "runB"]),
        _node("C2", runs=["runA", "runB"]),
    ]
    edges = [
        _edge("C1", "A"), _edge("C1", "B"),
        _edge("C2", "A"), _edge("C2", "B"),
    ]
    payload = build_network_analysis_payload(tmfg, bp, entities, edges)
    leads = payload["cross_event_leads"]
    assert len(leads) == 1
    assert sorted(leads[0]["bridges"]) == ["C1", "C2"]
    # Score = 0.8 + 0.7
    assert abs(leads[0]["score"] - 1.5) < 1e-6


def test_cross_event_lead_filters_same_run_pairs():
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {})
    # A and B are both in runA -> NOT a cross-event lead
    entities = [
        _node("A", runs=["runA"]),
        _node("B", runs=["runA"]),
        _node("C", runs=["runA", "runB"]),
        _node("D", runs=["runB"]),
    ]
    edges = [_edge("C", "A"), _edge("C", "B")]
    payload = build_network_analysis_payload(tmfg, bp, entities, edges)
    assert payload["cross_event_leads"] == []


def test_cross_event_lead_filters_when_no_shared_bridge():
    # A (runA), B (runB), but no entity C exists that's in both runs and
    # connected to both -> no lead.
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {})
    entities = [
        _node("A", runs=["runA"]),
        _node("B", runs=["runB"]),
        _node("C", runs=["runA"]),  # NOT a bridge
        _node("D", runs=["runB"]),
    ]
    edges = [_edge("C", "A"), _edge("C", "B")]
    payload = build_network_analysis_payload(tmfg, bp, entities, edges)
    assert payload["cross_event_leads"] == []


def test_cross_event_lead_ignores_hypothesis_edges():
    # A bridge exists but its edges to A and B are TMFG fill-ins, not
    # source-attested -> no lead.
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {"C": 0.9})
    entities = [
        _node("A", runs=["runA"]),
        _node("B", runs=["runB"]),
        _node("C", runs=["runA", "runB"]),
        _node("D", runs=["runA"]),
    ]
    edges = [
        _edge("C", "A", is_hypothesis=True),
        _edge("C", "B", is_hypothesis=True),
    ]
    payload = build_network_analysis_payload(tmfg, bp, entities, edges)
    assert payload["cross_event_leads"] == []


def test_cross_event_lead_ignores_event_participation_edges():
    # Participant edges are synthetic; they shouldn't anchor cross-event leads
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {"C": 0.9})
    entities = [
        _node("A", runs=["runA"]),
        _node("B", runs=["runB"]),
        _node("C", runs=["runA", "runB"]),
        _node("D", runs=["runA"]),
    ]
    edges = [
        _edge("C", "A", type="event_participation"),
        _edge("C", "B", type="event_participation"),
    ]
    payload = build_network_analysis_payload(tmfg, bp, entities, edges)
    assert payload["cross_event_leads"] == []


def test_cross_event_lead_skips_endpoint_that_spans_multiple_runs():
    # If A is itself a bridging entity (runs=[runA, runB]) it cannot be a
    # single-run endpoint of a cross-event lead.
    members = ["A", "B", "C", "D"]
    tmfg = _mock_tmfg(members)
    bp = _mock_bp(members, {})
    entities = [
        _node("A", runs=["runA", "runB"]),   # already cross-event
        _node("B", runs=["runB"]),
        _node("C", runs=["runA", "runB"]),
        _node("D", runs=["runA"]),
    ]
    edges = [_edge("C", "A"), _edge("C", "B")]
    payload = build_network_analysis_payload(tmfg, bp, entities, edges)
    # A is multi-run -> not a single-run endpoint -> no lead from this triangle
    assert payload["cross_event_leads"] == []


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
