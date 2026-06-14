"""Unit tests for the index-ified resolve_edge_endpoints (DATA_MODEL migration step 6).

Imports investigator.graph.dedup (loads WordLlama at import), so run with the
tangos env:

    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_register_edges.py

Locks in: UUID assigned once per edge, endpoint resolution via the node index
(by identifier or representative_identifier, first-node-wins), relations
json-dumped, and edges missing a resolved endpoint dropped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.graph.dedup import resolve_edge_endpoints  # noqa: E402


def _node(identifier, rep=None, uid=None):
    return {
        "identifier": identifier,
        "representative_identifier": rep or identifier,
        "unique_identifier": uid or f"uid-{identifier}",
    }


def _edge(a, b, relations=None):
    return {"source_node": a, "target_node": b, "relations": relations if relations is not None else [], "attributes": {}}


def test_resolves_endpoints_by_identifier():
    nodes = [_node("ACME"), _node("BOB")]
    out = resolve_edge_endpoints(nodes, [_edge("ACME", "BOB")])
    assert len(out) == 1
    e = out[0]
    assert e["src_identifier"] == "ACME" and e["src_unique_identifier"] == "uid-ACME"
    assert e["dst_identifier"] == "BOB" and e["dst_unique_identifier"] == "uid-BOB"
    assert "source_node" not in e and "target_node" not in e
    assert e["type"] == "affiliation"


def test_resolves_endpoint_by_representative_identifier():
    nodes = [_node("ACME CORP", rep="ACME CORP"), _node("BOB")]
    # edge references the rep name
    out = resolve_edge_endpoints([_node("X", rep="ACME CORP")] + nodes, [_edge("ACME CORP", "BOB")])
    assert out[0]["src_identifier"] in ("X", "ACME CORP")  # whichever node owns the key first
    assert out[0]["dst_identifier"] == "BOB"


def test_uuid_assigned_once_and_present():
    nodes = [_node("A"), _node("B"), _node("C")]
    out = resolve_edge_endpoints(nodes, [_edge("A", "B"), _edge("B", "C")])
    uids = [e["unique_identifier"] for e in out]
    assert all(isinstance(u, str) and u for u in uids)
    assert len(set(uids)) == len(uids)  # distinct per edge


def test_edge_missing_matched_endpoint_is_dropped():
    nodes = [_node("A")]   # no node for "GHOST"
    out = resolve_edge_endpoints(nodes, [_edge("A", "GHOST")])
    assert out == []       # dst never resolved -> not registered


def test_relations_are_json_dumped():
    nodes = [_node("A"), _node("B")]
    rel = {"name": "B", "type": "affiliation", "context": "ctx"}
    out = resolve_edge_endpoints(nodes, [_edge("A", "B", relations=rel)])
    assert out[0]["relations"] == json.dumps(rel)
    assert json.loads(out[0]["relations"]) == rel


def test_first_node_wins_on_key_collision():
    # two nodes both own key "K" (one by identifier, one by rep) -> first wins
    n1 = _node("K", uid="uid-1")
    n2 = _node("OTHER", rep="K", uid="uid-2")
    out = resolve_edge_endpoints([n1, n2, _node("Z")], [_edge("K", "Z")])
    assert out[0]["src_unique_identifier"] == "uid-1"   # n1 registered "K" first


def test_unmatched_edge_does_not_crash_and_is_dropped():
    out = resolve_edge_endpoints([], [_edge("A", "B")])
    assert out == []


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
