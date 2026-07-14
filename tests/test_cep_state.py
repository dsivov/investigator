"""CEP chain collapsing + fired-pattern state (monitor phase 2).

Standalone: PYTHONPATH=.:src python tests/test_cep_state.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from investigator.monitor.fired import load_fired, mark_fired, split_new
from investigator.monitor.patterns import chain_signature, collapse_matches


def _match(rule, final_id, *, first_id="E0", bridges=(), days=10):
    return {
        "rule": rule, "severity": "high",
        "events": [{"id": first_id, "date": "2026-07-01", "type": "sanctions"},
                   {"id": final_id, "date": "2026-07-05", "type": "financial_crime"}],
        "bridges": list(bridges),
        "span": {"from": "2026-07-01", "to": "2026-07-05", "days": days},
    }


def test_collapse():
    # Three chains reaching the same final event = one story; the strongest
    # linkage (most bridges) wins and alternates are counted.
    ms = [
        _match("r1", "EF", first_id="A", bridges=("X",)),
        _match("r1", "EF", first_id="B", bridges=("X", "Y")),
        _match("r1", "EF", first_id="C", bridges=()),
        _match("r1", "EG", first_id="A", bridges=("X",)),   # different final event
        _match("r2", "EF", first_id="A", bridges=("X",)),   # different rule
    ]
    out = collapse_matches(ms)
    assert len(out) == 3, out
    story = next(m for m in out if chain_signature(m) == "r1::EF")
    assert story["alternateChains"] == 2
    assert story["events"][0]["id"] == "B", "strongest-linked chain must win"
    assert all("alternateChains" in m for m in out)
    print("collapse: OK")


def test_fired_state():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fired.json"
        m1, m2 = _match("r1", "EF"), _match("r1", "EG")
        fired = load_fired(p)
        assert fired == {}
        new, old = split_new([m1, m2], fired)
        assert len(new) == 2 and not old
        mark_fired(new, "2026-07-12", fired, p)

        # Second run: same stories -> nothing new; a fresh completing event fires.
        fired2 = load_fired(p)
        m3 = _match("r1", "EH")
        new2, old2 = split_new([m1, m2, m3], fired2)
        assert [chain_signature(m) for m in new2] == ["r1::EH"], new2
        assert len(old2) == 2 and all(m["firedAt"] == "2026-07-12" for m in old2)

        # An ALTERNATE chain for an already-fired story must not re-fire.
        alt = _match("r1", "EF", first_id="Z", bridges=("Q",))
        new3, _ = split_new([alt], fired2)
        assert not new3, "alternate chain of a fired story re-fired"
    print("fired state: OK")


def test_corrupt_state_is_safe():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "fired.json"
        p.write_text("{not json")
        assert load_fired(p) == {}
    print("corrupt state: OK")


if __name__ == "__main__":
    test_collapse()
    test_fired_state()
    test_corrupt_state_is_safe()
    print("ALL OK")
    sys.exit(0)
