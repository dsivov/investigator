"""Level 3: temporal consistency / conflicting-date detection (read-time).

Standalone:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_temporal_consistency.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.graph.temporal_consistency import (  # noqa: E402
    date_spread_conflict, ordering_conflicts, scan,
)


# --- date_spread_conflict --------------------------------------------------

def test_wide_spread_is_flagged():
    c = date_spread_conflict(["2024-05-10", "2024-09-20"])
    assert c and c["daysApart"] == 133
    assert c["min"] == "2024-05-10" and c["max"] == "2024-09-20"


def test_year_off_extraction_flagged():
    # the real Yoon martial-law case: same event dated a year apart
    c = date_spread_conflict(["2023-12-03", "2024-12-03"])
    assert c and c["daysApart"] == 366


def test_close_dates_not_flagged():
    assert date_spread_conflict(["2024-05-10", "2024-05-20"]) is None  # 10d <= 30


def test_month_contains_day_not_flagged():
    # "2024-05" (whole month) is compatible with "2024-05-10"
    assert date_spread_conflict(["2024-05", "2024-05-10"]) is None


def test_year_only_skipped():
    # year-only is too imprecise to call a conflict (avoid false positives)
    assert date_spread_conflict(["2024", "2024-09-20"]) is None


def test_single_date_no_conflict():
    assert date_spread_conflict(["2024-05-10"]) is None
    assert date_spread_conflict([]) is None


def test_tolerance_is_tunable():
    assert date_spread_conflict(["2024-05-01", "2024-05-25"], tol_days=10)["daysApart"] == 24
    assert date_spread_conflict(["2024-05-01", "2024-05-25"], tol_days=60) is None


# --- ordering_conflicts ----------------------------------------------------

def test_ordering_contradiction_flagged():
    # followed_by means src is EARLIER; here src is dated AFTER dst -> contradiction
    ed = {"A": ["2024-09-01"], "B": ["2024-05-01"]}
    out = ordering_conflicts(ed, [{"type": "event_followed_by", "src": "A", "dst": "B"}])
    assert len(out) == 1 and out[0]["daysApart"] == 123


def test_consistent_ordering_passes():
    ed = {"A": ["2024-05-01"], "B": ["2024-09-01"]}
    assert ordering_conflicts(ed, [{"type": "event_followed_by", "src": "A", "dst": "B"}]) == []


def test_ordering_ignores_non_followed_by_and_missing_dates():
    ed = {"A": ["2024-09-01"]}  # B has no date
    assert ordering_conflicts(ed, [{"type": "event_coincident", "src": "A", "dst": "B"}]) == []
    assert ordering_conflicts(ed, [{"type": "event_followed_by", "src": "A", "dst": "B"}]) == []


def test_edge_end_key_aliases():
    # tolerate payload (source/target) and raw (src_identifier/dst_identifier) keys
    ed = {"A": ["2024-09-01"], "B": ["2024-05-01"]}
    assert len(ordering_conflicts(ed, [{"type": "event_followed_by", "source": "A", "target": "B"}])) == 1
    assert len(ordering_conflicts(ed, [{"type": "event_followed_by",
                                        "src_identifier": "A", "dst_identifier": "B"}])) == 1


# --- scan ------------------------------------------------------------------

def test_scan_combines_both():
    ed = {"E": ["2024-01-01", "2024-08-01"], "A": ["2024-09-01"], "B": ["2024-05-01"]}
    res = scan(ed, [{"type": "event_followed_by", "src": "A", "dst": "B"}])
    assert "E" in res["events"]
    assert len(res["orderings"]) == 1


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
