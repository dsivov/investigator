"""Unit tests for post-hoc canonical validation.

When NER or MRI admits a headline-shaped string as an entity identifier
(e.g. "BOEING FORCED TO DISASSEMBLE COMPLETED 737 MAX AIRCRAFT..."),
this validator rewrites the record's identifier to the shortest valid
label on the same record. The old identifier is preserved as a label.

Standalone runner:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_canonical_validation.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.graph.dedup import (  # noqa: E402
    _is_valid_canonical,
    validate_entity_canonicals,
)


# --- _is_valid_canonical predicate -----------------------------------------

def test_plain_org_name_is_valid():
    assert _is_valid_canonical("HAMAS")
    assert _is_valid_canonical("US TREASURY")
    assert _is_valid_canonical("Council on American-Islamic Relations")
    assert _is_valid_canonical("HELPING HAND FOR RELIEF AND DEVELOPMENT")


def test_plain_person_name_is_valid():
    assert _is_valid_canonical("Mohammed bin Salman")
    assert _is_valid_canonical("IZZ AL-DIN AL-HADDAD")


def test_gpe_and_loc_names_are_valid():
    assert _is_valid_canonical("United States")
    assert _is_valid_canonical("Strait of Hormuz")
    assert _is_valid_canonical("Gaza")


def test_headline_with_verb_is_rejected():
    assert not _is_valid_canonical(
        "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD"
    )
    assert not _is_valid_canonical(
        "FAA APPROVES BOEING 737 MAX PRODUCTION INCREASE"
    )
    assert not _is_valid_canonical(
        "US TREASURY SANCTIONS HEZBOLLAH FINANCIERS"
    )


def test_too_long_is_rejected_even_without_verb():
    # Over the word cap even without any explicit verb
    assert not _is_valid_canonical(
        "AMERICAN-ISLAMIC FOUNDATION FOR PALESTINIAN CHILDREN'S WELFARE AND ANNUAL EDUCATIONAL SUPPORT GROUP"
    )


def test_empty_and_invalid_inputs_rejected():
    assert not _is_valid_canonical("")
    assert not _is_valid_canonical("   ")
    assert not _is_valid_canonical(None)
    assert not _is_valid_canonical(["a", "b"])
    assert not _is_valid_canonical(123)


def test_short_string_with_no_verb_is_valid():
    assert _is_valid_canonical("FAA")
    assert _is_valid_canonical("X")


# --- validate_entity_canonicals end-to-end ---------------------------------

def _entity(identifier, labels=None, type_="entity"):
    return {
        "identifier": identifier,
        "representative_identifier": identifier,
        "type": type_,
        "labels": labels or [],
    }


def test_no_op_when_canonical_is_already_valid():
    e = _entity("HAMAS", labels=["Hamas", "the Hamas leadership"])
    n_fixed = validate_entity_canonicals([e])
    assert n_fixed == 0
    assert e["identifier"] == "HAMAS"


def test_swaps_headline_canonical_with_shortest_valid_label():
    e = _entity(
        "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD",
        labels=["Hamas", "Izz al-Din al-Haddad", "Israel"],
    )
    n_fixed = validate_entity_canonicals([e])
    assert n_fixed == 1
    # Shortest valid label is "Israel" (6 chars) vs "Hamas" (5 chars)
    # Actually "Hamas" (5) is shorter than "Israel" (6); pick that.
    assert e["identifier"] == "HAMAS"
    assert e["representative_identifier"] == "HAMAS"
    # Old (headline) identifier moved to labels
    assert "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD" in e["labels"]
    # The chosen canonical is no longer in labels
    assert "Hamas" not in e["labels"]


def test_no_swap_when_no_valid_alternative():
    e = _entity(
        "ISRAELI STRIKE KILLS HAMAS LEADER",
        labels=["FAA ENFORCES PRODUCTION CAP", "BOEING FORCED TO DISASSEMBLE COMPLETED AIRCRAFT"],
    )
    n_fixed = validate_entity_canonicals([e])
    # All labels are also headline-shaped, so no swap
    assert n_fixed == 0
    assert e["identifier"] == "ISRAELI STRIKE KILLS HAMAS LEADER"


def test_events_are_never_rewritten():
    e = _entity(
        "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD",
        labels=["Israel", "Hamas"],
        type_="event",
    )
    n_fixed = validate_entity_canonicals([e])
    assert n_fixed == 0
    assert e["identifier"] == "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD"


def test_mixed_records_each_handled_independently():
    e_ok = _entity("HAMAS", labels=["the Hamas leadership"])
    e_fix = _entity(
        "BOEING FORCED TO DISASSEMBLE COMPLETED 737 MAX AIRCRAFT",
        labels=["Boeing", "Boeing Co.", "FAA"],
    )
    e_event = _entity(
        "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD",
        labels=["Israel", "Hamas"], type_="event",
    )
    n_fixed = validate_entity_canonicals([e_ok, e_fix, e_event])
    assert n_fixed == 1
    assert e_ok["identifier"] == "HAMAS"
    assert e_fix["identifier"] == "FAA"   # shortest valid (3 chars)
    assert e_event["identifier"] == "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD"


def test_swap_updates_labels_correctly():
    e = _entity(
        "FAA APPROVES BOEING 737 MAX PRODUCTION INCREASE",
        labels=["Boeing", "FAA", "Boeing Co."],
    )
    validate_entity_canonicals([e])
    # FAA (3 chars) wins as shortest valid label
    assert e["identifier"] == "FAA"
    # Old headline became a label
    assert "FAA APPROVES BOEING 737 MAX PRODUCTION INCREASE" in e["labels"]
    # FAA is no longer in labels (it's the canonical now)
    assert "FAA" not in e["labels"]
    # The other valid labels remain
    assert "Boeing" in e["labels"]
    assert "Boeing Co." in e["labels"]


def _run_standalone() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            fails += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
