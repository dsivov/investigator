"""Stage 2 (dedup) tests — TRIANGULATION_REVIEW.md §2.

Encodes the confirmed merge policy (M1 no-drop / max relevance, M2 prefer-best
for single-valued, M3 distinct union for multi-valued) + the F3/F4 fixes, plus
a golden regression (re-run dedup on the captured NER input).

Imports tangraph.graph.dedup (loads WordLlama at import); run with the tangos env:
    PYTHONPATH=.:src:/home/dsivov/Work/tangos_mvp <tangos-python> tests/test_stage2_dedup.py
"""

from __future__ import annotations

import copy
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tangraph.graph.dedup import _is_empty_value, merge_data_fields, merge_duplicate_group  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_stages.json.gz"


# --- merge_data_fields (M1 / F3 / F4) -----------------------------------


def test_M1_no_relevance_drop():
    # all sources <0.5 must NOT be dropped (was: returned {})
    out = merge_data_fields([{"name": "A", "relevance_score": 0.1},
                               {"location": "X", "relevance_score": 0.1}])
    assert out.get("name") and out.get("location") == "X", out


def test_M1_relevance_is_maxed():
    out = merge_data_fields([{"relevance_score": 0.1}, {"relevance_score": 0.8}, {"relevance_score": 0.3}])
    assert out["relevance_score"] == 0.8


def test_F4_edge_attributes_merge_not_emptied():
    # attribute dicts have no relevance_score; must still merge (was: {})
    assert merge_data_fields([{"a": 1}, {"b": 2}]) == {"a": 1, "b": 2}


# --- merge_duplicate_group (M1 / M2 / M3) ----------------------


class _Dup:
    def __init__(self, data):
        self.record = {"identifier": "X", "data": data}
        self.duplicates = []

    def add(self, data):
        self.duplicates.append(({"data": data},))
        return self


def test_M3_multi_valued_fields_unioned_as_list():
    d = _Dup({"name": "Acme", "type": "ORG", "address": "123 A St", "relevance_score": 0.1})
    d.add({"name": "Acme Inc", "type": "ORG", "address": "456 B Ave", "relevance_score": 0.8})
    merged = merge_duplicate_group(d)
    assert sorted(merged["address"]) == ["123 A St", "456 B Ave"]   # distinct union, not joined string
    assert isinstance(merged["address"], list)


def test_M2_single_valued_type_prefers_one_value():
    d = _Dup({"type": "ORG", "relevance_score": 0.1})
    d.add({"type": "ORG", "relevance_score": 0.8})
    merged = merge_duplicate_group(d)
    assert merged["type"] == "ORG"          # scalar, not a list


def test_M1_relevance_max_and_no_drop_in_merge_duplicates():
    d = _Dup({"name": "A", "address": "only-here", "relevance_score": 0.1})
    d.add({"name": "A", "relevance_score": 0.8})
    merged = merge_duplicate_group(d)
    assert merged["relevance_score"] == 0.8
    assert merged["address"] == "only-here"   # low-relevance source's unique datum survives


def test_no_comma_joined_scalar_fields():
    d = _Dup({"location": "Texas", "relevance_score": 0.3})
    d.add({"location": "Gaza", "relevance_score": 0.3})
    merged = merge_duplicate_group(d)
    assert merged["location"] == ["Texas", "Gaza"]   # list, not "Texas,Gaza"


def test_llm_filler_values_are_dropped():
    # the LLM-armed-search layer emits filler for absent attributes; verbose
    # variants must not leak into the merged union
    for filler in ("Not specified in provided data", "Not available in the source",
                   "N/A", "unknown", "", None):
        assert _is_empty_value(filler), filler
    for real in ("Texas, USA", "(972) 257-2564", "Hamas"):
        assert not _is_empty_value(real), real
    d = _Dup({"location": "Not specified in provided data", "relevance_score": 0.3})
    d.add({"location": "Dallas", "relevance_score": 0.3})
    assert merge_duplicate_group(d)["location"] == "Dallas"   # filler dropped, real kept


# --- golden regression -----------------------------------------------------


def _run_golden_regression() -> None:
    try:
        from model2vec import StaticModel

        from tangraph.graph.dedup import deduplicate_entities
        sem = StaticModel.from_pretrained("minishlab/potion-multilingual-128M")
    except Exception as e:  # noqa: BLE001
        print(f"SKIP golden regression (models unavailable: {type(e).__name__})")
        return
    if not GOLDEN.exists():
        return  # golden fixture not shipped in the public repo
    g = json.load(gzip.open(GOLDEN))
    out, _i, _r = deduplicate_entities(
        copy.deepcopy(g["ner_nodes"]), g["representatives"], semhash_model=sem
    )
    assert len(out) == len(g["dedup_nodes"]), f"grouping drifted: {len(out)} vs {len(g['dedup_nodes'])}"
    assert all(n.get("data") for n in out), "a node lost all data (M1 violation)"
    assert all(isinstance(n["data"].get("relevance_score"), (int, float)) for n in out), "relevance not numeric"
    print(f"PASS golden regression ({len(out)} nodes, no empty data, relevance numeric)")


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
    _run_golden_regression()
    print(f"\n{len(tests) - failures}/{len(tests)} merge-policy tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
