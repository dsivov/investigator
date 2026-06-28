"""Root-selection tests — the triangulation root must be the investigation
subject (hops-to-root is the relevance metric), NOT merely the busiest node.

The pipeline resolves the root in three steps (see _standard_pipeline):
  1. fast-path: match the raw query to an entity (allow_name_in_query=False so a
     sentence query can't false-match a short entity name);
  2. if no match, NER on the query distils a subject, matched with the full rule;
  3. else fall back to the most-connected node.

These tests cover the pure matcher `match_query_to_entity` (steps 1/2); the LLM
distillation in step 2 is exercised by the e2e smoke.

Imports investigator.pipeline.orchestrator (heavy: loads dspy/model2vec); run with:
    PYTHONPATH=.:src <tangos-python> tests/test_resolve_root.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investigator.pipeline.orchestrator import match_query_to_entity  # noqa: E402


def _graph(*nodes):
    """Directed star; first arg is the hub (highest degree)."""
    g = nx.DiGraph()
    hub = nodes[0]
    for n in nodes:
        g.add_node(n)
    for n in nodes[1:]:
        g.add_edge(hub, n)
    return g


def _ent(identifier, *, rep=None, labels=None):
    return {"identifier": identifier, "representative_identifier": rep or identifier, "labels": list(labels or [])}


def test_subject_match_beats_highest_degree():
    # HUB has the most edges, but the subject is LEAF -> root must be LEAF
    g = _graph("HUB", "LEAF", "OTHER")
    ents = [_ent("HUB"), _ent("LEAF"), _ent("OTHER")]
    assert match_query_to_entity("leaf", ents, [], g) == "LEAF"


def test_subject_substring_of_entity_name():
    g = _graph("GLOBALAID, INC.", "X")
    ents = [_ent("GLOBALAID, INC."), _ent("X")]
    assert match_query_to_entity("Globalaid", ents, [], g) == "GLOBALAID, INC."


def test_entity_name_substring_of_subject_when_allowed():
    # distilled subject is more verbose than the entity name
    g = _graph("EXAMPLEORG", "X")
    ents = [_ent("EXAMPLEORG"), _ent("X")]
    assert match_query_to_entity("Exampleorg Network", ents, [], g) == "EXAMPLEORG"


def test_label_match():
    g = _graph("ACME CORP", "X")
    ents = [_ent("ACME CORP", labels=["ACME", "Acme Corporation"]), _ent("X")]
    assert match_query_to_entity("acme", ents, [], g) == "ACME CORP"


def test_representative_relevant_identifiers_match():
    g = _graph("CANON", "X")
    ents = [_ent("CANON"), _ent("X")]
    reps = [{"identifier": "CANON", "relevant_identifiers": ["aka acme", "the firm"]}]
    assert match_query_to_entity("acme", ents, reps, g) == "CANON"


def test_absent_subject_returns_none():
    g = _graph("HUB", "A", "B")
    ents = [_ent("HUB"), _ent("A"), _ent("B")]
    assert match_query_to_entity("Exampleorg", ents, [], g) is None


def test_empty_subject_returns_none():
    g = _graph("HUB", "A")
    assert match_query_to_entity("", [_ent("HUB")], [], g) is None
    assert match_query_to_entity(None, [_ent("HUB")], [], g) is None


def test_match_not_in_graph_used_anyway():
    # entity matches but has no affiliations (not a graph node): still returned,
    # since the caller has nothing better than the unrelated most-connected node
    g = _graph("HUB", "A")
    ents = [_ent("HUB"), _ent("ISOLATED")]
    assert match_query_to_entity("isolated", ents, [], g) == "ISOLATED"


def test_sentence_query_does_not_false_match_short_name():
    # raw-query fast-path: a common word in the sentence must NOT match a short
    # entity name (allow_name_in_query=False guards the nu-in-q direction)
    g = _graph("HUB", "ALL", "FOR")
    ents = [_ent("HUB"), _ent("ALL"), _ent("FOR")]
    got = match_query_to_entity(
        "Find all financial connections for Globalaid", ents, [], g, allow_name_in_query=False
    )
    assert got is None, f"sentence query false-matched a short entity name: {got}"


def test_in_graph_match_preferred_over_isolated():
    g = _graph("HUB", "ACME CORP")  # ACME CORP is in the graph
    ents = [_ent("ACME CORP"), _ent("ACME HOLDINGS")]  # both match "acme"; prefer the in-graph one
    assert match_query_to_entity("acme", ents, [], g) == "ACME CORP"


def test_event_node_is_never_the_root():
    # A long sentence query can match an event/headline-shaped node verbatim
    # (e.g. "CHARLOTTE KATES LINKS TO ... MATERIAL SUPPORT"). The root is the
    # investigation subject -- always an entity -- so the event must be skipped,
    # leaving the fast-path empty so the caller falls through to NER.
    sentence = "CHARLOTTE KATES LINKS TO DESIGNATED TERRORIST GROUPS MATERIAL SUPPORT"
    g = _graph(sentence, "CHARLOTTE KATES")
    ents = [
        {"identifier": sentence, "representative_identifier": sentence, "labels": [], "type": "event"},
        _ent("CHARLOTTE KATES"),
    ]
    # Raw-query fast-path: only the event matched verbatim -> excluded -> None.
    assert match_query_to_entity(
        "Charlotte Kates links to designated terrorist groups material support",
        ents, [], g, allow_name_in_query=False,
    ) is None
    # Distilled subject then matches the real entity (event still excluded).
    assert match_query_to_entity("Charlotte Kates", ents, [], g) == "CHARLOTTE KATES"


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
    print(f"\n{len(tests) - failures}/{len(tests)} root-selection tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
