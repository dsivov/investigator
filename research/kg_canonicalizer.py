"""Global cross-investigation canonicalization layer.

LightRAG merges KG nodes by EXACT entity name, so the same real entity named
differently across investigations ("ACME FOUNDATION OF AMERICA" vs "THE ACME
FOUNDATION OF AMERICA") would create duplicate KG nodes. This resolver maps each
incoming entity name to a stable GLOBAL canonical -- persisted across
investigations -- BEFORE the KG insert, reusing the same conservative alias
rules we trust inside one investigation (structural subset min-2 tokens, or
WordLlama similarity + Jaccard overlap).

Conservative by design: cross-investigation merges are sticky (a wrong fusion
permanently merges two real entities in the shared KG and is hard to undo), so
borderline near-matches are NOT auto-merged -- they are written to a review log
and registered as new canonicals. Tiers, highest precision first:

  1. exact (case-insensitive) match against a known canonical/alias
  2. normalized-key match (case / punctuation / whitespace variants)
  3. conservative alias rules (_find_alias_in_saved)
  4. review band [review_low, ALIAS_SIM_THRESHOLD): log, do NOT merge
  5. otherwise: register a new canonical

NOTE (scale): tier-3/4 scan all canonicals (O(N) WordLlama sims per name) --
fine for a prototype; back it with a vector index of canonicals for a large KG.

Usage (validation):
    PYTHONPATH=.:src python research/kg_canonicalizer.py <artifactA.json> <artifactB.json>
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from investigator.graph.dedup import (
    ALIAS_SIM_THRESHOLD,
    _find_alias_in_saved,
    _id_tokens,
    _wl,
)

_REVIEW_LOW = 0.82  # similarity band [low, ALIAS_SIM_THRESHOLD) -> flag for review


def _norm_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (name or "").upper())


def _coerce_type(t) -> str:
    if isinstance(t, list):
        return str(t[0]) if t else "UNKNOWN"
    return str(t) if t else "UNKNOWN"


class CanonicalRegistry:
    """Persistent map of surface forms -> global canonical entity names."""

    def __init__(self, path, review_path=None, review_low: float = _REVIEW_LOW):
        self.path = Path(path)
        self.review_path = Path(review_path) if review_path else self.path.with_name(
            "canonicalizer_review.jsonl")
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
            "aliases": [], "type": _coerce_type(entity_type),
            "first_seen": source or "", "count": 1,
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
        with self.review_path.open("a") as fh:
            fh.write(json.dumps({
                "name": name, "candidate_canonical": candidate,
                "similarity": round(sim, 3), "source": source,
                "ts": int(time.time()),
            }) + "\n")

    def resolve(self, name: str, entity_type=None, source=None) -> str:
        name = name.strip()
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
        # 3. Fuzzy match -> do NOT auto-merge. Cross-investigation fusions are
        # sticky and the structural-subset rule over-merges here (it would fuse
        # "TEL AVIV DISTRICT COURT" into "TEL AVIV", or a UN sub-body / a named
        # attorney-general into the generic parent). So any subset / similarity
        # match only FLAGS a review candidate; the name still registers as its
        # own canonical. A later (LLM-assisted) adjudication pass clears the log.
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
        self.path.write_text(json.dumps({"canonicals": self.canonicals}, ensure_ascii=False, indent=1))


def resolve_graph_entities(final_graph: dict, registry: CanonicalRegistry, source: str) -> dict:
    """Resolve every entity (non-event) name in a graph to its global canonical.
    Returns {original_name: canonical_name}."""
    mapping = {}
    for n in final_graph["nodes"]:
        if (n.get("node_type") or n.get("type")) == "event":
            continue
        ident = n["identifier"]
        mapping[ident] = registry.resolve(
            ident, (n.get("data") or {}).get("type"), source)
    return mapping


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _self_test():
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    r = CanonicalRegistry(tmp / "reg.json", review_path=tmp / "rev.jsonl")
    base = r.resolve("ACME FOUNDATION OF AMERICA")
    # case-insensitive EXACT -> auto-merge
    assert r.resolve("acme foundation of america") == base
    # normalized-key (whitespace / punctuation) -> auto-merge
    r.resolve("SEO HEE CONSTRUCTION")
    assert r.resolve("SEOHEE CONSTRUCTION") == "SEO HEE CONSTRUCTION"
    assert r.resolve("U.S.") == r.resolve("US")
    # subset / fuzzy -> NOT auto-merged (sticky-merge risk); flagged, own canonical
    assert r.resolve("THE ACME FOUNDATION OF AMERICA") != base
    assert r.resolve("HAMAS PROXIES IN GAZA") != r.resolve("HAMAS")
    assert r.stats["review"] >= 1  # the subset/fuzzy case was flagged, not fused
    print("self-test: PASS (exact/normalized auto-merge; subset & fuzzy flagged, not fused)")


def _validate_on_artifacts(a: Path, b: Path):
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    reg = CanonicalRegistry(tmp / "reg.json", review_path=tmp / "review.jsonl")
    ga = json.loads(a.read_text())["final_merged_graph"]
    gb = json.loads(b.read_text())["final_merged_graph"]
    # Register investigation A, then resolve investigation B against it.
    resolve_graph_entities(ga, reg, f"inv::{a.stem}")
    before = dict(reg.stats)
    mapping_b = resolve_graph_entities(gb, reg, f"inv::{b.stem}")
    # alias/normalized hits in B that mapped to a DIFFERENT surface = the
    # cross-investigation unifications this layer adds beyond exact-name merge.
    cross = [(k, v) for k, v in mapping_b.items() if k != v]
    print(f"\nA registered {len(ga['nodes'])} nodes; resolving B ({len(gb['nodes'])} nodes)")
    print(f"B resolution stats (delta): "
          f"exact={reg.stats['exact']-before['exact']} "
          f"normalized={reg.stats['normalized']-before['normalized']} "
          f"review-flagged={reg.stats['review']-before['review']} "
          f"new={reg.stats['new']-before['new']}")
    print(f"auto cross-investigation unifications (exact/normalized): {len(cross)}")
    for k, v in cross[:10]:
        print(f"   {k!r}  ->  {v!r}")
    if reg.review_path.exists():
        lines = reg.review_path.read_text().splitlines()
        print(f"\nreview log: {len(lines)} borderline pair(s) flagged (NOT merged):")
        for ln in lines[:6]:
            d = json.loads(ln)
            print(f"   {d['name']!r} ~ {d['candidate_canonical']!r}  sim={d['similarity']}")


if __name__ == "__main__":
    _self_test()
    if len(sys.argv) == 3:
        _validate_on_artifacts(Path(sys.argv[1]), Path(sys.argv[2]))
