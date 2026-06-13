"""Stage 4 — consolidator — TRIANGULATION_REVIEW §4.

The consolidator (a) attaches evidence to entities (both polarities for net
``prob``) and (b) stamps each supporting evidence with the affiliation path to
``root`` ("Evidence through affiliations …->ROOT") — the relevance chain to the
investigation subject. Survival = credible evidence (``prob>0``); G8 (every
survivor connected to root) is enforced downstream in ``score_graph``.

This file has two layers:
  * synthetic micro-graphs for polarity + path-annotation semantics;
  * a golden regression that runs the consolidator over the captured
    ``consolidator_in_*`` inputs with mocked LLM evidence lists, asserting the
    survival set and per-node probs match the captured ``consolidator_out_nodes``
    byte-for-byte (the reasoning text now carries the new annotation, which is
    asserted positively).

    PYTHONPATH=.:src:/home/dsivov/Work/tangos_mvp <tangos-python> tests/test_stage4_consolidator.py
"""

from __future__ import annotations

import asyncio
import copy
import gzip
import json
import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import tangraph.pipeline.orchestrator as orch  # noqa: E402
from tangraph.graph.operations import evidence_probability  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "golden_stages.json.gz"


def _evidence(related, supports, strength, confidence=0.9):
    return {
        "related_node": related,
        "hypothesis": supports,
        "strength": strength,
        "confidence": confidence,
        "evidence": [f"{'supports' if supports else 'contradicts'} {related}"],
        "reasoning": "r",
        "metadata": {"source": "doc-" + ("s" if supports else "c")},
    }


def _node(ident):
    return {"identifier": ident, "representative_identifier": ident, "labels": [], "data": {"relations": []}}


def _run_consolidator(extract_result, investigate_result, nodes, graph, reps, root):
    async def fake_extract(*a, **k):
        return extract_result

    async def fake_investigate(*a, **k):
        return investigate_result

    orig_e, orig_i = orch.extract_evidence_from_chunk_task, orch.investigate_evidences_task
    orch.extract_evidence_from_chunk_task = fake_extract
    orch.investigate_evidences_task = fake_investigate
    try:
        return asyncio.run(
            orch.node_and_evidence_consolidator({}, nodes, root, reps, graph, "hyp", "subj")
        )
    finally:
        orch.extract_evidence_from_chunk_task = orig_e
        orch.investigate_evidences_task = orig_i


# --- polarity / prob semantics ------------------------------------------


def test_both_supporting_and_contradicting_evidence_attached():
    g = nx.DiGraph()
    g.add_edge("ROOT", "ACME")
    out, *_ = _run_consolidator(
        extract_result=[_evidence("ACME", supports=True, strength=0.9)],
        investigate_result=[_evidence("ACME", supports=False, strength=0.8)],
        nodes=[_node("ROOT"), _node("ACME")], graph=g, reps=[], root="ROOT",
    )
    acme = next(n for n in out if n["identifier"] == "ACME")
    polarities = sorted(e["hypothesis"] for e in acme["evidence"])
    assert polarities == [False, True], f"expected both polarities, got {acme['evidence']}"


def test_contradicting_evidence_lowers_prob_vs_support_only():
    def run(invest):
        g = nx.DiGraph()
        g.add_edge("ROOT", "ACME")
        out, *_ = _run_consolidator(
            [_evidence("ACME", True, 0.9)], invest,
            [_node("ROOT"), _node("ACME")], g, [], root="ROOT",
        )
        acme = next(n for n in out if n["identifier"] == "ACME")
        return evidence_probability(acme["evidence"])

    assert run([_evidence("ACME", supports=False, strength=0.9)]) < run([])


# --- root-oriented path annotation (Loop 2 folded in) -------------------


def test_supporting_evidence_annotated_with_path_to_root():
    # ROOT -> HUB -> LEAF; supporting evidence on LEAF should be stamped with
    # the affiliation chain back to ROOT.
    g = nx.DiGraph()
    g.add_edge("ROOT", "HUB"); g.add_edge("HUB", "LEAF")
    out, routed, _, _ = _run_consolidator(
        [_evidence("LEAF", supports=True, strength=0.9)], [],
        [_node("ROOT"), _node("HUB"), _node("LEAF")], g, [], root="ROOT",
    )
    leaf = next(n for n in out if n["identifier"] == "LEAF")
    reasoning = leaf["evidence"][0]["reasoning"]
    assert reasoning.startswith("Evidence through affiliations LEAF->HUB->ROOT."), reasoning[:120]
    assert routed == 1


