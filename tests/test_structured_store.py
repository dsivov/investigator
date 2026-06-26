"""StructuredStore: the sidecar that preserves every structured node/edge
property LightRAG's fixed schema would drop, merged across investigations.

Standalone:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_structured_store.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.analytics.structured_store import StructuredStore  # noqa: E402


def _node(ident, **kw):
    d = {"identifier": ident, "type": "entity", "data": {"type": kw.pop("etype", "PERSON")}}
    d.update(kw)
    return d


def test_entity_preserves_all_props():
    ss = StructuredStore(tempfile.mktemp())
    ss.merge_entity("ALICE", _node(
        "ALICE", prob=0.9, score=0.8, posterior_prob=0.95,
        labels=["Alice Smith", "ALICE"], runs=["inv_a"], themes=["fraud"],
        source="https://x.test/a",
        evidence=[{"reasoning": "Alice did X", "confidence": 0.9, "strength": 0.8,
                   "hypothesis": True, "doc_id": "https://x.test/a"}],
    ), "inv::a")
    r = ss.get_entity("ALICE")
    assert r["prob"] == 0.9 and r["score"] == 0.8 and r["posterior_prob"] == 0.95
    assert "Alice Smith" in r["labels"] and "ALICE" not in r["labels"]  # self-name dropped
    assert r["runs"] == ["inv_a"] and r["themes"] == ["fraud"]
    assert r["evidence_count"] == 1 and r["evidence"][0]["confidence"] == 0.9
    assert r["investigations"] == ["inv::a"]


def test_entity_merges_across_investigations():
    ss = StructuredStore(tempfile.mktemp())
    ss.merge_entity("ALICE", _node("ALICE", prob=0.6, labels=["Alice Smith"], runs=["r1"],
                                   evidence=[{"reasoning": "claim1", "doc_id": "u1"}]), "inv::a")
    ss.merge_entity("ALICE", _node("ALICE", prob=0.9, labels=["A. Smith"], runs=["r2"],
                                   evidence=[{"reasoning": "claim2", "doc_id": "u2"}]), "inv::b")
    r = ss.get_entity("ALICE")
    assert r["prob"] == 0.9                              # max across investigations
    assert set(r["labels"]) == {"Alice Smith", "A. Smith"}  # union of true aliases
    assert set(r["runs"]) == {"r1", "r2"}
    assert r["evidence_count"] == 2                  # union of distinct claims
    assert set(r["investigations"]) == {"inv::a", "inv::b"}
    assert set(r["beliefs"].keys()) == {"inv::a", "inv::b"}  # per-investigation breakdown


def test_evidence_dedup():
    ss = StructuredStore(tempfile.mktemp())
    ev = [{"reasoning": "same claim", "doc_id": "u1"}]
    ss.merge_entity("BOB", _node("BOB", evidence=ev), "inv::a")
    ss.merge_entity("BOB", _node("BOB", evidence=ev), "inv::b")  # identical -> not double counted
    assert ss.get_entity("BOB")["evidence_count"] == 1


def test_edge_preserves_relation_and_hypothesis():
    ss = StructuredStore(tempfile.mktemp())
    ss.merge_edge("ALICE", "ACME", {
        "type": "affiliation",
        "relations": {"type": "works_at", "context": "Alice is CEO of Acme"},
        "search_url": "https://x.test/e", "is_hypothesis": False, "weight": 2.0,
    }, "inv::a")
    e = ss.edges[next(iter(ss.edges))]
    assert e["relations"][0]["type"] == "works_at"
    assert e["relations"][0]["context"].startswith("Alice is CEO")
    assert "https://x.test/e" in e["sources"]
    assert e["weight"] == 2.0


def test_edge_undirected_merge():
    ss = StructuredStore(tempfile.mktemp())
    ss.merge_edge("A", "B", {"relations": {"type": "knows", "context": "x"}}, "inv::a")
    ss.merge_edge("B", "A", {"relations": {"type": "partner", "context": "y"}}, "inv::b")
    assert len(ss.edges) == 1                        # A-B == B-A
    e = ss.edges[next(iter(ss.edges))]
    assert len(e["relations"]) == 2                  # both relation facts kept
    assert set(e["investigations"]) == {"inv::a", "inv::b"}


def test_save_load_roundtrip():
    p = Path(tempfile.mktemp())
    ss = StructuredStore(p)
    ss.merge_entity("ALICE", _node("ALICE", prob=0.7), "inv::a")
    ss.save()
    assert StructuredStore(p).get_entity("ALICE")["prob"] == 0.7


def test_entity_timeline_events_captured():
    ss = StructuredStore(tempfile.mktemp())
    ss.merge_entity("ALICE", _node("ALICE", data={"type": "PERSON", "timeline_events": [
        {"date": "2025-03-01", "event": "indicted"},
        {"date": "", "event": "questioned"}]}), "inv::a")
    tl = ss.entity_timeline("ALICE")
    assert {t["event"] for t in tl} == {"indicted", "questioned"}
    assert tl[0]["date"] == "2025-03-01"          # dated first
    assert tl[-1]["date"] == ""                    # undated last


def test_event_layer_and_participated_timeline():
    ss = StructuredStore(tempfile.mktemp())
    ss.merge_entity("ALICE", _node("ALICE"), "inv::a")
    ss.merge_event("ALICE CHARGED WITH FRAUD",
                   {"data": {"date": ["2025-05-10"], "event_type": "legal"}},
                   ["ALICE", "ACME"], "inv::a")
    assert "ALICE CHARGED WITH FRAUD" in ss.events
    tl = ss.entity_timeline("ALICE")
    assert any(t["kind"] == "event" and t["date"] == "2025-05-10" for t in tl)
    assert ss.date_range("ALICE") == ("2025-05-10", "2025-05-10")


def test_temporal_edges_and_event_merge():
    ss = StructuredStore(tempfile.mktemp())
    ss.merge_event("E1", {"data": {"date": "2025-01-01"}}, ["A"], "inv::a")
    ss.merge_event("E1", {"data": {"date": ["2025-01-02"]}}, ["B"], "inv::b")  # merge
    assert ss.events["E1"]["dates"] == ["2025-01-01", "2025-01-02"]
    assert set(ss.events["E1"]["participants"]) == {"A", "B"}
    ss.merge_temporal_edge("event_followed_by", "E1", "E2")
    ss.merge_temporal_edge("event_followed_by", "E1", "E2")  # dedup
    assert len(ss.temporal_edges) == 1


def test_temporal_roundtrip():
    p = Path(tempfile.mktemp())
    ss = StructuredStore(p)
    ss.merge_entity("ALICE", _node("ALICE", data={"type": "PERSON",
                    "timeline_events": [{"date": "2025-03-01", "event": "x"}]}), "inv::a")
    ss.merge_event("E1", {"data": {"date": "2025-03-02"}}, ["ALICE"], "inv::a")
    ss.save()
    ss2 = StructuredStore(p)
    assert len(ss2.entity_timeline("ALICE")) == 2
    assert "E1" in ss2.events


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
