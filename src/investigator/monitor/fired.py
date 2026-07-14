"""Fired-pattern state: remember which CEP chains have already been alerted.

Without this, a daily digest re-alerts every previously matched chain each
run -- the single biggest obstacle to using the monitor as a standing watch.
Identity is the collapsed :func:`~investigator.monitor.patterns.chain_signature`
(rule + final event), so alternate chains for an already-alerted story do not
re-fire, but a NEW completing event does.

Persisted as ``fired_patterns.json`` next to the cumulative KG store (like the
watchlist and the rule library). Read-modify-write is process-local; the digest
is the only writer and runs one at a time.
"""
from __future__ import annotations

import json
from pathlib import Path

from investigator.analytics import kg_store_dir
from investigator.monitor.patterns import chain_signature


def fired_path() -> Path:
    return kg_store_dir() / "fired_patterns.json"


def load_fired(path: Path | None = None) -> dict[str, str]:
    """``{chain signature: ISO date first alerted}``."""
    p = Path(path) if path else fired_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return dict(data.get("fired") or {}) if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 -- corrupt state must not break the digest
        return {}


def save_fired(fired: dict[str, str], path: Path | None = None) -> None:
    p = Path(path) if path else fired_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"fired": fired}, ensure_ascii=False, indent=1))


def split_new(matches: list[dict], fired: dict[str, str]) -> tuple[list[dict], list[dict]]:
    """Partition collapsed matches into (new, previously fired). Previously
    fired matches carry ``firedAt`` so a UI can still show them as history."""
    new: list[dict] = []
    old: list[dict] = []
    for m in matches:
        at = fired.get(chain_signature(m))
        if at is None:
            new.append(m)
        else:
            m = dict(m)
            m["firedAt"] = at
            old.append(m)
    return new, old


def mark_fired(matches: list[dict], today: str,
               fired: dict[str, str], path: Path | None = None) -> dict[str, str]:
    """Record ``matches`` as alerted on ``today`` and persist. Returns the
    updated map."""
    for m in matches:
        fired.setdefault(chain_signature(m), today)
    save_fired(fired, path)
    return fired