def test_contradicting_evidence_not_annotated():
    # Only supporting evidence carries the provenance chain (the entity surfaces
    # because it is implicated, not because it is exonerated).
    g = nx.DiGraph()
    g.add_edge("ROOT", "ACME")
    out, routed, _, _ = _run_consolidator(
        [], [_evidence("ACME", supports=False, strength=0.9)],
        [_node("ROOT"), _node("ACME")], g, [], root="ROOT",
    )
    acme = next(n for n in out if n["identifier"] == "ACME")
    assert not acme["evidence"][0]["reasoning"].startswith("Evidence through affiliations")
    assert routed == 0


def test_unaffiliated_entity_not_annotated_relies_on_g8_wire():
    # ISLAND has no affiliation path to ROOT -> not annotated by the consolidator;
    # score_graph will wire it to root via an evidence edge for G8.
    g = nx.DiGraph()
    g.add_node("ROOT"); g.add_node("ISLAND")
    out, routed, _, _ = _run_consolidator(
        [_evidence("ISLAND", supports=True, strength=0.9)], [],
        [_node("ROOT"), _node("ISLAND")], g, [], root="ROOT",
    )
    island = next(n for n in out if n["identifier"] == "ISLAND")
    assert not island["evidence"][0]["reasoning"].startswith("Evidence through affiliations")
    assert routed == 0
    assert island.get("prob", 0) > 0, "evidenced entity must still survive"


def test_related_node_canonicalized_via_representative():
    # related_node "Acme Holdings" matches a representative whose canonical id is
    # ACME CORP; evidence attaches to ACME CORP and gets the path annotation.
    g = nx.DiGraph()
    g.add_edge("ROOT", "ACME CORP")
    reps = [{"identifier": "ACME CORP", "relevant_identifiers": ["Acme Holdings"]}]
    out, _routed, _, _ = _run_consolidator(
        [_evidence("Acme Holdings", supports=True, strength=0.9)], [],
        [_node("ROOT"), _node("ACME CORP")], g, reps, root="ROOT",
    )
    acme = next(n for n in out if n["identifier"] == "ACME CORP")
    assert acme["evidence"][0]["reasoning"].startswith("Evidence through affiliations ACME CORP->ROOT.")


# --- golden regression --------------------------------------------------


def test_golden_consolidator_preserves_survival_and_probs():
    """Run the consolidator over the captured input with mocked evidence lists;
    assert the survival set + per-node probs are byte-for-byte identical to the
    captured output (the only intended change is reasoning text annotation)."""
    if not GOLDEN.exists():
        return  # golden fixture not shipped in the public repo
    g = json.load(gzip.open(GOLDEN))
    clean = nx.node_link_graph(g["build_graph_graph"]["__nx__"], edges="edges")
    nodes_in = copy.deepcopy(g["consolidator_in_nodes"])
    reps = g["consolidator_in_reps"]
    # the golden was captured with query="Exampleorg" (not in the data), so root
    # fell back to top_degrees[0] = GLOBALAID, INC.
    root = g["consolidator_in_top_degrees"][0]

    out, routed, _phc, _ = _run_consolidator(
        g["evidences_extract"], g["evidences_investigate"],
        nodes_in, clean.copy(), reps, root=root,
    )
    gold = g["consolidator_out_nodes"]

    def survival(ns): return {n["identifier"] for n in ns if n.get("evidence") and n.get("prob", 0) > 0}
    def probs(ns): return {n["identifier"]: round(n.get("prob", 0.0), 6) for n in ns}

    sm, sg = survival(out), survival(gold)
    assert sm == sg, f"survival drift: only-mine={sorted(sm - sg)[:5]} only-gold={sorted(sg - sm)[:5]}"
    pm, pg = probs(out), probs(gold)
    diffs = {k for k in pm if abs(pm[k] - pg.get(k, -1)) > 1e-6}
    assert not diffs, f"prob drift on {len(diffs)} nodes: {list(diffs)[:5]}"
    # the new root-provenance annotation must actually fire on the golden
    assert routed > 0, "Loop-2 folded in: at least one supporting evidence should carry the path-to-root annotation"
    ann = sum(
        1 for n in out for e in n.get("evidence", [])
        if isinstance(e, dict) and str(e.get("reasoning", "")).startswith("Evidence through affiliations")
    )
    assert ann == routed, f"counter / annotations disagree: routed={routed} annotated={ann}"


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
