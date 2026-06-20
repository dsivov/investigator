"""Claim-level corroboration tests.

The point of claim-level (vs. entity-source-count): N sources only corroborate
when they attest the SAME claim, independently. So:
  * identical text from many sources = syndication -> 1 independent source,
  * unrelated claims from many sources -> no single fact corroborated -> weak,
  * the same fact reported (differently) by distinct sources -> strong.

Thresholds are passed explicitly so outcomes don't hinge on the default tuning:
we only rely on WordLlama giving word-overlapping text high similarity and
unrelated text low similarity.

Standalone:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_claim_corroboration.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.graph.corroboration import claim_corroboration, corroborate  # noqa: E402


def _ev(text, source, strength=0.8, confidence=0.9, hypothesis=True):
    return {"reasoning": text, "doc_id": source, "strength": strength,
            "confidence": confidence, "hypothesis": hypothesis}


def test_no_credible_evidence_is_weak_zero():
    r = claim_corroboration([])
    assert r == {"tier": "weak", "sources": 0, "claim": "", "corroborated_claims": 0}
    # zero strength / zero confidence don't count
    assert claim_corroboration([_ev("x", "a", strength=0.0)])["sources"] == 0


def test_single_source_is_weak():
    r = claim_corroboration([_ev("Netanyahu accepted gifts from Milchan", "a")])
    assert r["tier"] == "weak" and r["sources"] == 1
    assert "Milchan" in r["claim"]


def test_identical_text_across_sources_is_syndication_not_corroboration():
    # Same wire story republished by 3 outlets -> 1 independent source -> weak.
    claim = "Netanyahu accepted luxury gifts from Arnon Milchan in exchange for favours"
    r = claim_corroboration(
        [_ev(claim, "outletA"), _ev(claim, "outletB"), _ev(claim, "outletC")],
        claim_sim=0.5, syndication_sim=0.99,
    )
    assert r["sources"] == 1, r
    assert r["tier"] == "weak"


def test_distinct_claims_from_many_sources_not_corroborated():
    # 3 sources, 3 unrelated facts -> no single claim has >1 source -> weak.
    r = claim_corroboration([
        _ev("Netanyahu corruption trial resumes in Jerusalem", "a"),
        _ev("Boeing 737 MAX production cap enforced by the FAA", "b"),
        _ev("Iran expands uranium enrichment at Natanz", "c"),
    ], claim_sim=0.5, syndication_sim=0.99)
    assert r["sources"] == 1, r
    assert r["tier"] == "weak"
    assert r["corroborated_claims"] == 0


def test_same_fact_distinct_sources_is_strong():
    # Same fact, different wording, 3 independent outlets -> strong.
    r = claim_corroboration([
        _ev("Netanyahu accepted expensive gifts from Arnon Milchan", "a"),
        _ev("Netanyahu received costly presents from Milchan", "b"),
        _ev("Netanyahu took luxury goods from businessman Arnon Milchan", "c"),
    ], claim_sim=0.5, syndication_sim=0.99)
    assert r["sources"] == 3, r
    assert r["tier"] == "strong"
    assert r["corroborated_claims"] >= 1


def test_two_independent_sources_is_moderate():
    r = claim_corroboration([
        _ev("Netanyahu accepted expensive gifts from Arnon Milchan", "a"),
        _ev("Netanyahu received costly presents from Milchan", "b"),
    ], claim_sim=0.5, syndication_sim=0.99)
    assert r["sources"] == 2, r
    assert r["tier"] == "moderate"


def test_contradicting_evidence_uses_dominant_side():
    # Net supports (2 strong supports vs 1 weak contradiction); corroboration is
    # measured on the supporting side.
    r = claim_corroboration([
        _ev("Netanyahu accepted gifts from Milchan", "a", strength=0.9),
        _ev("Netanyahu received presents from Milchan", "b", strength=0.9),
        _ev("Netanyahu denies wrongdoing in the gifts affair", "c", strength=0.3, hypothesis=False),
    ], claim_sim=0.5, syndication_sim=0.99)
    assert r["sources"] == 2, r
    assert r["tier"] == "moderate"


# --- per-evidence corroboration (corroborate.items) -------------------------

def test_per_evidence_items_aligned_and_tiered():
    evs = [
        _ev("Netanyahu accepted expensive gifts from Arnon Milchan", "a"),   # claim A
        _ev("Netanyahu received costly presents from Milchan", "b"),         # claim A
        _ev("Netanyahu took luxury goods from businessman Arnon Milchan", "c"),  # claim A
        _ev("Netanyahu launched a plan to overhaul the judiciary", "a"),     # claim B (lone)
    ]
    r = corroborate(evs, claim_sim=0.5, syndication_sim=0.99)
    items = r["items"]
    assert len(items) == len(evs)               # aligned by index
    # the 3 gift-claim items are corroborated by 3 independent sources
    assert all(items[i]["tier"] == "strong" and items[i]["sources"] == 3 for i in (0, 1, 2)), items
    # the lone judiciary claim is weak (only 1 source attests it)
    assert items[3]["tier"] == "weak" and items[3]["sources"] == 1, items[3]
    # node summary picks the best claim
    assert r["node"]["sources"] == 3 and r["node"]["tier"] == "strong"


def test_per_evidence_syndication_marks_items_weak():
    claim = "Netanyahu accepted luxury gifts from Arnon Milchan in exchange for favours"
    r = corroborate([_ev(claim, "outletA"), _ev(claim, "outletB")],
                    claim_sim=0.5, syndication_sim=0.99)
    assert [it["tier"] for it in r["items"]] == ["weak", "weak"]
    assert all(it["sources"] == 1 for it in r["items"])


def test_per_evidence_ineligible_items_are_weak_zero():
    # index 1 has no source -> not eligible -> weak/0, but indices stay aligned
    evs = [_ev("Netanyahu accepted gifts from Milchan", "a"),
           {"reasoning": "no source", "strength": 0.8, "confidence": 0.9, "hypothesis": True}]
    r = corroborate(evs, claim_sim=0.5, syndication_sim=0.99)
    assert len(r["items"]) == 2
    assert r["items"][1] == {"tier": "weak", "sources": 0}


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
