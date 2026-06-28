"""CEP pattern matching (monitor Phase 2): rules over dated, linked events.

Standalone:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_patterns.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.monitor.patterns import match_rules  # noqa: E402
from investigator.monitor.rules import validate_rule, DEFAULT_RULES  # noqa: E402

RULE = {
    "name": "sanction then transaction", "windowDays": 30, "severity": "high",
    "steps": [{"types": ["sanctions"]},
              {"types": ["financial_crime"], "keywords": ["transfer"]}],
}


def _ev(date, type_, parts, desc=""):
    return {"dates": [date], "participants": parts, "type": type_, "description": desc}


# --- step matching ----------------------------------------------------------

def test_two_step_chain_matches_within_window_and_link():
    events = {
        "S": _ev("2026-01-01", "sanctions", ["A"]),
        "T": _ev("2026-01-20", "financial_crime", ["B", "C"]),
    }
    adj = {"A": {"B"}, "B": {"A"}}                       # A linked to B -> chain links
    m = match_rules(events, adj, [RULE])
    assert len(m) == 1
    assert [e["id"] for e in m[0]["events"]] == ["S", "T"]
    assert "B" in m[0]["bridges"]                        # the linking actor


def test_keyword_step_matches():
    events = {
        "S": _ev("2026-01-01", "sanctions", ["A"]),
        "T": _ev("2026-01-10", "other", ["A"], desc="A made a wire transfer to C"),
    }
    assert len(match_rules(events, {}, [RULE])) == 1     # shared participant A links them


def test_out_of_window_no_match():
    events = {"S": _ev("2026-01-01", "sanctions", ["A"]),
              "T": _ev("2026-03-01", "financial_crime", ["A"], desc="transfer")}  # 59d > 30
    assert match_rules(events, {}, [RULE]) == []


def test_unlinked_no_match():
    events = {"S": _ev("2026-01-01", "sanctions", ["A"]),
              "T": _ev("2026-01-10", "financial_crime", ["Z"], desc="transfer")}  # no link A~Z
    assert match_rules(events, {}, [RULE]) == []


def test_ordering_enforced():
    # transaction BEFORE the sanction -> not a forward chain
    events = {"S": _ev("2026-02-01", "sanctions", ["A"]),
              "T": _ev("2026-01-10", "financial_crime", ["A"], desc="transfer")}
    assert match_rules(events, {}, [RULE]) == []


def test_hub_link_is_ignored():
    # A and Z share only a hub (USA, degree > threshold) -> not a meaningful link
    events = {"S": _ev("2026-01-01", "sanctions", ["A"]),
              "T": _ev("2026-01-10", "financial_crime", ["Z"], desc="transfer")}
    hub_neighbours = {f"N{i}" for i in range(40)}
    adj = {"USA": hub_neighbours, "A": {"USA"}, "Z": {"USA"}}
    assert match_rules(events, adj, [RULE]) == []


def test_recent_since_filters_stale_chains():
    events = {"S": _ev("2026-01-01", "sanctions", ["A"]),
              "T": _ev("2026-01-20", "financial_crime", ["A"], desc="transfer")}
    assert len(match_rules(events, {}, [RULE], recent_since="2026-01-15")) == 1   # final 01-20 >= cutoff
    assert match_rules(events, {}, [RULE], recent_since="2026-02-01") == []        # final before cutoff


def test_watchlist_scoping():
    events = {"S": _ev("2026-01-01", "sanctions", ["A"]),
              "T": _ev("2026-01-20", "financial_crime", ["A"], desc="transfer")}
    assert len(match_rules(events, {}, [RULE], watched={"A"})) == 1
    assert match_rules(events, {}, [RULE], watched={"SOMEONE ELSE"}) == []


def test_dedup_same_chain():
    events = {"S": _ev("2026-01-01", "sanctions", ["A"]),
              "T": _ev("2026-01-20", "financial_crime", ["A"], desc="transfer")}
    m = match_rules(events, {}, [RULE, RULE])             # same rule twice
    assert len({tuple(e["id"] for e in x["events"]) for x in m}) == len(m)  # no dup chains per rule


def test_undated_events_skipped():
    events = {"S": _ev("", "sanctions", ["A"]),           # no date -> can't order
              "T": _ev("2026-01-20", "financial_crime", ["A"], desc="transfer")}
    assert match_rules(events, {}, [RULE]) == []


# --- rule validation --------------------------------------------------------

def test_validate_rule():
    assert validate_rule(RULE)
    assert all(validate_rule(r) for r in DEFAULT_RULES)
    assert not validate_rule({"name": "", "steps": [{"types": ["x"]}]})       # no name
    assert not validate_rule({"name": "x", "steps": []})                      # no steps
    assert not validate_rule({"name": "x", "steps": [{}]})                    # empty step


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
