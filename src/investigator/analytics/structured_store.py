"""Sidecar structured store for the cumulative KG.

LightRAG's knowledge graph keeps only a fixed schema (entity name/type/
description/source, edge weight/keywords/description/source). Every other
structured property we construct per investigation -- belief scores (prob,
score, posterior), evidence (reasoning + confidence + strength + source URLs),
labels, cross-investigation provenance (runs), themes, relation type/context,
hypothesis flags -- would be lost on merge.

This store preserves all of it, keyed by the SAME canonical entity names the KG
uses (so it joins one-to-one with LightRAG's semantic retrieval), and merges
records as the same entity/edge recurs across investigations:

  * scalar beliefs (prob, score, posterior_prob) -> keep the max seen, plus a
    per-investigation breakdown,
  * list-valued props (labels, runs, themes, sources, evidence) -> deduped union,
  * data (position/location/...) -> first non-empty wins,
  * provenance (which investigations attested it) -> accumulated.

Persisted as a single JSON next to the LightRAG store.
"""
from __future__ import annotations

import json
from pathlib import Path

_EMPTY = {"", "not found", "unknown", "n/a", "none"}


def _clean(v) -> str:
    if isinstance(v, (list, tuple)):
        v = v[0] if v else ""
    s = str(v or "").strip()
    return "" if s.lower() in _EMPTY else s


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _edge_key(src: str, dst: str) -> str:
    a, b = sorted((src, dst))
    return a + "\t" + b


class StructuredStore:
    """All structured node/edge properties, merged across investigations."""

    def __init__(self, path):
        self.path = Path(path)
        d = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.entities: dict = d.get("entities", {})
        self.edges: dict = d.get("edges", {})

    # --- merge -------------------------------------------------------------

    def merge_entity(self, name: str, node: dict, source_id: str) -> None:
        rec = self.entities.setdefault(name, {
            "name": name, "types": [], "labels": [], "runs": [], "themes": [],
            "sources": [], "investigations": [], "data": {},
            "evidence": [], "beliefs": {},
        })
        data = node.get("data") or {}
        t = data.get("type")
        for tv in (t if isinstance(t, list) else [t]):
            tv = _clean(tv)
            if tv and tv not in rec["types"]:
                rec["types"].append(tv)
        for lab in (node.get("most_significant_labels") or []) + (node.get("labels") or []):
            lab = _clean(lab)
            if lab and lab.upper() != name.upper() and lab not in rec["labels"]:
                rec["labels"].append(lab)
        self._union(rec["runs"], node.get("runs"))
        self._union(rec["themes"], [t.get("label") if isinstance(t, dict) else t
                                    for t in (node.get("themes") or [])])
        src = _clean(node.get("source"))
        if src and src not in rec["sources"]:
            rec["sources"].append(src)
        if source_id not in rec["investigations"]:
            rec["investigations"].append(source_id)
        for k in ("position", "location", "address"):
            v = _clean(data.get(k))
            if v and not rec["data"].get(k):
                rec["data"][k] = v
        belief = rec["beliefs"].setdefault(source_id, {})
        for k in ("prob", "score", "posterior_prob"):
            val = _num(node.get(k))
            if val is not None:
                belief[k] = val
                rec[k] = max(val, rec.get(k, val))
        self._merge_evidence(rec, node.get("evidence"), source_id)
        rec["evidence_count"] = len(rec["evidence"])

    def merge_edge(self, src: str, dst: str, edge: dict, source_id: str) -> None:
        rec = self.edges.setdefault(_edge_key(src, dst), {
            "src": src, "dst": dst, "relations": [], "sources": [],
            "runs": [], "investigations": [], "is_hypothesis": False, "weight": 0.0,
        })
        rec["src"], rec["dst"] = src, dst
        rel = edge.get("relations")
        if isinstance(rel, str):
            try:
                rel = json.loads(rel)
            except ValueError:
                rel = {}
        if isinstance(rel, dict):
            entry = {"type": _clean(rel.get("type")) or _clean(edge.get("type")),
                     "context": _clean(rel.get("context"))}
            if entry not in rec["relations"] and (entry["type"] or entry["context"]):
                rec["relations"].append(entry)
        for u in (_clean(edge.get("search_url")), _clean(edge.get("source"))):
            if u.startswith("http") and u not in rec["sources"]:
                rec["sources"].append(u)
        self._union(rec["runs"], edge.get("runs"))
        if source_id not in rec["investigations"]:
            rec["investigations"].append(source_id)
        rec["is_hypothesis"] = bool(rec["is_hypothesis"] or edge.get("is_hypothesis"))
        w = _num(edge.get("weight"))
        if w is not None:
            rec["weight"] = max(rec["weight"], w)

    @staticmethod
    def _union(target: list, src) -> None:
        for v in (src or []):
            v = _clean(v)
            if v and v not in target:
                target.append(v)

    @staticmethod
    def _merge_evidence(rec: dict, evidence, source_id: str) -> None:
        seen = {(e.get("reasoning", ""), e.get("source", "")) for e in rec["evidence"]}
        for ev in (evidence or []):
            if not isinstance(ev, dict):
                continue
            meta = ev.get("metadata") or {}
            item = {
                "reasoning": _clean(ev.get("reasoning"))[:600],
                "confidence": _num(ev.get("confidence")),
                "strength": _num(ev.get("strength")),
                "supports": bool(ev.get("hypothesis", True)),
                "source": _clean(ev.get("doc_id") or meta.get("source")),
                "investigation": source_id,
            }
            key = (item["reasoning"], item["source"])
            if item["reasoning"] and key not in seen:
                seen.add(key)
                rec["evidence"].append(item)

    # --- access / persist --------------------------------------------------

    def get_entity(self, name: str):
        return self.entities.get(name)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"entities": self.entities, "edges": self.edges}, ensure_ascii=False))
