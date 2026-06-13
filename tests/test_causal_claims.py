"""Unit tests for the causal-claim edge synthesiser.

Mocks chunk-level extraction output + merged_entities to exercise the
resolve / aggregate / weight pipeline without invoking the LLM.

Standalone runner:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_causal_claims.py
"""
from __future__ import annotations

import sys
import importlib.util
from pathlib import Path

# Patch in the function via direct import; the orchestrator class hosts it
# as a method but the logic is pure -- we instantiate a thin shim to call it.

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


class _Shim:
    """Minimal shim that exposes only the static-ish synthesise method."""
    def __init__(self):
        # Lazy import to avoid pulling the whole pipeline at module top
        from tangraph.pipeline.orchestrator import InvestigationPipeline
        # Bind the method to this instance
        self._synthesise = InvestigationPipeline._synthesise_causal_claim_edges.__get__(self, type(self))

    def __call__(self, chunks, entities, run=None):
        return self._synthesise(chunks, entities, run=run)


def _node(ident, **kw):
    n = {"identifier": ident, "unique_identifier": f"uid-{ident[:10]}",
         "type": kw.pop("type", "entity"),
         "representative_identifier": ident,
         "labels": kw.pop("labels", [])}
    n.update(kw)
    return n


def _chunk(claims, uuid="c1"):
    return {"uuid": uuid, "causal_claims": claims}


def _claim(cause, effect, **kw):
    c = {
        "cause": cause, "effect": effect,
        "direction": kw.pop("direction", "triggers"),
        "hedging": kw.pop("hedging", "explicit"),
        "claim_text": kw.pop("claim_text", f"{cause} triggered {effect}"),
        "strength": kw.pop("strength", 0.8),
        "confidence": kw.pop("confidence", 0.9),
        "source_url": kw.pop("source_url", "https://example.com/a"),
    }
    c.update(kw)
    return c


# --- core resolution + aggregation -----------------------------------------

def test_single_claim_emits_edge_with_correct_weight():
    syn = _Shim()
    entities = [_node("ISRAELI STRIKE"), _node("HAMAS APPOINTMENT")]
    chunks = [_chunk([_claim("Israeli strike", "Hamas appointment",
                              strength=0.8, confidence=0.9)])]
    edges = syn(chunks, entities)
    assert len(edges) == 1
    e = edges[0]
    assert e["type"] == "claimed_caused_by"
    assert e["src_identifier"] == "ISRAELI STRIKE"
    assert e["dst_identifier"] == "HAMAS APPOINTMENT"
    # single source -> multi_boost = 1.0; weight = 0.8 * 0.9 * 1.0 = 0.72
    assert abs(e["attributes"]["weight"] - 0.72) < 1e-3
    assert e["attributes"]["attestation_count"] == 1


def test_unresolved_endpoints_drop_silently():
    syn = _Shim()
    entities = [_node("HAMAS")]
    # cause resolves; effect doesn't
    chunks = [_chunk([_claim("Hamas", "Some Imaginary Org")])]
    assert syn(chunks, entities) == []


def test_self_loop_dropped():
    syn = _Shim()
    entities = [_node("HAMAS")]
    chunks = [_chunk([_claim("Hamas", "Hamas")])]
    assert syn(chunks, entities) == []


def test_multi_source_boost_aggregates_distinct_urls():
    syn = _Shim()
    entities = [_node("STRIKE"), _node("APPOINTMENT")]
    # Same claim from 4 distinct sources
    chunks = [_chunk([
        _claim("strike", "appointment", strength=0.8, confidence=0.9,
               source_url=f"https://example.com/a{i}")
        for i in range(4)
    ])]
    edges = syn(chunks, entities)
    assert len(edges) == 1
    e = edges[0]
    # n=4: multi_boost = min(2.0, 1 + 0.3*3) = 1.9
    # weight = 0.8 * 0.9 * 1.9 = 1.368 -> capped at... actually not capped
    assert e["attributes"]["attestation_count"] == 4
    assert abs(e["attributes"]["weight"] - 0.8 * 0.9 * 1.9) < 1e-3


def test_multi_source_boost_caps_at_2x():
    syn = _Shim()
    entities = [_node("A"), _node("B")]
    chunks = [_chunk([
        _claim("a", "b", strength=0.5, confidence=1.0,
               source_url=f"https://example.com/{i}")
        for i in range(10)
    ])]
    edges = syn(chunks, entities)
    # boost capped at 2.0; weight = 0.5 * 1.0 * 2.0 = 1.0
    assert abs(edges[0]["attributes"]["weight"] - 1.0) < 1e-3


def test_max_strength_and_max_confidence_across_claims():
    syn = _Shim()
    entities = [_node("A"), _node("B")]
    chunks = [_chunk([
        _claim("a", "b", strength=0.3, confidence=0.5,
               hedging="speculative", source_url="u1"),
        _claim("a", "b", strength=0.9, confidence=0.95,
               hedging="explicit", source_url="u2"),
    ])]
    edges = syn(chunks, entities)
    e = edges[0]
    assert abs(e["attributes"]["strength"] - 0.9) < 1e-3
    assert abs(e["attributes"]["confidence"] - 0.95) < 1e-3
    assert set(e["attributes"]["hedging_tags"]) == {"speculative", "explicit"}


def test_resolution_via_entity_labels():
    syn = _Shim()
    entities = [
        _node("US TREASURY", labels=["US DEPARTMENT OF THE TREASURY", "TREASURY"]),
        _node("HEZBOLLAH"),
    ]
    chunks = [_chunk([_claim("US Department of the Treasury", "Hezbollah")])]
    edges = syn(chunks, entities)
    assert len(edges) == 1
    # Resolved through the label -> canonical identifier
    assert edges[0]["src_identifier"] == "US TREASURY"


def test_distinct_pairs_emit_separate_edges():
    syn = _Shim()
    entities = [_node("A"), _node("B"), _node("C")]
    chunks = [_chunk([
        _claim("a", "b"),
        _claim("a", "c"),
        _claim("c", "b"),
    ])]
    edges = syn(chunks, entities)
    pairs = sorted((e["src_identifier"], e["dst_identifier"]) for e in edges)
    assert pairs == [("A", "B"), ("A", "C"), ("C", "B")]


def test_edges_ranked_by_weight_desc():
    syn = _Shim()
    entities = [_node("A"), _node("B"), _node("C")]
    chunks = [_chunk([
        _claim("a", "b", strength=0.3, confidence=0.5),       # weight = 0.15
        _claim("a", "c", strength=0.9, confidence=0.9),       # weight = 0.81
    ])]
    edges = syn(chunks, entities)
    assert edges[0]["dst_identifier"] == "C"
    assert edges[1]["dst_identifier"] == "B"
    assert edges[0]["attributes"]["weight"] > edges[1]["attributes"]["weight"]


def test_run_label_stamped_when_passed():
    syn = _Shim()
    entities = [_node("A"), _node("B")]
    chunks = [_chunk([_claim("a", "b")])]
    edges = syn(chunks, entities, run="evX")
    assert edges[0]["runs"] == ["evX"]


def test_no_run_field_when_run_is_none():
    syn = _Shim()
    entities = [_node("A"), _node("B")]
    chunks = [_chunk([_claim("a", "b")])]
    edges = syn(chunks, entities)
    assert "runs" not in edges[0]


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
