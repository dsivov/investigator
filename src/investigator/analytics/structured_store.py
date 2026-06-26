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


_DATE_RE = __import__("re").compile(r"\d{4}-\d{2}-\d{2}")


def _dates(v) -> list[str]:
    """Coerce a date field (str / list / None) to a sorted list of ISO dates."""
    out = []
    for item in (v if isinstance(v, (list, tuple)) else [v]):
        s = str(item or "").strip()
        m = _DATE_RE.search(s)
        if m and m.group(0) not in out:
            out.append(m.group(0))
    return sorted(out)


class StructuredStore:
    """All structured node/edge properties, merged across investigations -- plus
    a temporal layer (per-entity timelines, dated events, event ordering) that
    LightRAG drops entirely when it strips events on merge."""

    def __init__(self, path):
        self.path = Path(path)
        d = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.entities: dict = d.get("entities", {})
        self.edges: dict = d.get("edges", {})
        # Temporal layer.
        self.events: dict = d.get("events", {})            # event_id -> {dates, participants, ...}
        self.temporal_edges: list = d.get("temporal_edges", [])  # event->event ordering

    # --- merge -------------------------------------------------------------

    def merge_entity(self, name: str, node: dict, source_id: str) -> None:
        rec = self.entities.setdefault(name, {
            "name": name, "types": [], "labels": [], "runs": [], "themes": [],
            "sources": [], "investigations": [], "data": {},
            "evidence": [], "beliefs": {}, "timeline": [],
        })
        rec.setdefault("timeline", [])
        data = node.get("data") or {}
        # Per-actor dated mini-timeline (TimelineEvent: {date, event}).
        seen_tl = {(t.get("date"), t.get("event")) for t in rec["timeline"]}
        for te in (data.get("timeline_events") or []):
            if not isinstance(te, dict):
                continue
            ev = _clean(te.get("event"))
            ds = _dates(te.get("date"))
            entry = {"date": ds[0] if ds else "", "event": ev[:300]}
            key = (entry["date"], entry["event"])
            if ev and key not in seen_tl:
                seen_tl.add(key)
                rec["timeline"].append(entry)
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
        # High-signal structured attributes (no chunks => captured here or lost).
        for k in ("position", "location", "address", "email", "phone_number",
                  "financial_restrictions"):
            v = _clean(data.get(k))
            if v and not rec["data"].get(k):
                rec["data"][k] = v
        belief = rec["beliefs"].setdefault(source_id, {})
        for k in ("prob", "score", "posterior_prob", "posterior_delta"):
            val = _num(node.get(k))
            if val is not None:
                belief[k] = val
                if k == "posterior_delta":
                    # keep the largest-magnitude shift seen (the impact signal)
                    if abs(val) > abs(rec.get(k) or 0.0):
                        rec[k] = val
                else:
                    rec[k] = max(val, rec.get(k, val))
        self._merge_evidence(rec, node.get("evidence"), source_id)
        rec["evidence_count"] = len(rec["evidence"])

    def merge_edge(self, src: str, dst: str, edge: dict, source_id: str) -> None:
        rec = self.edges.setdefault(_edge_key(src, dst), {
            "src": src, "dst": dst, "relations": [], "roles": [], "sources": [],
            "runs": [], "investigations": [], "is_hypothesis": False, "weight": 0.0,
        })
        rec.setdefault("roles", [])
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
        # Edge attributes: role (nature of the link) + per-edge citation.
        attrs = edge.get("attributes") or {}
        role = _clean(attrs.get("role"))
        if role and role not in rec["roles"]:
            rec["roles"].append(role)
        for u in (_clean(edge.get("search_url")), _clean(edge.get("source")),
                  _clean(attrs.get("source_url"))):
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

    # --- temporal layer ----------------------------------------------------

    def merge_event(self, event_id: str, event_node: dict, participants: list[str],
                    source_id: str) -> None:
        """A dated event (stripped from the LightRAG graph) + the canonical
        actors that took part. Keyed by event identifier, merged across runs."""
        rec = self.events.setdefault(event_id, {
            "id": event_id, "dates": [], "participants": [], "type": "",
            "runs": [], "investigations": [],
        })
        data = event_node.get("data") or {}
        for ds in _dates(data.get("date")):
            if ds not in rec["dates"]:
                rec["dates"].append(ds)
        rec["dates"].sort()
        if not rec["type"]:
            rec["type"] = _clean(data.get("event_type"))
        self._union(rec["participants"], participants)
        self._union(rec["runs"], event_node.get("runs"))
        if source_id not in rec["investigations"]:
            rec["investigations"].append(source_id)

    def merge_temporal_edge(self, etype: str, src: str, dst: str) -> None:
        """An event->event ordering edge (event_followed_by / event_coincident)."""
        key = {"type": etype, "src": src, "dst": dst}
        if key not in self.temporal_edges:
            self.temporal_edges.append(key)

    # --- access / persist --------------------------------------------------

    def get_entity(self, name: str):
        return self.entities.get(name)

    def entity_timeline(self, name: str) -> list[dict]:
        """An actor's chronology: their own timeline_events PLUS the dated events
        they participated in, deduped and sorted by date (undated last)."""
        rec = self.entities.get(name)
        if not rec:
            return []
        out, seen = [], set()
        for te in rec.get("timeline", []):
            k = (te.get("date", ""), te.get("event", ""))
            if k not in seen:
                seen.add(k)
                out.append({"date": te.get("date", ""), "event": te.get("event", ""),
                            "kind": "timeline"})
        for ev in self.events.values():
            if name in (ev.get("participants") or []):
                date = (ev.get("dates") or [""])[0]
                label = ev["id"]
                k = (date, label)
                if k not in seen:
                    seen.add(k)
                    out.append({"date": date, "event": label, "kind": "event"})
        # dated first (chronological), undated appended after
        return sorted(out, key=lambda x: (x["date"] == "", x["date"]))

    def date_range(self, name: str) -> tuple[str, str]:
        ds = sorted(t["date"] for t in self.entity_timeline(name) if t.get("date"))
        return (ds[0], ds[-1]) if ds else ("", "")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"entities": self.entities, "edges": self.edges,
             "events": self.events, "temporal_edges": self.temporal_edges},
            ensure_ascii=False))
