"""Stage 4 — evidence_probability (prob) tests — TRIANGULATION_REVIEW.md §4 / F10.

Run with the tangos env:
    PYTHONPATH=.:src:/home/dsivov/Work/tangos_mvp <tangos-python> tests/test_stage4_prob.py
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tangraph.graph.operations import evidence_probability  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_stages.json.gz"


def _ev(strength, confidence=1.0, hypothesis=True):
    return {"strength": strength, "confidence": confidence, "hypothesis": hypothesis}


def test_strength_drives_prob():
    # confidence cancels for a lone evidence; strength is the magnitude
    assert evidence_probability([_ev(1.0)]) == 1.0
    assert evidence_probability([_ev(0.5)]) == 0.75
    assert evidence_probability([_ev(0.0)]) == 0.5


def test_confidence_actually_weights_competing_evidence():
    # high-confidence strong vs low-confidence weak -> pulled toward the strong one
    p = evidence_probability([_ev(1.0, confidence=0.9), _ev(0.0, confidence=0.1)])
    assert 0.9 < p <= 1.0


def test_contradicting_evidence_lowers_prob():
    support_only = evidence_probability([_ev(1.0, hypothesis=True)])
    with_contra = evidence_probability([_ev(1.0, hypothesis=True), _ev(1.0, hypothesis=False)])
    assert with_contra < support_only
    assert with_contra == 0.5                                   # equal-and-opposite -> neutral
    assert evidence_probability([_ev(1.0, hypothesis=False)]) == 0.0   # pure contradiction


def test_single_evidence_gets_real_prob_not_zero():
    # the old t-test scorer skipped evidence_count<=1 -> prob stayed 0 (a bug)
    assert abs(evidence_probability([_ev(0.8, confidence=0.7)]) - 0.9) < 1e-9


def test_empty_or_zero_confidence_is_zero():
    assert evidence_probability([]) == 0.0
    assert evidence_probability([_ev(1.0, confidence=0.0)]) == 0.0


def test_missing_strength_contributes_no_signal():
    # PR2 removed the |score| fallback; missing strength now contributes nothing
    # (was: signed |score| × confidence). Defensive — production runs always
    # have strength because the Evidence schema requires it.
    assert evidence_probability([{"confidence": 1.0, "hypothesis": True}]) == 0.5


def test_range_always_0_1():
    import random
    for _ in range(200):
        evs = [_ev(random.random(), random.random(), random.random() > 0.5) for _ in range(random.randint(1, 5))]
        p = evidence_probability(evs)
        assert 0.0 <= p <= 1.0


def _run_golden_validation() -> None:
    if not GOLDEN.exists():
        return  # golden fixture not shipped in the public repo
    g = json.load(gzip.open(GOLDEN))
    nodes = [n for n in g["consolidator_out_nodes"] if n.get("evidence")]
    probs = sorted({round(evidence_probability(n["evidence"]), 3) for n in nodes})
    print(f"golden: {len(nodes)} evidenced nodes; distinct prob values now = {len(probs)}")
    print("  prob range:", min(probs), "to", max(probs))
    # old scorer produced only {0.75, 1.0}; the fix must discriminate more finely
    assert len(probs) > 2, "prob still not discriminating"


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
