"""The watchlist: the monitor's scoping primitive.

A user-defined set of canonical entity names (and an optional free-text domain
query) that the daily job watches -- the noise filter that keeps monitoring from
running over the whole KG. Persisted as ``watchlist.json`` next to the cumulative
KG store, so the engine, UI, and the monitor all see the same list.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from investigator.analytics import kg_store_dir


def watchlist_path() -> Path:
    return kg_store_dir() / "watchlist.json"


@dataclass
class Watchlist:
    """Watched canonical entity names + an optional domain query.

    ``entities`` are upper-cased canonical names (matching the KG / registry);
    ``domain`` is an optional broad query used to pull topical news beyond the
    named subjects. ``path`` is where it persists.
    """
    entities: list[str] = field(default_factory=list)
    domain: str = ""
    path: Path = field(default_factory=watchlist_path)

    # --- editing -----------------------------------------------------------

    def add(self, name: str) -> bool:
        c = (name or "").strip().upper()
        if c and c not in self.entities:
            self.entities.append(c)
            return True
        return False

    def remove(self, name: str) -> bool:
        c = (name or "").strip().upper()
        if c in self.entities:
            self.entities.remove(c)
            return True
        return False

    def has(self, name: str) -> bool:
        return (name or "").strip().upper() in self.entities

    # --- subjects to fetch news for ---------------------------------------

    def subjects(self) -> list[str]:
        """Queries the daily intake fetches news for: every watched entity, plus
        the domain query if set."""
        subs = list(self.entities)
        if self.domain.strip():
            subs.append(self.domain.strip())
        return subs

    # --- persistence -------------------------------------------------------

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"entities": self.entities, "domain": self.domain}, ensure_ascii=False, indent=1))

    def to_dict(self) -> dict:
        return {"entities": self.entities, "domain": self.domain}


def load_watchlist(path: Path | None = None) -> Watchlist:
    """Load the watchlist (empty if the file doesn't exist yet)."""
    p = Path(path) if path else watchlist_path()
    if not p.exists():
        return Watchlist(path=p)
    d = json.loads(p.read_text())
    ents = [str(e).strip().upper() for e in (d.get("entities") or []) if str(e).strip()]
    return Watchlist(entities=ents, domain=str(d.get("domain") or ""), path=p)
