"""Global cross-investigation canonicalization layer.

The cumulative KG (see :mod:`investigator.analytics.cumulative_kg`) merges nodes
by EXACT entity name, so the same real entity named differently across
investigations ("ACME FOUNDATION OF AMERICA" vs "THE ACME FOUNDATION OF
AMERICA") would create duplicate KG nodes. This resolver maps each incoming
entity name to a stable GLOBAL canonical -- persisted across investigations --
BEFORE the KG merge.

Conservative by design: cross-investigation merges are sticky (a wrong fusion
permanently merges two real entities in the shared KG and is hard to undo), so
only the highest-precision matches auto-merge. Tiers, highest precision first:

  1. exact (case-insensitive) match against a known canonical/alias  -> merge
  2. normalized-key match (case / punctuation / whitespace variants)  -> merge
  3. fuzzy (structural subset or WordLlama similarity >= review_low)   -> log a
     review candidate, but register the name as its OWN canonical (NO merge)
  4. otherwise: register a new canonical

A later (optionally LLM-assisted) adjudication pass clears the review log: the
auto-rules cannot tell "DEMOCRATIC PARTY" ~ "DEMOCRATS PARTY" (same) from
"JAMES COMER" ~ "JAMES COMEY" (different), so those decisions are deferred
rather than guessed.

NOTE (scale): the fuzzy tier scans all canonicals (O(N) WordLlama sims per
name) -- fine for now; back it with a vector index of canonicals for a large KG.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from investigator.graph.dedup import _find_alias_in_saved, _id_tokens, _wl

REVIEW_LOW = 0.82  # similarity band [REVIEW_LOW, 1.0) -> flag for review, do not merge


def _norm_key(name: str) -> str:
    """Collapse case / punctuation / whitespace so formatting-only variants match."""
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def _coerce_type(t) -> str:
    if isinstance(t, list):
        return str(t[0]) if t else "UNKNOWN"
    return str(t) if t else "UNKNOWN"


class CanonicalRegistry:
    """Persistent map of surface forms -> global canonical entity names."""

    def __init__(self, path, review_path=None, review_low: float = REVIEW_LOW):
        self.path = Path(path)
        self.review_path = (
            Path(review_path)
            if review_path
            else self.path.with_name("canonicalizer_review.jsonl")
        )
        self.review_low = review_low
        d = json.loads(self.path.read_text()) if self.path.exists() else {}
        # canonical -> {"aliases": [...], "type": str, "first_seen": str, "count": int}
        self.canonicals: dict = d.get("canonicals", {})
        self.stats = {"exact": 0, "normalized": 0, "review": 0, "new": 0}
        self._reindex()

    def _reindex(self):
        self.alias_index: dict = {}
        self.norm_index: dict = {}
        for c, m in self.canonicals.items():
            for surf in [c] + (m.get("aliases") or []):
                self.alias_index.setdefault(surf.upper(), c)
                self.norm_index.setdefault(_norm_key(surf), c)
        self._token_sets = {c: _id_tokens(c) for c in self.canonicals}

    def _best_similar(self, name: str) -> tuple[str | None, float]:
        best, best_sim = None, 0.0
        for c in self.canonicals:
            try:
                s = float(_wl.similarity(name, c))
            except Exception:  # noqa: BLE001
                continue
            if s > best_sim:
                best, best_sim = c, s
        return best, best_sim

    def _add_alias(self, canonical: str, surface: str):
        if surface.upper() == canonical.upper():
            return
        al = self.canonicals[canonical].setdefault("aliases", [])
        if surface not in al:
            al.append(surface)
            self.alias_index[surface.upper()] = canonical
            self.norm_index.setdefault(_norm_key(surface), canonical)

    def _register(self, name: str, entity_type, source) -> str:
        self.canonicals[name] = {
            "aliases": [],
            "type": _coerce_type(entity_type),
            "first_seen": source or "",
            "count": 1,
        }
        self.alias_index[name.upper()] = name
        self.norm_index.setdefault(_norm_key(name), name)
        self._token_sets[name] = _id_tokens(name)
        self.stats["new"] += 1
        return name

    def _touch(self, canonical: str, surface: str, source) -> str:
        self.canonicals[canonical]["count"] = self.canonicals[canonical].get("count", 0) + 1
        self._add_alias(canonical, surface)
        return canonical

    def _log_review(self, name, candidate, sim, source):
        self.stats["review"] += 1
        self.review_path.parent.mkdir(parents=True, exist_ok=True)
        with self.review_path.open("a") as fh:
            fh.write(
                json.dumps(
                    {
                        "name": name,
                        "candidate_canonical": candidate,
                        "similarity": round(sim, 3),
                        "source": source,
                        "ts": int(time.time()),
                    }
                )
                + "\n"
            )

    def lookup(self, name: str) -> str | None:
        """Match-only: return the existing canonical for ``name`` (exact or
        normalized), or None -- WITHOUT registering anything. Used by the monitor's
        intersection filter so noisy daily actors don't pollute the registry
        (``resolve`` always mints on a miss). Fuzzy is intentionally not consulted
        (sticky-fusion risk + cost)."""
        name = (name or "").strip()
        if not name:
            return None
        c = self.alias_index.get(name.upper())
        if c:
            return c
        nk = _norm_key(name)
        return self.norm_index.get(nk) if nk else None

    def resolve(self, name: str, entity_type=None, source=None) -> str:
        """Map a surface form to its global canonical, auto-merging only safe
        (exact/normalized) matches and flagging fuzzy ones for review."""
        name = (name or "").strip()
        if not name:
            return name
        u = name.upper()
        # 1. exact canonical / alias
        if u in self.alias_index:
            self.stats["exact"] += 1
            return self._touch(self.alias_index[u], name, source)
        # 2. normalized-key (case / punctuation / whitespace variants)
        nk = _norm_key(name)
        if nk and nk in self.norm_index:
            self.stats["normalized"] += 1
            return self._touch(self.norm_index[nk], name, source)
        # 3. Fuzzy -> do NOT auto-merge (sticky-fusion risk). The structural
        # subset rule would fuse "TEL AVIV DISTRICT COURT" into "TEL AVIV", or a
        # UN sub-body / named attorney-general into the generic parent. Flag a
        # review candidate; the name still registers as its own canonical.
        cand = _find_alias_in_saved(name, list(self.canonicals), self._token_sets)
        if cand is None:
            best, sim = self._best_similar(name)
            cand = best if (best and sim >= self.review_low) else None
        if cand is not None:
            try:
                sim = float(_wl.similarity(name, cand))
            except Exception:  # noqa: BLE001
                sim = 0.0
            self._log_review(name, cand, sim, source)
        # 4. register as a new canonical (no auto-merge on fuzzy)
        return self._register(name, entity_type, source)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"canonicals": self.canonicals}, ensure_ascii=False, indent=1)
        )


def resolve_graph_entities(final_graph: dict, registry: CanonicalRegistry, source: str) -> dict:
    """Resolve every non-event entity name in a graph to its global canonical.

    Returns ``{original_name: canonical_name}``. Events are skipped (they are
    per-investigation and never shared across the cumulative KG).
    """
    mapping = {}
    for n in final_graph["nodes"]:
        if (n.get("node_type") or n.get("type")) == "event":
            continue
        ident = n["identifier"]
        mapping[ident] = registry.resolve(ident, (n.get("data") or {}).get("type"), source)
    return mapping
