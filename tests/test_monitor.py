"""Standing-monitor (CEP) layer: watchlist, intersection, impact, digest.

Standalone:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_monitor.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.analytics.canonicalizer import CanonicalRegistry  # noqa: E402
from investigator.monitor.watchlist import Watchlist, load_watchlist  # noqa: E402
from investigator.monitor.intersect import intersect  # noqa: E402
from investigator.monitor import impact as I  # noqa: E402
from investigator.monitor.digest import build_digest  # noqa: E402


def _registry(names):
    r = CanonicalRegistry(Path(tempfile.mkdtemp()) / "reg.json")
    for n in names:
        r.resolve(n, "ORG", "inv::seed")
    return r


# --- registry.lookup (match-only) ------------------------------------------

def test_registry_lookup_matches_without_minting():
    r = _registry(["ACME CORP"])
    assert r.lookup("acme corp") == "ACME CORP"          # exact (case-insensitive)
    assert r.lookup("Acme  Corp.") == "ACME CORP"        # normalized
    assert r.lookup("TOTALLY NEW ORG") is None           # miss
    assert "TOTALLY NEW ORG" not in r.canonicals         # did NOT mint


# --- watchlist --------------------------------------------------------------

def test_watchlist_crud_and_roundtrip():
    p = Path(tempfile.mkdtemp()) / "wl.json"
    w = load_watchlist(p)
    assert w.add("Samidoun") and not w.add("samidoun")   # de-dupes case-insensitively
    w.add("Hamas"); w.domain = "terror financing"
    assert w.has("SAMIDOUN") and w.subjects() == ["SAMIDOUN", "HAMAS", "terror financing"]
    w.save()
    assert load_watchlist(p).entities == ["SAMIDOUN", "HAMAS"]
    assert w.remove("hamas") and not w.has("HAMAS")


# --- intersection filter ----------------------------------------------------

def _day_graph():
    return {
        "nodes": [
            {"identifier": "EV-KNOWN", "type": "event",
             "data": {"date": "2026-06-27", "event_type": "deal", "confidence": 0.9}},
            {"identifier": "EV-NOISE", "type": "event", "data": {"date": "2026-06-27"}},
            {"identifier": "ACME CORP", "type": "entity", "data": {}},
            {"identifier": "UNKNOWN LLC", "type": "entity", "data": {}},
        ],
        "edges": [
            {"src_identifier": "EV-KNOWN", "dst_identifier": "ACME CORP", "type": "event_participation"},
            {"src_identifier": "EV-NOISE", "dst_identifier": "UNKNOWN LLC", "type": "event_participation"},
        ],
    }


def test_intersect_keeps_only_kg_events():
    r = _registry(["ACME CORP"])
    wl = Watchlist(entities=["ACME CORP"])
    out = intersect(_day_graph(), r, {"ACME CORP"}, wl)
    assert len(out) == 1                                  # EV-NOISE dropped (UNKNOWN LLC not in KG)
    assert out[0]["event"]["id"] == "EV-KNOWN"
    assert out[0]["touched"] == ["ACME CORP"]
    assert out[0]["watched"] == ["ACME CORP"]


def test_intersect_watched_via_canonical():
    # watchlist holds an alias; intersect resolves it to the canonical for `watched`
    r = _registry(["ACME CORP"])
    r.alias_index["ACME"] = "ACME CORP"                   # ACME is an alias of ACME CORP
    wl = Watchlist(entities=["ACME"])                     # watched by the alias
    out = intersect(_day_graph(), r, {"ACME CORP"}, wl)
    assert out[0]["watched"] == ["ACME CORP"]


# --- impact model -----------------------------------------------------------

def _star_graph():
    # X at the centre, A/B/C direct, D/E one hop further (a small ripple network)
    nodes = [{"id": n, "prob": 0.5} for n in ("X", "A", "B", "C", "D", "E")]
    edges = [
        {"source": "X", "target": "A", "weight": 5}, {"source": "X", "target": "B", "weight": 5},
        {"source": "X", "target": "C", "weight": 5}, {"source": "A", "target": "B", "weight": 3},
        {"source": "B", "target": "C", "weight": 3}, {"source": "C", "target": "D", "weight": 2},
        {"source": "D", "target": "E", "weight": 1},
    ]
    return nodes, edges


def test_impact_ripples_and_decays():
    nodes, edges = _star_graph()
    res = I.impact_of_event(nodes, edges, ["X"], event_strength=1.0,
                            event_date="2026-06-28", today="2026-06-28", watched={"A"})
    assert res["usedBP"]                                  # 6 nodes -> a TMFG exists
    by = {x["entity"]: x for x in res["impacted"]}
    # direct neighbours (1 hop) outrank the far node (2+ hops)
    assert by["A"]["hops"] == 1 and by["D"]["hops"] >= 2
    assert by["A"]["score"] > by["D"]["score"]
    assert by["A"]["watched"] is True and by["B"]["watched"] is False


def test_impact_fallback_when_too_small():
    # 3-node graph: no TMFG -> topological fallback, still returns ranked impact
    nodes = [{"id": n, "prob": 0.5} for n in ("X", "A", "B")]
    edges = [{"source": "X", "target": "A", "weight": 2}, {"source": "A", "target": "B", "weight": 2}]
    res = I.impact_of_event(nodes, edges, ["X"], today="2026-06-28", event_date="2026-06-28")
    assert res["usedBP"] is False
    by = {x["entity"]: x for x in res["impacted"]}
    assert by["A"]["score"] > by["B"]["score"]           # nearer ranks higher


def test_impact_unknown_touched_is_empty():
    nodes, edges = _star_graph()
    assert I.impact_of_event(nodes, edges, ["NOT IN GRAPH"])["impacted"] == []


def test_recency_decay():
    assert I.recency_decay("2026-06-28", "2026-06-28") == 1.0
    assert I.recency_decay("2026-05-29", "2026-06-28", halflife_days=30) == 0.5   # ~30d -> half
    assert I.recency_decay("", "2026-06-28") == 0.5                               # undated -> neutral


# --- digest assembly --------------------------------------------------------

def test_build_digest_ranks_and_alerts():
    nodes, edges = _star_graph()

    class _Struct:  # only .entities/.edges are read by global_graph_dicts (unused here)
        entities, edges = {}, {}
    r = _registry(["X"])
    wl = Watchlist(entities=["A"])
    intersected = [{
        "event": {"id": "EV1", "date": "2026-06-28", "type": "deal", "confidence": 0.9},
        "touched": ["X"], "watched": [],
    }]
    digest = build_digest(intersected, _Struct(), r, wl, today="2026-06-28",
                          kg_nodes=nodes, kg_edges=edges, alert_threshold=0.1)
    assert digest["counts"]["events"] == 1
    assert digest["events"][0]["impacted"][0]["score"] >= digest["events"][0]["impacted"][-1]["score"]
    assert digest["events"][0]["alert"] is True          # top score above the 0.1 threshold


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
