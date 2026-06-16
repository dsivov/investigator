"""Unit tests for index-ified merge_run_into_saved + attach_relations_to_nodes (step 7).

Imports investigator.graph.dedup (loads WordLlama at import), so run with the
tangos env:

    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_merge_and_relations.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.graph.dedup import (  # noqa: E402
    attach_relations_to_nodes,
    cluster_identifiers,
    merge_run_into_saved,
)
from investigator.graph.dedup import _MRI_GROUP_SIZE  # noqa: E402


def _node(identifier, rep=None, **kw):
    n = {
        "identifier": identifier,
        "representative_identifier": rep or identifier,
        "unique_identifier": kw.pop("uid", f"uid-{identifier}"),
        "type": "entity",
        "data": {"relevance_score": 0.9},
    }
    n.update(kw)
    return n


def _edge(src, dst, **kw):
    e = {"src_identifier": src, "dst_identifier": dst, "attributes": {}, "source": ""}
    e.update(kw)
    return e


# --- merge_run_into_saved ---------------------------------------------------------


def test_merge_node_into_matching_saved_node():
    saved = [_node("ACME", uid="old-uid", labels=["ACME"])]
    new = _node("ACME", uid="new-uid", labels=["ACME CORP"], leaf=True, prob=0.7)
    edges, dedup, sedges, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == []                                  # merged node removed from the new list
    sn = snodes[0]
    assert sn["unique_identifier"] == "new-uid"
    assert set(sn["labels"]) == {"ACME", "ACME CORP"}   # union
    assert sn["leaf"] is True and sn["prob"] == 0.7


def test_merge_propagates_unique_ids_to_saved_edges():
    saved_nodes = [_node("ACME", uid="acme-uid")]
    saved_edges = [_edge("ACME", "BOB"), _edge("BOB", "ACME")]
    new = _node("ACME", uid="acme-uid")
    merge_run_into_saved([], [new], saved_edges, saved_nodes)
    assert saved_edges[0]["src_unique_identifier"] == "acme-uid"   # ACME is src of edge0
    assert saved_edges[1]["dst_unique_identifier"] == "acme-uid"   # ACME is dst of edge1


def test_non_matching_node_is_kept():
    saved = [_node("ACME")]
    new = _node("NEWCO")
    _e, dedup, _se, _sn = merge_run_into_saved([], [new], [], saved)
    assert len(dedup) == 1 and dedup[0]["identifier"] == "NEWCO"


# --- cross-stage alias dedup (task #13) ------------------------------------
# Rule 1 (structural subset): smaller token set is a subset of larger AND
# smaller has >= 2 tokens. Rule 2 (semantic): WordLlama sim + Jaccard overlap.


def test_alias_THE_prefix_merged_into_saved():
    saved = [_node("ACME FOUNDATION OF AMERICA", uid="z-1")]
    new = _node("THE ACME FOUNDATION OF AMERICA", uid="z-2", labels=["acme"])
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == []                  # alias merged, no duplicate left as a "new" node
    assert len(snodes) == 1
    # canonical identifier preserved (saved's), alias recorded as a label
    assert snodes[0]["identifier"] == "ACME FOUNDATION OF AMERICA"
    assert "THE ACME FOUNDATION OF AMERICA" in snodes[0]["labels"]


def test_alias_qualifier_prefix_merged():
    saved = [_node("MOSQUE FOUNDATION")]
    new = _node("CHICAGO-AREA MOSQUE FOUNDATION")
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == []
    assert len(snodes) == 1
    assert "CHICAGO-AREA MOSQUE FOUNDATION" in snodes[0]["labels"]


def test_alias_dotted_acronym_merged():
    # "U.S. DEPARTMENT OF THE TREASURY" should merge into "US TREASURY":
    # dots inside acronyms must be stripped before tokenisation, otherwise the
    # token set becomes {U, S, DEPARTMENT, TREASURY} -- no `US` to satisfy the
    # subset rule against {US, TREASURY}.
    saved = [_node("US TREASURY")]
    new = _node("U.S. DEPARTMENT OF THE TREASURY")
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == []
    assert len(snodes) == 1
    assert snodes[0]["identifier"] == "US TREASURY"
    assert "U.S. DEPARTMENT OF THE TREASURY" in snodes[0]["labels"]


def test_alias_singular_plural_merged_via_semantic_rule():
    # HAND vs HANDS -- token sets differ, Rule 1 fails; Rule 2 (sim + jaccard) catches it.
    saved = [_node("HELPING HAND FOR RELIEF AND DEVELOPMENT")]
    new = _node("HELPING HANDS FOR RELIEF AND DEVELOPMENT")
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == [], "HAND/HANDS variant should merge via semantic rule"
    assert len(snodes) == 1


def test_distinct_HAMAS_vs_HAMAS_PROXIES_not_merged():
    # 'HAMAS' is a subset of 'HAMAS PROXIES IN GAZA' but the size-2 guard
    # blocks the merge (single-token subset is too generic).
    saved = [_node("HAMAS")]
    new = _node("HAMAS PROXIES IN GAZA")
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert len(dedup) == 1, "single-token subset must NOT merge (would collapse distinct entities)"
    assert {sn["identifier"] for sn in snodes} == {"HAMAS"}
    assert {n["identifier"] for n in dedup} == {"HAMAS PROXIES IN GAZA"}


def test_distinct_INTERACTION_vs_TOGETHER_PROJECT_not_merged():
    saved = [_node("INTERACTION")]
    new = _node("INTERACTION'S TOGETHER PROJECT (CIVIC SPACE)")
    _e, dedup, _se, _sn = merge_run_into_saved([], [new], [], saved)
    assert len(dedup) == 1, "single-token subset INTERACTION must NOT collapse a parent/project pair"


def test_alias_rewrites_incoming_edge_endpoints():
    # When a new node's identifier is detected as an alias of a saved one,
    # incoming edges that reference the alias must be rewritten to use the
    # canonical identifier.
    saved_nodes = [_node("ACME FOUNDATION OF AMERICA", uid="z-1")]
    new = _node("THE ACME FOUNDATION OF AMERICA")
    enrich_edges = [_edge("THE ACME FOUNDATION OF AMERICA", "JOHN DOE"),
                    _edge("JOHN DOE", "THE ACME FOUNDATION OF AMERICA")]
    out_edges, _d, _se, _sn = merge_run_into_saved(enrich_edges, [new], [], saved_nodes)
    # Endpoints rewritten to the saved canonical identifier
    assert out_edges[0]["src_identifier"] == "ACME FOUNDATION OF AMERICA"
    assert out_edges[1]["dst_identifier"] == "ACME FOUNDATION OF AMERICA"


# --- Rule 4: symmetric surface-form match (cross-stage canonical divergence) ---
# S1 and S2 choose canonical names independently; the same entity can surface as
# two different canonicals across stages. The incoming node's labels / rep id
# must be matched against saved identifiers, not just the incoming identifier.


def test_cross_stage_incoming_label_matches_saved_identifier():
    # S1 canonical "INTERNATIONAL CRIMINAL COURT"; S2 picked "ICC" but carries
    # the S1 canonical as a label -> must merge into the saved node, not dup.
    saved = [_node("INTERNATIONAL CRIMINAL COURT", uid="s1")]
    new = _node("ICC", uid="s2", labels=["INTERNATIONAL CRIMINAL COURT"])
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == []                                   # merged, not a new node
    assert len(snodes) == 1
    assert snodes[0]["identifier"] == "INTERNATIONAL CRIMINAL COURT"  # saved canonical kept
    assert "ICC" in snodes[0]["labels"]                  # variant recorded


def test_cross_stage_incoming_rep_matches_saved_identifier():
    # Incoming identifier "KOREA" with representative_identifier "SOUTH KOREA"
    # merges into a saved "SOUTH KOREA".
    saved = [_node("SOUTH KOREA", uid="s1")]
    new = _node("KOREA", rep="SOUTH KOREA", uid="s2")
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == []
    assert len(snodes) == 1
    assert snodes[0]["identifier"] == "SOUTH KOREA"


def test_cross_stage_unrelated_label_does_not_overmerge():
    # A label that shares no name with the saved record must NOT pull a merge.
    saved = [_node("ACME CORP", uid="s1")]
    new = _node("GLOBEX", uid="s2", labels=["GLOBEX INTERNATIONAL"])
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert len(dedup) == 1 and dedup[0]["identifier"] == "GLOBEX"
    assert len(snodes) == 1


# --- cluster_identifiers: always split into small similar groups for the LLM --
# Long name lists make MostRepresentativeIdentifier return wrong canonicals, so
# every list above the group size is clustered into bounded similar groups.


def test_cluster_identifiers_splits_and_preserves():
    # A large list is split into more than one group, losing/duplicating nothing.
    names = [f"PERSON {i:03d} SURNAME{i}" for i in range(_MRI_GROUP_SIZE * 3)]
    groups = cluster_identifiers(names)
    assert len(groups) > 1                                  # not one long list
    assert all(g for g in groups)                           # no empty groups
    flat = [n for g in groups for n in g]
    assert sorted(flat) == sorted(names)                    # nothing lost/duplicated


def test_cluster_identifiers_keeps_variants_together():
    # The point of the recursive *similarity* split: variants of one entity must
    # never land in different groups. Tight variant set + many other names.
    variants = ["ACME CORPORATION", "ACME CORP", "ACME COMPANY"]
    others = [f"UNREL ENTITY {i:03d}" for i in range(_MRI_GROUP_SIZE * 2)]
    groups = cluster_identifiers(variants + others)
    home = next(g for g in groups if "ACME CORP" in g)
    assert all(v in home for v in variants), "ACME variants must share one group"


def test_cluster_identifiers_short_list_single_group():
    names = ["ACME", "GLOBEX", "INITECH"]
    assert cluster_identifiers(names) == [names]
    assert cluster_identifiers([]) == []


def test_enrichment_edge_merged_into_saved_edge_by_pair():
    saved_edges = [_edge("ACME", "BOB", source="old", attributes={"a": 1})]
    new_edges = [_edge("ACME", "BOB", source="new", attributes={"b": 2})]
    out_edges, _d, sedges, _sn = merge_run_into_saved(new_edges, [], saved_edges, [])
    assert out_edges == []                  # matched (by src/dst pair) -> removed from new list
    assert sedges[0]["source"] == "new"     # saved edge updated from the enrichment edge
    # attributes now merge properly (Stage-2 F4 fix removed the relevance filter
    # that used to empty attribute dicts with no relevance_score).
    assert sedges[0]["attributes"] == {"a": 1, "b": 2}


def test_non_matching_edge_is_kept():
    saved_edges = [_edge("ACME", "BOB")]
    new_edges = [_edge("ACME", "CAROL")]
    out_edges, _d, _se, _sn = merge_run_into_saved(new_edges, [], saved_edges, [])
    assert len(out_edges) == 1


def test_blank_saved_relation_backfilled_from_incoming():
    # A pair first attested with a blank/"unknown" relation gets its label
    # back-filled when a later run characterizes the same pair.
    blank = json.dumps({"type": "unknown", "context": ""})
    good = json.dumps({"type": "partnership", "context": "joint energy deal"})
    saved_edges = [_edge("CHINA", "RUSSIA", relations=blank)]
    new_edges = [_edge("CHINA", "RUSSIA", relations=good)]
    _o, _d, sedges, _sn = merge_run_into_saved(new_edges, [], saved_edges, [])
    assert sedges[0]["relations"] == good


def test_informative_saved_relation_not_overwritten():
    # Never clobber an already-good saved label with an incoming blank one.
    good = json.dumps({"type": "partnership", "context": "joint energy deal"})
    blank = json.dumps({"type": "unknown", "context": ""})
    saved_edges = [_edge("CHINA", "RUSSIA", relations=good)]
    new_edges = [_edge("CHINA", "RUSSIA", relations=blank)]
    _o, _d, sedges, _sn = merge_run_into_saved(new_edges, [], saved_edges, [])
    assert sedges[0]["relations"] == good


# --- cross-run provenance (runs list union) --------------------------------


def test_runs_union_on_exact_node_match():
    saved = [_node("HAMAS", runs=["israeli_strike_haddad"])]
    new = _node("HAMAS", runs=["gaza_flotilla_sanctions"])
    _e, _d, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert snodes[0]["runs"] == ["israeli_strike_haddad", "gaza_flotilla_sanctions"]


def test_runs_no_field_when_both_legacy():
    # Neither saved nor incoming carries `runs` -> field stays absent.
    saved = [_node("ACME")]
    new = _node("ACME")
    _e, _d, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert "runs" not in snodes[0]


def test_runs_introduced_when_only_incoming_has_them():
    # Saved is legacy (no runs); incoming carries a run -> saved gets it.
    saved = [_node("ACME")]
    new = _node("ACME", runs=["new_run"])
    _e, _d, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert snodes[0]["runs"] == ["new_run"]


def test_runs_dedup_on_repeated_label():
    saved = [_node("HAMAS", runs=["evA", "evB"])]
    new = _node("HAMAS", runs=["evB", "evC"])
    _e, _d, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert snodes[0]["runs"] == ["evA", "evB", "evC"]  # evB not duplicated


def test_runs_union_on_alias_match():
    # Alias path should ALSO union runs lists, not just exact matches.
    saved = [_node("ACME FOUNDATION OF AMERICA", runs=["evA"], uid="z-1")]
    new = _node("THE ACME FOUNDATION OF AMERICA", runs=["evB"])
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == []                                       # alias merged
    assert snodes[0]["identifier"] == "ACME FOUNDATION OF AMERICA"
    assert snodes[0]["runs"] == ["evA", "evB"]


def test_runs_union_on_edge_pair_match():
    saved_edges = [_edge("ACME", "BOB", runs=["evA"])]
    new_edges = [_edge("ACME", "BOB", runs=["evB"])]
    out_edges, _d, sedges, _sn = merge_run_into_saved(new_edges, [], saved_edges, [])
    assert out_edges == []
    assert sedges[0]["runs"] == ["evA", "evB"]


def test_edges_runs_no_field_when_both_legacy():
    saved_edges = [_edge("ACME", "BOB")]
    new_edges = [_edge("ACME", "BOB")]
    _o, _d, sedges, _sn = merge_run_into_saved(new_edges, [], saved_edges, [])
    assert "runs" not in sedges[0]


# --- Rule 3: label-match alias (catches surface forms Rules 1+2 miss) ------


def test_alias_PUTIN_into_VLADIMIR_PUTIN_via_label_match():
    # WordLlama sim is 0.88 (below 0.90); subset min-2 guard blocks Rule 1.
    # Saved record carries 'VLADIMIR PUTIN' as a label, so Rule 3 fires.
    saved = [_node("PUTIN", labels=["VLADIMIR PUTIN"])]
    new = _node("VLADIMIR PUTIN")
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == [], "should merge via label match"
    assert snodes[0]["identifier"] == "PUTIN"


def test_alias_STRAIT_OF_HORMUZ_into_HORMUZ_via_label_match():
    saved = [_node("HORMUZ", labels=["STRAIT OF HORMUZ"])]
    new = _node("STRAIT OF HORMUZ")
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == []
    assert snodes[0]["identifier"] == "HORMUZ"


def test_label_match_unions_runs_and_rewrites_edges():
    saved = [_node("PUTIN", labels=["VLADIMIR PUTIN"], runs=["russia_oil_darkfleet"])]
    new = _node("VLADIMIR PUTIN", runs=["china_yuan_russia"])
    enrich_edges = [_edge("VLADIMIR PUTIN", "XI JINPING")]
    out_edges, dedup, _se, snodes = merge_run_into_saved(enrich_edges, [new], [], saved)
    assert dedup == []
    assert snodes[0]["runs"] == ["russia_oil_darkfleet", "china_yuan_russia"]
    # Incoming edge endpoint rewritten to the canonical
    assert out_edges[0]["src_identifier"] == "PUTIN"


def test_label_match_rejected_when_incoming_id_is_headline_shaped():
    # If a saved record happens to carry a headline-shaped label, an incoming
    # entity with that exact headline must NOT merge -- the canonical-valid
    # gate blocks it. "APPROVES" is in _HEADLINE_VERBS, so the incoming id
    # fails _is_valid_canonical and Rule 3 is skipped.
    saved = [_node("PUTIN", labels=["VLADIMIR PUTIN", "PUTIN APPROVES BEIJING ENERGY DEAL"])]
    new = _node("PUTIN APPROVES BEIJING ENERGY DEAL")
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert len(dedup) == 1, "headline-shaped incoming id must not piggy-back on a label match"
    assert {sn["identifier"] for sn in snodes} == {"PUTIN"}


def test_label_match_does_not_apply_to_events():
    saved = [_node("PUTIN", labels=["VLADIMIR PUTIN"])]
    new = _node("VLADIMIR PUTIN", type="event")
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    # Events skip the alias path entirely; new record stays separate.
    assert len(dedup) == 1
    assert {sn["identifier"] for sn in snodes} == {"PUTIN"}


def test_label_match_skips_self_label():
    # A label identical to the saved record's own identifier must not match
    # itself (defensive: prior canonical-validation can leave a label that
    # equals the identifier).
    saved = [_node("HAMAS", labels=["HAMAS", "Hamas the Resistance Movement"])]
    new = _node("HAMAS")  # exact match -- should hit the saved_by_id path, not Rule 3
    _e, dedup, _se, snodes = merge_run_into_saved([], [new], [], saved)
    assert dedup == []
    assert len(snodes) == 1


# --- attach_relations_to_nodes -----------------------------------------------


def test_outgoing_relation_attached_to_src_node():
    nodes = [_node("ACME"), _node("BOB")]
    edges = [{"source_node": "ACME", "target_node": "BOB", "relations": "rel", "attributes": {}}]
    out = attach_relations_to_nodes(nodes, edges)
    acme = next(n for n in out if n["identifier"] == "ACME")
    rels = acme["data"]["relations"]
    assert len(rels) == 1
    assert rels[0]["direction"] == "outgoing" and rels[0]["related_node"] == "BOB"
    assert acme["triangulated"] is True


def test_incoming_relation_attached_to_dst_node():
    nodes = [_node("ACME"), _node("BOB")]
    edges = [{"source_node": "ACME", "target_node": "BOB", "relations": "rel", "attributes": {}}]
    attach_relations_to_nodes(nodes, edges)
    bob = next(n for n in nodes if n["identifier"] == "BOB")
    assert bob["data"]["relations"][0]["direction"] == "incoming"
    assert bob["triangulated"] is True


def test_match_by_representative_identifier():
    nodes = [_node("ACME CORP", rep="ACME CORP")]
    edges = [{"source_node": "ACME CORP", "target_node": "OUTSIDER", "relations": "rel", "attributes": {}}]
    attach_relations_to_nodes(nodes, edges)
    assert len(nodes[0]["data"]["relations"]) == 1
    assert edges[0]["source_node"] == "ACME CORP"   # endpoint rewritten to the rep


def test_empty_relations_adds_nothing():
    nodes = [_node("ACME"), _node("BOB")]
    edges = [{"source_node": "ACME", "target_node": "BOB", "relations": "", "attributes": {}}]
    attach_relations_to_nodes(nodes, edges)
    assert all(n["data"]["relations"] == [] for n in nodes)
    assert all(n["triangulated"] is False for n in nodes)


def test_node_without_edges_has_empty_relations():
    nodes = [_node("LONELY")]
    attach_relations_to_nodes(nodes, [])
    assert nodes[0]["data"]["relations"] == []
    assert nodes[0]["triangulated"] is False


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
