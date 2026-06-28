"""Temporal layer: edge/node dating in the graph payload + as-of reconstruction.

Standalone:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_temporal_asof.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for sub in ("src", "research", "ui"):
    sys.path.insert(0, str(_ROOT / sub))

from investigator.graph import to_iso_date  # noqa: E402
import build_graph_prototype as bg  # noqa: E402
import server as srv  # noqa: E402


# --- to_iso_date normaliser ------------------------------------------------

def test_to_iso_date_formats():
    assert to_iso_date("Wed, 15 May 2024 12:00:00 GMT") == "2024-05-15"  # GNews RFC-2822
    assert to_iso_date("20240515T120000Z") == "2024-05-15"              # GDELT compact
    assert to_iso_date("2024-10-09T00:00:00Z") == "2024-10-09"          # ISO datetime
    assert to_iso_date("2024-10-09") == "2024-10-09"
    assert to_iso_date("") == ""
    assert to_iso_date(None) == ""
    assert to_iso_date("not a date") == ""


# --- _payload edge/node dating ---------------------------------------------

def _artifact() -> dict:
    def edge(s, t, etype, url=None, src="Publisher"):
        e = {"src_identifier": s, "dst_identifier": t, "type": etype,
             "relations": json.dumps({"type": "rel", "context": "ctx"}), "source": src}
        if url:
            e["search_url"] = url
        return e
    return {
        "events": [{"name": "run1", "query": "q"}],
        "source_dates": {"http://a.com/1": "2025-01-15"},
        "final_merged_graph": {
            "bridging_entities": [],
            "nodes": [
                {"identifier": "ALICE", "type": "entity", "data": {"type": "PERSON"}},
                {"identifier": "BOB", "type": "entity", "data": {"type": "PERSON"}},
                {"identifier": "E1", "type": "event", "data": {"date": "2025-03-02"}},
                {"identifier": "E2", "type": "event", "data": {"date": "2025-06-10"}},
            ],
            "edges": [
                edge("ALICE", "BOB", "affiliation", url="http://a.com/1"),
                edge("E1", "ALICE", "event_participation"),
                edge("E1", "BOB", "event_participation"),
                edge("E2", "ALICE", "event_participation"),
            ],
        },
    }


def test_payload_dates_edges_and_nodes():
    p = bg._payload(_artifact())
    by_id = {n["id"]: n for n in p["nodes"]}
    # ALICE in E1 (Mar) + E2 (Jun); BOB in E1 only.
    assert by_id["ALICE"]["firstSeen"] == "2025-03-02"
    assert by_id["ALICE"]["lastSeen"] == "2025-06-10"
    assert by_id["BOB"]["firstSeen"] == "2025-03-02"
    assert by_id["BOB"]["lastSeen"] == "2025-03-02"
    assert by_id["E1"]["firstSeen"] == "2025-03-02"
    aff = next(e for e in p["edges"] if e["type"] == "affiliation")
    assert aff["firstSeen"] == "2025-01-15"               # observed: article pub date
    assert aff["activeWindow"] == ["2025-03-02", "2025-03-02"]  # valid: shared event E1


def test_payload_flags_date_conflicts():
    art = _artifact()
    # give E1 a second date a year off, and an ordering edge that contradicts dates
    e1 = next(n for n in art["final_merged_graph"]["nodes"] if n["identifier"] == "E1")
    e1["data"]["date"] = ["2025-03-02", "2024-03-02"]   # ~365d apart -> conflict
    art["final_merged_graph"]["edges"].append({
        "src_identifier": "E2", "dst_identifier": "E1", "type": "event_followed_by",
        "relations": json.dumps({"type": "followed_by", "context": ""}),
    })  # E2(2025-06) "followed by" E1(2025-03) -> src after dst -> contradiction
    p = bg._payload(art)
    e1n = next(n for n in p["nodes"] if n["id"] == "E1")
    assert e1n["dateConflict"] and e1n["dateConflict"]["daysApart"] >= 365
    oc = next(e for e in p["edges"] if e["type"] == "event_followed_by")
    assert oc["dateConflict"] is not None


# --- _filter_payload_as_of -------------------------------------------------

def _payload_fixture() -> dict:
    return {
        "nodes": [
            {"id": "ALICE", "type": "entity", "firstSeen": "", "lastSeen": ""},
            {"id": "BOB", "type": "entity", "firstSeen": "", "lastSeen": ""},
            {"id": "CAROL", "type": "entity", "firstSeen": "", "lastSeen": ""},
            {"id": "ROOT", "type": "entity", "firstSeen": "", "lastSeen": ""},
            {"id": "E1", "type": "event", "firstSeen": "2025-03-02", "lastSeen": "2025-03-02"},
            {"id": "E2", "type": "event", "firstSeen": "2026-01-01", "lastSeen": "2026-01-01"},
        ],
        "edges": [
            # dated affiliation, asserted 2025-02
            {"source": "ALICE", "target": "BOB", "type": "affiliation",
             "structural": False, "firstSeen": "2025-02-01", "activeWindow": None},
            # undated affiliation -> always kept
            {"source": "ALICE", "target": "CAROL", "type": "affiliation",
             "structural": False, "firstSeen": "", "activeWindow": None},
            # structural hub edge keeps a node connected but is not a "real" link
            {"source": "ALICE", "target": "ROOT", "type": "evidence",
             "structural": True, "firstSeen": "", "activeWindow": None},
            {"source": "ROOT", "target": "BOB", "type": "evidence",
             "structural": True, "firstSeen": "", "activeWindow": None},
            {"source": "ROOT", "target": "CAROL", "type": "evidence",
             "structural": True, "firstSeen": "", "activeWindow": None},
            # participation edges (undated) to a late event
            {"source": "E2", "target": "BOB", "type": "event_participation",
             "structural": False, "firstSeen": "", "activeWindow": None},
        ],
    }


def test_no_args_is_identity():
    p = _payload_fixture()
    assert srv._filter_payload_as_of(p) is p


def test_event_hidden_after_its_date():
    p = _payload_fixture()
    f = srv._filter_payload_as_of(p, as_of="2025-06-01")
    ids = {n["id"] for n in f["nodes"]}
    assert "E1" in ids        # dated 2025-03, before as-of
    assert "E2" not in ids    # dated 2026-01, after as-of -> hidden
    # E2's participation edge goes with it
    assert not any(e["source"] == "E2" for e in f["edges"])


def test_affiliation_hidden_before_first_seen():
    p = _payload_fixture()
    early = srv._filter_payload_as_of(p, as_of="2025-01-15")
    assert not any(e["type"] == "affiliation" and e["source"] == "ALICE" and e["target"] == "BOB"
                   for e in early["edges"])           # asserted 2025-02, not yet known
    late = srv._filter_payload_as_of(p, as_of="2025-06-01")
    assert any(e["type"] == "affiliation" and e["source"] == "ALICE" and e["target"] == "BOB"
               for e in late["edges"])


def test_undated_edge_kept():
    p = _payload_fixture()
    f = srv._filter_payload_as_of(p, as_of="2025-01-15")
    assert any(e["source"] == "ALICE" and e["target"] == "CAROL" for e in f["edges"])


def test_structural_only_entity_pruned():
    # At a very early date, BOB's only real link (the 2025-02 affiliation) and its
    # late participation edge are both gone; only structural hub edges remain -> pruned.
    p = _payload_fixture()
    f = srv._filter_payload_as_of(p, as_of="2025-01-01")
    ids = {n["id"] for n in f["nodes"]}
    assert "BOB" not in ids
    assert "ALICE" in ids and "CAROL" in ids   # kept via the undated ALICE-CAROL link


def test_original_not_mutated():
    p = _payload_fixture()
    n_nodes, n_edges = len(p["nodes"]), len(p["edges"])
    srv._filter_payload_as_of(p, as_of="2025-01-01")
    assert len(p["nodes"]) == n_nodes and len(p["edges"]) == n_edges


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
