"""CEP rule storage: the patterns the monitor watches for.

Persisted as ``rules.json`` next to the cumulative KG store (like the watchlist).
Seeded with a few built-in defaults keyed off the KG's clean event-type
categories (sanctions / financial_crime / indictment / military_action / …).
"""
from __future__ import annotations

import json
from pathlib import Path

from investigator.analytics import kg_store_dir

# Built-ins, expressed against the KG's event_type categories. Each is a
# chronological chain whose consecutive events must be linked (shared participant
# or a 1-hop KG bridge) within windowDays.
DEFAULT_RULES: list[dict] = [
    {
        "name": "sanctioned actor's network transacts",
        "windowDays": 60, "severity": "high",
        "steps": [
            {"types": ["sanctions"]},
            {"types": ["financial_crime", "corporate_action"],
             "keywords": ["transact", "transfer", "payment", "funnel", "launder"]},
        ],
    },
    {
        "name": "indictment followed by sanctions",
        "windowDays": 90, "severity": "medium",
        "steps": [{"types": ["indictment"]}, {"types": ["sanctions"]}],
    },
    {
        "name": "strike then diplomatic fallout",
        "windowDays": 30, "severity": "medium",
        "steps": [{"types": ["military_action"]}, {"types": ["diplomatic"]}],
    },
]


def rules_path() -> Path:
    return kg_store_dir() / "rules.json"


def validate_rule(d: dict) -> bool:
    if not isinstance(d, dict) or not (d.get("name") or "").strip():
        return False
    steps = d.get("steps")
    if not isinstance(steps, list) or not steps:
        return False
    for s in steps:
        if not isinstance(s, dict) or not ((s.get("types") or []) or (s.get("keywords") or [])):
            return False
    try:
        int(d.get("windowDays") or 30)
    except (TypeError, ValueError):
        return False
    return True


def load_rules(path: Path | None = None) -> list[dict]:
    """Load rules, seeding the defaults on first use (and persisting them)."""
    p = Path(path) if path else rules_path()
    if not p.exists():
        save_rules(DEFAULT_RULES, p)
        return list(DEFAULT_RULES)
    try:
        data = json.loads(p.read_text())
        rules = data.get("rules") if isinstance(data, dict) else data
        return [r for r in (rules or []) if validate_rule(r)]
    except Exception:  # noqa: BLE001 -- a corrupt file shouldn't break the monitor
        return list(DEFAULT_RULES)


def save_rules(rules: list[dict], path: Path | None = None) -> None:
    p = Path(path) if path else rules_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"rules": [r for r in rules if validate_rule(r)]},
                            ensure_ascii=False, indent=1))
