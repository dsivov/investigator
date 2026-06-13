"""Unit tests for the Stage-1 entity picker's headline-shape filter.

NER occasionally admits a headline string as an entity (e.g. "FRANCE
DETAINS TANKER LINKED TO IRANIAN NETWORK..."). Such pseudo-entities
score highly because TMFG triangulates them into the seed's clique,
so without filtering they get picked as Stage-2 expansion seeds --
burning the article budget on queries that just mirror one headline.

Standalone runner:
    PYTHONPATH=.:src:research /home/dsivov/.conda/envs/tangos/bin/python tests/test_picker_headline_filter.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "research"))

from gnews_deep_investigation import _pick_top_entities  # noqa: E402


HEADLINE_LIKE = (
    "FRANCE DETAINS TANKER LINKED TO IRANIAN NETWORK ACCUSED OF MOVING RUSSIAN OIL"
)


def _node(ident, score=1.0, runs=("run_a",), type_="entity"):
    return {"identifier": ident, "score": score, "runs": list(runs), "type": type_}


def _response(nodes, themes):
    return {"nodes": nodes, "themes": [{"members": list(m)} for m in themes]}


def test_headline_shaped_identifier_is_skipped():
    nodes = [
        _node("FRENCH NAVY", 0.9),
        _node("FRANCE", 0.8),
        _node(HEADLINE_LIKE, 0.95),
        _node("IRANIAN NETWORK", 0.85),
        _node("RUSSIAN SHADOW FLEET", 0.7),
    ]
    themes = [
        ["FRENCH NAVY", HEADLINE_LIKE, "FRANCE", "IRANIAN NETWORK"],
        ["RUSSIAN SHADOW FLEET"],
    ]
    picked = _pick_top_entities(_response(nodes, themes), top_n=4,
                                exclude=set(), restrict_to_run="run_a")
    assert HEADLINE_LIKE not in picked, "headline-shaped pseudo-entity must not be picked"
    assert "FRENCH NAVY" in picked
    # Slot freed up by the rejection gets filled from the fallback pool
    assert len(picked) == 4


def test_filter_promotes_real_entity_over_headline():
    # Highest-score candidate is the headline; without the filter it would
    # always come first. With the filter, the real entity wins.
    nodes = [
        _node(HEADLINE_LIKE, 0.99),
        _node("RUSSIA", 0.5),
    ]
    themes = [[HEADLINE_LIKE, "RUSSIA"]]
    picked = _pick_top_entities(_response(nodes, themes), top_n=1,
                                exclude=set(), restrict_to_run="run_a")
    assert picked == ["RUSSIA"]


def test_short_noun_phrase_passes_filter():
    nodes = [_node("US TREASURY", 0.9), _node("HAMAS", 0.8)]
    themes = [["US TREASURY", "HAMAS"]]
    picked = _pick_top_entities(_response(nodes, themes), top_n=2,
                                exclude=set(), restrict_to_run="run_a")
    assert set(picked) == {"US TREASURY", "HAMAS"}


def test_event_node_rejected_from_picks():
    # Events are incident descriptions, not subjects to query GNews for --
    # a Stage-2 fetch on an event identifier just returns articles about
    # the same incident, wasting the article budget. The picker must skip
    # them outright (independent of their identifier shape).
    event_id = "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD"
    nodes = [
        _node(event_id, 0.95, type_="event"),
        _node("HAMAS", 0.5),
    ]
    themes = [[event_id, "HAMAS"]]
    picked = _pick_top_entities(_response(nodes, themes), top_n=2,
                                exclude=set(), restrict_to_run="run_a")
    assert event_id not in picked, "event identifiers must be rejected as Stage-2 seeds"
    assert "HAMAS" in picked


def test_event_with_short_id_also_rejected():
    # Even if an event identifier happens to be short / noun-phrase-shaped,
    # the type-based reject still fires.
    nodes = [
        _node("Russia-Ukraine ceasefire talks", 0.95, type_="event"),
        _node("RUSSIA", 0.5),
    ]
    themes = [["Russia-Ukraine ceasefire talks", "RUSSIA"]]
    picked = _pick_top_entities(_response(nodes, themes), top_n=2,
                                exclude=set(), restrict_to_run="run_a")
    assert "Russia-Ukraine ceasefire talks" not in picked
    assert picked == ["RUSSIA"]


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
