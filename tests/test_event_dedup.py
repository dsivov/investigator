"""Unit tests for dedup_events_by_signature.

Verifies the (event_type, date_window, participant Jaccard) matching rule
collapses paraphrased event-records and refuses to collapse distinct
incidents (different date, different participants, different category).

Standalone runner:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_event_dedup.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tangraph.graph.dedup import (  # noqa: E402
    dedup_events_by_signature,
    infer_event_temporal_edges,
)


def _event(identifier: str, *, event_type: str = "military_action",
           date: str | list[str] = "", location: str = "",
           participants: list[dict] = None,
           description: str | list[str] = "",
           source_url: str | list[str] = "",
           confidence: float = 1.0,
           runs: list[str] | None = None) -> dict:
    e = {
        "identifier": identifier,
        "unique_identifier": f"uid-{identifier[:10]}",
        "type": "event",
        "data": {
            "name": identifier,
            "event_type": event_type,
            "date": date,
            "location": location,
            "participants": participants or [],
            "description": description,
            "source_url": source_url,
            "confidence": confidence,
        },
    }
    if runs is not None:
        e["runs"] = runs
    return e


# --- positive: same incident paraphrased -----------------------------------

def test_two_paraphrases_of_same_strike_merge():
    e1 = _event(
        "ISRAELI STRIKE KILLS HAMAS LEADER IZZ AL-DIN AL-HADDAD",
        date="2026-05-16", location="Gaza",
        participants=[
            {"name": "Israel", "role": "perpetrator"},
            {"name": "Izz al-Din al-Haddad", "role": "target"},
            {"name": "Hamas", "role": "organization"},
        ],
        description="Israeli airstrike kills al-Haddad in Gaza.",
        source_url="https://npr.org/a1",
    )
    e2 = _event(
        "ISRAELI AIRSTRIKE KILLS SENIOR HAMAS MILITARY COMMANDER",
        date="2026-05-16", location="Gaza",
        participants=[
            {"name": "Israel", "role": "perpetrator"},
            {"name": "Izz al-Din al-Haddad", "role": "target"},
            {"name": "Hamas", "role": ""},
        ],
        description="Senior Hamas military leader killed in Israeli strike.",
        source_url="https://washingtonpost.com/a2",
    )
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 1, f"expected merge, got {[x['identifier'] for x in out]}"
    canonical = out[0]
    assert canonical["identifier"] == e1["identifier"]
    # Surface form of the merged-away event becomes a label
    assert e2["identifier"] in (canonical.get("labels") or [])
    # Descriptions and source URLs unioned
    desc = canonical["data"]["description"]
    assert isinstance(desc, list) and len(desc) == 2
    urls = canonical["data"]["source_url"]
    assert isinstance(urls, list) and "https://npr.org/a1" in urls and "https://washingtonpost.com/a2" in urls


def test_two_events_within_7_days_with_same_participants_merge():
    # May-16 strike vs May-22 follow-up coverage of same incident -- inside
    # the 7-day window.
    e1 = _event(
        "STRIKE A", date="2026-05-16",
        participants=[{"name": "Israel"}, {"name": "Haddad"}, {"name": "Hamas"}],
    )
    e2 = _event(
        "STRIKE A FOLLOWUP", date="2026-05-22",
        participants=[{"name": "Israel"}, {"name": "Haddad"}, {"name": "Hamas"}],
    )
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 1


def test_year_only_date_refuses_merge():
    # Year-only date is too loose to pin down an incident. The dedup
    # requires month-precision on BOTH sides (calibrated from the big-run
    # dry-run where year-only matches produced false merges across
    # high-frequency actor pairs).
    e1 = _event("E1", date="2026-05-16",
                participants=[{"name": "A"}, {"name": "B"}, {"name": "C"}])
    e2 = _event("E2", date="2026",
                participants=[{"name": "A"}, {"name": "B"}, {"name": "C"}])
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 2


# --- negative: distinct incidents stay separate ----------------------------

def test_other_event_type_refuses_merge():
    # "other" is a catch-all bucket; even when participants overlap and
    # dates align, distinct events tagged as "other" should NOT merge.
    e1 = _event("INDIA DEMANDS BOEING 787 AUDIT", event_type="other",
                date="2026-05-21",
                participants=[{"name": "India"}, {"name": "Boeing"}])
    e2 = _event("NTSB HEARING REVEALS BOEING FAA CERT", event_type="other",
                date="2026-05-22",
                participants=[{"name": "NTSB"}, {"name": "Boeing"}, {"name": "FAA"}])
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 2


def test_empty_event_type_refuses_merge():
    # If either side has no event_type, dedup must refuse the merge --
    # we can't be sure we're comparing same-category incidents.
    e1 = _event("E1", event_type="", date="2026-05-16",
                participants=[{"name": "A"}, {"name": "B"}, {"name": "C"}])
    e2 = _event("E2", event_type="military_action", date="2026-05-16",
                participants=[{"name": "A"}, {"name": "B"}, {"name": "C"}])
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 2


def test_same_actors_different_event_types_do_not_merge():
    e1 = _event(
        "ISRAELI STRIKE ON X", event_type="military_action", date="2026-05-16",
        participants=[{"name": "Israel"}, {"name": "X"}, {"name": "Hamas"}],
    )
    e2 = _event(
        "US TREASURY SANCTIONS X", event_type="sanctions", date="2026-05-16",
        participants=[{"name": "Israel"}, {"name": "X"}, {"name": "Hamas"}],
    )
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 2


def test_same_actors_different_months_do_not_merge():
    # Same cast, different month -> different incidents
    e1 = _event(
        "STRIKE", date="2026-05-16",
        participants=[{"name": "Israel"}, {"name": "X"}, {"name": "Hamas"}],
    )
    e2 = _event(
        "OTHER STRIKE", date="2026-04-08",
        participants=[{"name": "Israel"}, {"name": "X"}, {"name": "Hamas"}],
    )
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 2


def test_disjoint_participants_do_not_merge():
    e1 = _event("STRIKE A", date="2026-05-16",
                participants=[{"name": "Israel"}, {"name": "Haddad"}, {"name": "Hamas"}])
    e2 = _event("STRIKE B", date="2026-05-16",
                participants=[{"name": "Russia"}, {"name": "Ukraine"}, {"name": "Wagner"}])
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 2


def test_known_limit_same_actors_different_actions_DO_over_merge():
    # KNOWN LIMIT (documented in dedup.py): when participants are IDENTICAL
    # and dates align, the dedup CANNOT tell apart distinct actions on the
    # same actor pair (e.g. FAA lifts production cap vs FAA certifies MAX 7).
    # A description-aware second pass is the obvious follow-up; for now we
    # accept the over-merge and document it. This test pins the current
    # behaviour so a future tightening that fixes it is visible.
    e1 = _event(
        "FAA LIFTS BOEING 737 MAX PRODUCTION CAP",
        event_type="legislative", date="2026-05-30",
        participants=[{"name": "FAA"}, {"name": "Boeing"}],
    )
    e2 = _event(
        "FAA EXPECTS BOEING 737 MAX 7 CERTIFICATION BY SUMMER 2026",
        event_type="legislative", date="2026-05-27",
        participants=[{"name": "FAA"}, {"name": "Boeing"}],
    )
    out = dedup_events_by_signature([e1, e2])
    # Pin current (over-merging) behaviour. Flip to == 2 when a smarter
    # action-aware second pass lands.
    assert len(out) == 1


def test_only_one_participant_in_common_below_threshold_no_merge():
    # 1 of 3 participants overlap on each side: |A∩B|/|A∪B| = 1/5 = 0.20 < 0.50
    e1 = _event("E1", date="2026-05-16",
                participants=[{"name": "Israel"}, {"name": "Haddad"}, {"name": "Hamas"}])
    e2 = _event("E2", date="2026-05-16",
                participants=[{"name": "Hamas"}, {"name": "Russia"}, {"name": "Ukraine"}])
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 2


# --- merging semantics -----------------------------------------------------

def test_merge_unions_runs_provenance():
    e1 = _event("S1", date="2026-05-16",
                participants=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
                runs=["runA"])
    e2 = _event("S1 v2", date="2026-05-17",
                participants=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
                runs=["runB"])
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 1
    assert set(out[0].get("runs") or []) == {"runA", "runB"}


def test_merge_takes_max_confidence():
    e1 = _event("S1", date="2026-05-16",
                participants=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
                confidence=0.7)
    e2 = _event("S2", date="2026-05-16",
                participants=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
                confidence=0.95)
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 1
    assert float(out[0]["data"]["confidence"]) == 0.95


def test_merge_dedupes_participants_by_uppercase_name():
    e1 = _event("S1", date="2026-05-16",
                participants=[{"name": "Israel", "role": "perpetrator"},
                              {"name": "Hamas"}, {"name": "Haddad"}])
    e2 = _event("S2", date="2026-05-16",
                participants=[{"name": "ISRAEL", "role": "attacker"},
                              {"name": "hamas"}, {"name": "Haddad"}])
    out = dedup_events_by_signature([e1, e2])
    assert len(out) == 1
    # Participants deduped to 3 unique names, first occurrence wins on role
    names = [p.get("name", "").upper() for p in out[0]["data"]["participants"]]
    assert sorted(names) == ["HADDAD", "HAMAS", "ISRAEL"]


import json


# --- infer_event_temporal_edges --------------------------------------------


def test_temporal_emits_followed_by_when_dates_ordered_with_shared_participants():
    # Two events sharing Hamas, 10 days apart -> event_followed_by edge
    e1 = _event("HADDAD STRIKE", event_type="military_action",
                date="2026-05-16",
                participants=[{"name": "Israel"}, {"name": "Hamas"}, {"name": "Haddad"}])
    e2 = _event("OUDA APPOINTMENT", event_type="other",
                date="2026-05-26",
                participants=[{"name": "Hamas"}, {"name": "Ouda"}])
    out = infer_event_temporal_edges([e1, e2])
    assert len(out) == 1
    edge = out[0]
    assert edge["type"] == "event_followed_by"
    assert edge["src_identifier"] == "HADDAD STRIKE"     # earlier -> later
    assert edge["dst_identifier"] == "OUDA APPOINTMENT"
    rels = json.loads(edge["relations"])
    assert rels["type"] == "followed_by"
    assert edge["attributes"]["shared_participants"] == ["HAMAS"]
    assert edge["attributes"]["days_apart"] == 10


def test_temporal_emits_coincident_for_same_day_events():
    # Two events on the same day sharing actors -> event_coincident
    e1 = _event("STRIKE A", event_type="military_action",
                date="2026-05-16",
                participants=[{"name": "Israel"}, {"name": "Hamas"}])
    e2 = _event("STATEMENT", event_type="diplomatic",
                date="2026-05-16",
                participants=[{"name": "Hamas"}, {"name": "Israel"}])
    out = infer_event_temporal_edges([e1, e2])
    assert len(out) == 1
    assert out[0]["type"] == "event_coincident"
    # Coincident edge uses lexicographic identifier order for stability
    assert out[0]["src_identifier"] < out[0]["dst_identifier"]


def test_temporal_no_edge_when_no_shared_participants():
    e1 = _event("E1", date="2026-05-16",
                participants=[{"name": "Israel"}, {"name": "Hamas"}])
    e2 = _event("E2", date="2026-05-17",
                participants=[{"name": "Boeing"}, {"name": "FAA"}])
    out = infer_event_temporal_edges([e1, e2])
    assert out == []


def test_temporal_no_edge_when_too_far_apart():
    # Same actors but 90 days apart -> too distant to chain as sequence
    e1 = _event("E1", date="2026-02-16",
                participants=[{"name": "Israel"}, {"name": "Hamas"}])
    e2 = _event("E2", date="2026-05-26",
                participants=[{"name": "Israel"}, {"name": "Hamas"}])
    out = infer_event_temporal_edges([e1, e2])
    assert out == []


def test_temporal_no_edge_when_either_date_missing():
    e1 = _event("E1", date="",
                participants=[{"name": "Israel"}, {"name": "Hamas"}])
    e2 = _event("E2", date="2026-05-16",
                participants=[{"name": "Israel"}, {"name": "Hamas"}])
    out = infer_event_temporal_edges([e1, e2])
    assert out == []


def test_temporal_chain_three_events_emits_three_edges():
    # Haddad killed May 16 -> Odeh killed May 26 -> Ouda appointed May 27
    # All share HAMAS. Should emit 3 pairwise edges (3 choose 2):
    # Haddad<->Odeh (10d followed_by), Haddad<->Ouda (11d followed_by),
    # Odeh<->Ouda (1d coincident).
    e1 = _event("HADDAD KILLED", event_type="military_action",
                date="2026-05-16",
                participants=[{"name": "Israel"}, {"name": "Hamas"}, {"name": "Haddad"}])
    e2 = _event("ODEH KILLED", event_type="military_action",
                date="2026-05-26",
                participants=[{"name": "Israel"}, {"name": "Hamas"}, {"name": "Odeh"}])
    e3 = _event("OUDA APPOINTED", event_type="other",
                date="2026-05-27",
                participants=[{"name": "Hamas"}, {"name": "Ouda"}])
    out = infer_event_temporal_edges([e1, e2, e3])
    types = [e["type"] for e in out]
    assert types.count("event_coincident") == 1
    assert types.count("event_followed_by") == 2


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
