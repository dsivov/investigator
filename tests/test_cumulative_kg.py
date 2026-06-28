"""Tests for the in-code cumulative KG + cross-investigation canonicalizer.

Two angles, both offline (local WordLlama embeddings + stub LLM, file storage):

  * CanonicalRegistry: exact / normalized variants auto-merge; subset/fuzzy
    matches are flagged for review but registered as their OWN canonical (the
    conservative policy -- cross-investigation fusions are sticky).
  * CumulativeKG.merge_graph: two investigation graphs that share entities
    (one exact, one punctuation variant) end up as single KG nodes whose
    source_id unions BOTH investigations -- proof of merge, not clobber.

Standalone runner:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_cumulative_kg.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.analytics.canonicalizer import CanonicalRegistry  # noqa: E402


def _registry(tmp: Path) -> CanonicalRegistry:
    return CanonicalRegistry(tmp / "reg.json", review_path=tmp / "review.jsonl")


def _reset_shared_storage() -> None:
    """LightRAG's shared_storage is process-global and its locks bind to the
    first background loop. Production uses ONE CumulativeKG for the process
    lifetime, but these tests build several (each with its own bg loop), so reset
    the globals between them to avoid 'bound to a different event loop'."""
    try:
        from lightrag.kg.shared_storage import finalize_share_data
        finalize_share_data()
    except Exception:  # noqa: BLE001 -- nothing to reset on first use
        pass


# --- CanonicalRegistry tiers -----------------------------------------------

def test_exact_case_insensitive_merges():
    tmp = Path(tempfile.mkdtemp())
    r = _registry(tmp)
    base = r.resolve("ACME CORP", "ORG", "inv::a")
    assert r.resolve("acme corp", "ORG", "inv::b") == base
    assert r.stats["exact"] == 1


def test_normalized_punctuation_and_whitespace_merge():
    tmp = Path(tempfile.mkdtemp())
    r = _registry(tmp)
    r.resolve("SEO HEE CONSTRUCTION", "ORG", "inv::a")
    assert r.resolve("SEOHEE CONSTRUCTION", "ORG", "inv::b") == "SEO HEE CONSTRUCTION"
    assert r.resolve("U.S.", "GPE", "inv::a") == r.resolve("US", "GPE", "inv::b")
    assert r.stats["normalized"] >= 2


def test_subset_is_flagged_not_merged():
    tmp = Path(tempfile.mkdtemp())
    r = _registry(tmp)
    base = r.resolve("ACME FOUNDATION OF AMERICA", "ORG", "inv::a")
    # Structural superset -> deterministically a review candidate, NOT a merge.
    other = r.resolve("THE ACME FOUNDATION OF AMERICA", "ORG", "inv::b")
    assert other != base
    assert r.stats["review"] >= 1
    assert (tmp / "review.jsonl").exists()


def test_unrelated_names_register_separately():
    tmp = Path(tempfile.mkdtemp())
    r = _registry(tmp)
    a = r.resolve("TEHRAN", "GPE", "inv::a")
    b = r.resolve("WASHINGTON", "GPE", "inv::b")
    assert a != b
    assert r.stats["new"] == 2


def test_save_and_reload_roundtrip():
    tmp = Path(tempfile.mkdtemp())
    r = _registry(tmp)
    r.resolve("ACME CORP", "ORG", "inv::a")
    r.resolve("acme corp", "ORG", "inv::b")  # alias
    r.save()
    r2 = _registry(tmp)
    # Reloaded registry still recognizes the alias as the same canonical.
    assert r2.resolve("ACME CORP.", "ORG", "inv::c") == "ACME CORP"


# --- CumulativeKG end-to-end merge -----------------------------------------

def _graph(nodes, edges):
    return {
        "nodes": [
            {"identifier": ident, "type": "entity", "data": {"type": t}}
            for ident, t in nodes
        ],
        "edges": [
            {"src_identifier": s, "dst_identifier": d, "type": "relationship",
             "weight": 1.0, "relations": {"type": rel, "context": ctx}}
            for s, d, rel, ctx in edges
        ],
    }


async def _merge_two() -> dict:
    from investigator.analytics.cumulative_kg import CumulativeKG

    work = Path(tempfile.mkdtemp()) / "kg"
    kg = CumulativeKG(work)  # init is lazy, on the background loop

    # Investigation A
    ga = _graph(
        nodes=[("JOHN SMITH", "PERSON"), ("ACME CORP", "ORG"), ("TEHRAN", "GPE")],
        edges=[("JOHN SMITH", "ACME CORP", "works_at", "CEO of Acme")],
    )
    # Investigation B shares JOHN SMITH (exact, lowercase) and ACME CORP (punct
    # variant "ACME CORP."), plus a brand-new entity.
    gb = _graph(
        nodes=[("john smith", "PERSON"), ("ACME CORP.", "ORG"), ("WASHINGTON", "GPE")],
        edges=[("john smith", "ACME CORP.", "works_at", "leads Acme")],
    )

    res_a = await kg.merge_graph(ga, source_id="inv::a")
    res_b = await kg.merge_graph(gb, source_id="inv::b")

    smith = await kg.get_node("JOHN SMITH")
    acme = await kg.get_node("ACME CORP")
    tehran = await kg.get_node("TEHRAN")
    washington = await kg.get_node("WASHINGTON")
    return {
        "res_a": res_a, "res_b": res_b,
        "smith_src": (smith or {}).get("source_id", ""),
        "acme_src": (acme or {}).get("source_id", ""),
        "tehran": tehran is not None,
        "washington": washington is not None,
        "lower_smith_missing": (await kg.get_node("john smith")) is None,
    }


def test_cumulative_merge_unions_source_ids():
    _reset_shared_storage()
    out = asyncio.run(_merge_two())
    # Shared entities carry BOTH investigations' source ids (merge, not clobber).
    assert "inv::a" in out["smith_src"] and "inv::b" in out["smith_src"], out["smith_src"]
    assert "inv::a" in out["acme_src"] and "inv::b" in out["acme_src"], out["acme_src"]
    # The punctuation variant collapsed onto the canonical (no separate node).
    assert out["lower_smith_missing"]
    # Investigation-unique entities exist on their own.
    assert out["tehran"] and out["washington"]
    # B added one new canonical (WASHINGTON); JOHN SMITH=exact, ACME CORP=normalized.
    assert out["res_b"]["registry"]["new"] >= 1


async def _merge_with_event() -> dict:
    from investigator.analytics.cumulative_kg import CumulativeKG

    work = Path(tempfile.mkdtemp()) / "kg"
    kg = CumulativeKG(work)
    headline = "ALICE INDICTED ON FRAUD CHARGES IN LANDMARK CASE"
    graph = {
        "nodes": [
            {"identifier": "ALICE", "type": "entity", "data": {"type": "PERSON"}},
            {"identifier": "GLOBEX", "type": "entity", "data": {"type": "ORG"}},
            {"identifier": headline, "type": "event", "data": {"type": "EVENT"}},
        ],
        "edges": [
            # entity<->entity: kept
            {"src_identifier": "ALICE", "dst_identifier": "GLOBEX", "type": "relationship",
             "weight": 1.0, "relations": {"type": "works_at", "context": ""}},
            # entity<->event and event<->entity: must be dropped (would resurrect the event)
            {"src_identifier": "ALICE", "dst_identifier": headline, "type": "relationship",
             "weight": 1.0, "relations": {"type": "involved_in", "context": ""}},
            {"src_identifier": headline, "dst_identifier": "GLOBEX", "type": "relationship",
             "weight": 1.0, "relations": {"type": "involves", "context": ""}},
        ],
    }
    res = await kg.merge_graph(graph, source_id="inv::e")
    return {
        "res": res,
        "alice": await kg.get_node("ALICE") is not None,
        "globex": await kg.get_node("GLOBEX") is not None,
        "event_missing": await kg.get_node(headline) is None,
    }


def test_event_nodes_and_their_edges_excluded():
    _reset_shared_storage()
    out = asyncio.run(_merge_with_event())
    # Entities survive; the headline-shaped event never becomes a KG node.
    assert out["alice"] and out["globex"]
    assert out["event_missing"]
    # Only the single entity<->entity edge is merged (both event edges dropped).
    assert out["res"]["nodes_merged"] == 2, out["res"]
    assert out["res"]["edges_merged"] == 1, out["res"]


async def _merge_with_temporal() -> dict:
    from investigator.analytics.cumulative_kg import CumulativeKG

    work = Path(tempfile.mkdtemp()) / "kg"
    kg = CumulativeKG(work)
    graph = {
        "nodes": [
            {"identifier": "ALICE", "type": "entity", "data": {"type": "PERSON"}},
            {"identifier": "GLOBEX", "type": "entity", "data": {"type": "ORG"}},
            {"identifier": "EV1", "type": "event", "data": {"type": "EVENT", "date": "2024-05-10"}},
            {"identifier": "EV2", "type": "event", "data": {"type": "EVENT", "date": "2024-09-20"}},
        ],
        "edges": [
            {"src_identifier": "ALICE", "dst_identifier": "GLOBEX", "type": "affiliation",
             "weight": 1.0, "relations": {"type": "works_at", "context": ""},
             "search_url": "https://news.test/a"},
            # ALICE + GLOBEX both participate in EV1 and EV2 -> active window spans both.
            {"src_identifier": "EV1", "dst_identifier": "ALICE", "type": "event_participation",
             "relations": {"type": "participates_in", "context": ""}},
            {"src_identifier": "EV1", "dst_identifier": "GLOBEX", "type": "event_participation",
             "relations": {"type": "participates_in", "context": ""}},
            {"src_identifier": "EV2", "dst_identifier": "ALICE", "type": "event_participation",
             "relations": {"type": "participates_in", "context": ""}},
            {"src_identifier": "EV2", "dst_identifier": "GLOBEX", "type": "event_participation",
             "relations": {"type": "participates_in", "context": ""}},
        ],
    }
    await kg.merge_graph(graph, source_id="inv::t",
                         source_dates={"https://news.test/a": "2024-03-01"})
    return {"edge": kg.structured_edge("ALICE", "GLOBEX")}


def test_edge_gets_observed_and_valid_time():
    _reset_shared_storage()
    out = asyncio.run(_merge_with_temporal())
    e = out["edge"]
    assert e is not None
    assert e["observed_dates"] == ["2024-03-01"]              # from source_dates (pub date)
    assert e["active_window"] == ["2024-05-10", "2024-09-20"]  # from shared events EV1, EV2


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
