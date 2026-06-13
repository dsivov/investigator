"""Stage 1 (NER) tests — TRIANGULATION_REVIEW.md §1.

Two parts:
  * golden invariant checks (I1-I3, I6) frozen against the captured NER output —
    fast, no heavy imports;
  * deterministic-assembly check of extract_entities_from_chunk with the LLM
    (`get_entities`) mocked — needs the orchestrator import (heavy: loads
    embedding models), so it is skipped if that import fails.

    PYTHONPATH=.:src:/home/dsivov/Work/tangos_mvp <tangos-python> tests/test_stage1_ner.py
"""

from __future__ import annotations

import asyncio
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_stages.json.gz"


def _golden():
    if not GOLDEN.exists():
        return None
    return json.load(gzip.open(GOLDEN))


# --- golden invariant checks (frozen NER contract) ------------------------


def test_I1_identifiers_clean():
    nodes = _golden()["ner_nodes"]
    assert nodes, "golden has no NER nodes"
    for n in nodes:
        ident = n.get("identifier")
        assert ident and ident not in ("", "None", "NONE", "N/A"), f"bad identifier {ident!r}"
        assert ident == ident.upper(), f"identifier not upper-cased: {ident!r}"


def test_I2_type_is_person_or_org():
    nodes = _golden()["ner_nodes"]
    for n in nodes:
        t = (n["data"].get("type") or "").upper()
        assert t in {"PERSON", "ORG"}, f"unexpected entity type {t!r} for {n['identifier']}"


def test_I3_relevance_score_in_range():
    nodes = _golden()["ner_nodes"]
    for n in nodes:
        rs = n["data"].get("relevance_score")
        assert isinstance(rs, (int, float)) and 0.0 <= rs <= 1.0, f"relevance out of range: {rs}"


def test_I6_affiliation_endpoints_mostly_grounded():
    g = _golden()
    ids = {n["identifier"].upper() for n in g["ner_nodes"]}
    eps = [
        e.upper()
        for ch in g["chunks"]
        for a in ch.get("affiliations", [])
        for e in (a.get("entityA", ""), a.get("entityB", ""))
        if e
    ]
    grounded = sum(1 for e in eps if e in ids)
    # recall-first NER: nearly all affiliation endpoints should be extracted
    # entities; a small tail of phantom endpoints is tolerated (see F2).
    assert eps and grounded / len(eps) >= 0.95, f"only {grounded}/{len(eps)} endpoints grounded"


def test_I8_ner_keeps_all_no_relevance_filter():
    # NER must not drop on relevance; the golden keeps low-relevance entities.
    nodes = _golden()["ner_nodes"]
    below = sum(1 for n in nodes if (n["data"].get("relevance_score") or 0) < 0.5)
    assert below > 0, "expected NER to retain sub-0.5 entities (recall-first)"


# --- deterministic assembly check (LLM mocked) ----------------------------


class _FakeEntity:
    def __init__(self, name, search_url="", search_source="", relevance_score=0.5):
        self.name = name
        self.search_url = search_url
        self.search_source = search_source
        self._d = {
            "name": name,
            "type": "ORG",
            "relevance_score": relevance_score,
            "search_url": search_url,
            "search_source": search_source,
        }

    def model_dump(self):
        return dict(self._d)


def _run_assembly_check() -> None:
    try:
        import tangraph.pipeline.orchestrator as orch
    except Exception as e:  # noqa: BLE001
        print(f"SKIP assembly check (orchestrator import failed: {type(e).__name__})")
        return

    async def fake_get_entities(input_data, investigation_query=""):
        return ([_FakeEntity("Acme Corp", search_url="http://acme.test"),
                 _FakeEntity("", search_source="x"),          # empty name -> filtered
                 _FakeEntity("Bob", search_source="src-bob")], [])

    orig = orch.get_entities
    orch.get_entities = fake_get_entities
    try:
        local: dict = {}
        groups, all_dicts, affs = asyncio.run(
            orch.extract_entities_from_chunk('{"text":"x"}', {"query": "q"}, local, task_id=0)
        )
    finally:
        orch.get_entities = orig

    names = {d["identifier"] for d in all_dicts}
    assert names == {"ACME CORP", "BOB"}, f"empty name not filtered / wrong set: {names}"
    acme = next(d for d in all_dicts if d["identifier"] == "ACME CORP")
    assert acme["type"] == "entity" and acme["unique_identifier"] and acme["chunk_uuid"]
    assert acme["source"] == "http://acme.test"          # search_url wins
    bob = next(d for d in all_dicts if d["identifier"] == "BOB")
    assert bob["source"] == "src-bob"                    # falls back to search_source
    assert local.get("nodes") and len(local["nodes"]) == 2
    assert local.get("chunks") and local["chunks"][0]["entities"]
    assert local.get("dirty_node_names")
    print("PASS assembly check (extract_entities_from_chunk, LLM mocked)")


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
    _run_assembly_check()
    print(f"\n{len(tests) - failures}/{len(tests)} golden-invariant tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
