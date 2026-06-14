"""Unit tests for EntityRecord (DATA_MODEL migration step 3).

Runs under pytest *or* standalone (the tangos conda env has no pytest):

    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_records.py

The load-bearing property is round-trip fidelity: from_dict(d).to_dict() == d
for the entity dicts the pipeline actually produces — that is what makes step 4
wiring behaviour-preserving.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.state.records import EdgeRecord, EntityRecord  # noqa: E402


class FakeModel:
    """Duck-typed stand-in for a dspy models.Entity output."""

    def __init__(self, name, search_url="", search_source="", **data):
        self.name = name
        self.search_url = search_url
        self.search_source = search_source
        self._data = {"name": name, "search_url": search_url, "search_source": search_source, **data}

    def model_dump(self):
        return dict(self._data)


def _minimal_extracted() -> dict:
    # exactly what extract_entities_from_chunk produces before dedup
    return {
        "identifier": "ACME CORP",
        "unique_identifier": "uuid-1",
        "type": "entity",
        "data": {"name": "Acme Corp", "relevance_score": 0.9, "relevant_entities": ["ACME CORP"]},
        "chunk_uuid": "chunk-1",
        "source": "http://example.com",
    }


def _full_consolidated() -> dict:
    # what a node looks like after dedup + graph build + consolidation
    return {
        "identifier": "ACME CORP",
        "unique_identifier": "uuid-1",
        "type": "ENTITY",
        "data": {"name": "Acme Corp", "relevance_score": 0.9, "relations": []},
        "chunk_uuid": "chunk-1",
        "source": "http://example.com",
        "representative_identifier": "ACME CORP",
        "labels": ["ACME CORP", "ACME"],
        "most_significant_labels": [["ACME CORP", 2]],
        "triangulated": True,
        "hypothesis": True,
        "leaf": True,
        "prob": 0.83,
        "evidence": [{"identifier": "ACME CORP_src_evidence", "related_node": "ACME CORP", "hypothesis": True}],
        "evidence_count": 1,
        "self_evidence": {"reasoning": "..."},
    }


# --- round-trip fidelity --------------------------------------------------


def test_round_trip_minimal():
    d = _minimal_extracted()
    assert EntityRecord.from_dict(d).to_dict() == d


def test_round_trip_full():
    d = _full_consolidated()
    assert EntityRecord.from_dict(d).to_dict() == d


def test_absent_optionals_are_omitted():
    d = _minimal_extracted()
    out = EntityRecord.from_dict(d).to_dict()
    for absent in ("representative_identifier", "leaf", "evidence", "prob", "labels"):
        assert absent not in out


def test_falsy_optionals_preserved_not_dropped():
    # False / 0.0 / "" are real values, distinct from absent (None) — must survive
    d = {
        "identifier": "X",
        "unique_identifier": "u",
        "type": "entity",
        "data": {},
        "leaf": False,
        "triangulated": False,
        "prob": 0.0,
        "representative_identifier": "",
        "evidence_count": 0,
    }
    assert EntityRecord.from_dict(d).to_dict() == d


def test_extra_keys_preserved():
    d = _minimal_extracted()
    d["graph_identifier"] = "ACME CORP"   # dead field — preserved until step 8 deletes it
    d["some_future_key"] = {"k": "v"}
    rec = EntityRecord.from_dict(d)
    assert rec.extra == {"graph_identifier": "ACME CORP", "some_future_key": {"k": "v"}}
    assert rec.to_dict() == d


# --- canonical_id ---------------------------------------------------------


def test_canonical_id_prefers_representative():
    rec = EntityRecord(identifier="acme", representative_identifier="acme corp")
    assert rec.canonical_id == "ACME CORP"


def test_canonical_id_falls_back_to_identifier():
    assert EntityRecord(identifier="acme").canonical_id == "ACME"
    assert EntityRecord(identifier="acme", representative_identifier="").canonical_id == "ACME"
    assert EntityRecord(identifier="acme", representative_identifier=None).canonical_id == "ACME"


def test_canonical_id_empty_when_no_ids():
    assert EntityRecord().canonical_id == ""


# --- from_extraction ------------------------------------------------------


def test_from_extraction_core_fields():
    model = FakeModel("Acme Corp", search_url="http://acme.test", relevance_score=0.7)
    rec = EntityRecord.from_extraction(model, chunk_id="chunk-9")
    assert rec.identifier == "ACME CORP"      # name upper-cased
    assert rec.type == "entity"
    assert rec.chunk_uuid == "chunk-9"
    assert rec.source == "http://acme.test"   # search_url wins
    assert rec.data["relevance_score"] == 0.7
    assert rec.unique_identifier                # a uuid was assigned


def test_from_extraction_source_resolution_order():
    # search_source used when no search_url
    r = EntityRecord.from_extraction(FakeModel("A", search_source="src-A"), "c")
    assert r.source == "src-A"
    # all empty but the keys are PRESENT in model_dump -> "" (faithfully mirrors the
    # original: .get("search_source", "unknown") returns "" when the key exists empty,
    # which is what a real pydantic models.Entity always produces)
    r2 = EntityRecord.from_extraction(FakeModel("B"), "c")
    assert r2.source == ""


def test_from_extraction_unknown_only_when_blob_lacks_source_key():
    class NoSourceKeyModel:
        name = "Z"
        search_url = ""
        search_source = ""

        def model_dump(self):
            return {"name": "Z"}   # no search_url / search_source keys at all

    assert EntityRecord.from_extraction(NoSourceKeyModel(), "c").source == "unknown"


# --- EdgeRecord -----------------------------------------------------------


def _registered_edge() -> dict:
    # the shape resolve_edge_endpoints produces (and what gets persisted)
    return {
        "unique_identifier": "edge-uuid-1",
        "src_identifier": "ACME CORP",
        "dst_identifier": "BOB",
        "src_unique_identifier": "uid-acme",
        "dst_unique_identifier": "uid-bob",
        "type": "affiliation",
        "relations": '{"name": "BOB", "type": "affiliation", "context": "ctx"}',
        "attributes": {"k": "v"},
        "source": "http://example.com",
    }


def test_edge_round_trip_registered():
    d = _registered_edge()
    assert EdgeRecord.from_dict(d).to_dict() == d


def test_edge_round_trip_with_metadata_and_extra():
    d = _registered_edge()
    d["metadata"] = {"src": "doc1"}
    d["source_node"] = "LEFTOVER"   # transient, not modelled -> must survive via extra
    rec = EdgeRecord.from_dict(d)
    assert rec.extra == {"source_node": "LEFTOVER"}
    assert rec.to_dict() == d


def test_edge_absent_fields_omitted():
    rec = EdgeRecord(unique_identifier="u", src_identifier="A", dst_identifier="B")
    out = rec.to_dict()
    assert out == {"unique_identifier": "u", "src_identifier": "A", "dst_identifier": "B"}
    assert "metadata" not in out and "attributes" not in out


def test_edge_falsy_attributes_preserved():
    d = {"unique_identifier": "u", "attributes": {}, "metadata": {}}
    assert EdgeRecord.from_dict(d).to_dict() == d


# --- runs field (cross-run provenance) -------------------------------------

def test_entity_runs_defaults_absent():
    r = EntityRecord.from_dict({"identifier": "X", "unique_identifier": "u", "type": "entity", "data": {}})
    assert r.runs is None
    assert "runs" not in r.to_dict()


def test_entity_runs_round_trips():
    d = {"identifier": "HAMAS", "unique_identifier": "u", "type": "entity", "data": {},
         "runs": ["israeli_strike_haddad", "gaza_flotilla_sanctions"]}
    assert EntityRecord.from_dict(d).to_dict() == d


def test_edge_runs_defaults_absent():
    r = EdgeRecord.from_dict({"unique_identifier": "e"})
    assert r.runs is None
    assert "runs" not in r.to_dict()


def test_edge_runs_round_trips():
    d = {"unique_identifier": "e", "src_identifier": "A", "dst_identifier": "B",
         "runs": ["evA", "evB"]}
    assert EdgeRecord.from_dict(d).to_dict() == d


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
