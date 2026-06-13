"""Unit tests for InvestigationState + canonical_id (DATA_MODEL migration step 1).

Runs under pytest *or* standalone (the tangos conda env has no pytest):

    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_state.py

Only imports tangraph.state (no dspy / embeddings), so it is fast and isolated.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tangraph.state.ids import canonical_id  # noqa: E402
from tangraph.state.investigation import InvestigationState  # noqa: E402
from tangraph.state.records import EntityRecord  # noqa: E402


class FakeRepo:
    """In-memory stand-in for InvestigationStateRepo (same find/add/update API)."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def find(self, session_id):
        return self._rows.get(session_id)

    def add(self, record):
        self._rows[record["session_id"]] = dict(record)

    def update(self, session_id, fields):
        self._rows[session_id].update(fields)

    def get_field(self, session_id, field, default=None):
        row = self._rows.get(session_id)
        return default if row is None else row.get(field, default)


# --- canonical_id ---------------------------------------------------------


def test_canonical_id_prefers_representative():
    node = {"identifier": "ACME", "representative_identifier": "ACME CORP"}
    assert canonical_id(node) == "ACME CORP"


def test_canonical_id_falls_back_to_identifier():
    assert canonical_id({"identifier": "acme"}) == "ACME"
    assert canonical_id({"identifier": "acme", "representative_identifier": ""}) == "ACME"
    assert canonical_id({"identifier": "acme", "representative_identifier": None}) == "ACME"


def test_canonical_id_empty_when_no_ids():
    assert canonical_id({}) == ""
    assert canonical_id({"identifier": ""}) == ""


# --- index ----------------------------------------------------------------


def test_reindex_on_construction():
    s = InvestigationState(
        session_id="s1",
        nodes=[
            EntityRecord(identifier="A"),
            EntityRecord(identifier="B", representative_identifier="BEE"),
        ],
    )
    assert s.node("A").identifier == "A"
    assert s.node("BEE").identifier == "B"
    # lookup is case-insensitive and indexed by canonical (representative) id
    assert s.node("bee").identifier == "B"
    assert s.node("B") is None  # "B" is shadowed by its representative id
    assert s.node("missing") is None
    assert s.node(None) is None


def test_add_nodes_updates_index():
    s = InvestigationState(session_id="s1")
    assert s.node("A") is None
    s.add_nodes([EntityRecord(identifier="A"), EntityRecord(identifier="C", representative_identifier="CEE")])
    assert len(s.nodes) == 2
    assert s.node("A").identifier == "A"
    assert s.node("CEE").identifier == "C"


def test_reindex_last_duplicate_wins():
    first = EntityRecord(identifier="A", data={"tag": 1})
    second = EntityRecord(identifier="A", data={"tag": 2})
    s = InvestigationState(session_id="s1", nodes=[first, second])
    assert s.node("A").data["tag"] == 2


def test_node_without_id_is_skipped_from_index():
    s = InvestigationState(session_id="s1", nodes=[EntityRecord(data={"x": "no id"})])
    assert len(s.nodes) == 1
    assert s._index == {}


# --- persistence ----------------------------------------------------------


def test_load_fresh_when_absent():
    repo = FakeRepo()
    s = InvestigationState.load(repo, "new-session")
    assert s.session_id == "new-session"
    assert s.nodes == [] and s.edges == [] and s.runs_number == 0


def test_load_existing_record():
    repo = FakeRepo()
    repo.add(
        {
            "session_id": "s1",
            "nodes": [{"identifier": "A"}],
            "edges": [{"src_identifier": "A", "dst_identifier": "B"}],
            "representative_identifiers": [{"identifier": "A", "relevant_identifiers": ["A"]}],
            "dirty_node_names": [["A"]],
            "runs_number": 3,
        }
    )
    s = InvestigationState.load(repo, "s1")
    assert s.runs_number == 3
    assert s.node("A").identifier == "A"   # persisted dict -> EntityRecord on load
    assert len(s.edges) == 1
    assert s.dirty_node_names == [["A"]]


def test_load_coerces_non_int_runs_number():
    repo = FakeRepo()
    repo.add({"session_id": "s1", "runs_number": "oops"})
    assert InvestigationState.load(repo, "s1").runs_number == 0


def test_save_inserts_then_patches():
    repo = FakeRepo()
    s = InvestigationState(session_id="s1", nodes=[EntityRecord(identifier="A")], runs_number=1)
    s.save(repo)
    assert repo.find("s1")["runs_number"] == 1
    assert len(repo.find("s1")["nodes"]) == 1
    assert repo.find("s1")["nodes"][0]["identifier"] == "A"   # persisted as a dict
    # mutate + save again -> patch, not duplicate insert
    s.runs_number = 2
    s.add_nodes([EntityRecord(identifier="B")])
    s.save(repo)
    assert repo.find("s1")["runs_number"] == 2
    assert len(repo.find("s1")["nodes"]) == 2


def test_save_does_not_persist_working_fields():
    repo = FakeRepo()
    s = InvestigationState(session_id="s1", chunks=[{"uuid": "c1"}])
    s.save(repo)
    record = repo.find("s1")
    assert "chunks" not in record   # per-request working data, never persisted


def test_round_trip_load_save_load():
    repo = FakeRepo()
    s = InvestigationState(session_id="s1", nodes=[EntityRecord(identifier="A")], runs_number=5)
    s.save(repo)
    reloaded = InvestigationState.load(repo, "s1")
    assert reloaded.runs_number == 5
    assert reloaded.node("A").identifier == "A"


def test_query_subject_persist_and_round_trip():
    repo = FakeRepo()
    s = InvestigationState(
        session_id="s1",
        investigation_query="Exampleorg",
        investigation_subject="terror financing?",
    )
    s.save(repo)
    record = repo.find("s1")
    assert record["investigation_query"] == "Exampleorg"
    assert record["investigation_subject"] == "terror financing?"
    reloaded = InvestigationState.load(repo, "s1")
    assert reloaded.investigation_query == "Exampleorg"
    assert reloaded.investigation_subject == "terror financing?"


def test_query_subject_default_none_when_absent():
    repo = FakeRepo()
    repo.add({"session_id": "s1", "nodes": []})
    s = InvestigationState.load(repo, "s1")
    assert s.investigation_query is None
    assert s.investigation_subject is None


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
