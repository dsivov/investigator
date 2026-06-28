"""Assemble + persist the dated impact digest, and the one-shot orchestrator.

`run_once` is the whole daily loop: intake -> intersect -> impact per event ->
ranked, thresholded digest written to ``news_investigations/monitor/``. Reads the
KG sidecar + registry directly (no LightRAG boot) since the monitor is read-only.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

from investigator.analytics import kg_store_dir
from investigator.analytics.canonicalizer import CanonicalRegistry
from investigator.analytics.structured_store import StructuredStore
from investigator.graph.temporal_consistency import date_spread_conflict
from investigator.monitor import impact as _impact
from investigator.monitor.intake import daily_intake
from investigator.monitor.intersect import intersect

ALERT_THRESHOLD = float(os.getenv("INVESTIGATOR_MONITOR_ALERT", "0.2"))
DIGEST_DIR = Path("news_investigations/monitor")
_MAX_IMPACTED = 12   # impacted nodes kept per event in the digest


def _event_strength(ev: dict) -> float:
    """A coarse 0..1 strength for the event from its extraction confidence."""
    try:
        return max(0.1, min(1.0, float(ev.get("confidence"))))
    except (TypeError, ValueError):
        return 0.7


def build_digest(intersected: list[dict], structured, registry, watchlist, *,
                 today: str, kg_nodes=None, kg_edges=None,
                 alert_threshold: float = ALERT_THRESHOLD) -> dict:
    """Score every intersecting event's ripple and assemble a ranked digest."""
    if kg_nodes is None or kg_edges is None:
        kg_nodes, kg_edges = _impact.global_graph_dicts(structured)
    watched_canon = {registry.lookup(w) or w.strip().upper() for w in watchlist.entities}
    events_out = []
    for rec in intersected:
        ev = rec["event"]
        res = _impact.impact_of_event(
            kg_nodes, kg_edges, rec["touched"],
            event_strength=_event_strength(ev),
            event_date=ev.get("date") or "", today=today, watched=watched_canon)
        impacted = res["impacted"][:_MAX_IMPACTED]
        top = impacted[0]["score"] if impacted else 0.0
        conflict = date_spread_conflict([ev["date"]] if isinstance(ev.get("date"), str) else (ev.get("date") or []))
        events_out.append({
            "event": ev,
            "touched": rec["touched"],
            "watched": rec["watched"],
            "topScore": round(top, 4),
            "usedBP": res["usedBP"],
            "dateConflict": conflict,
            "impacted": impacted,
            "alert": top >= alert_threshold,
        })
    events_out.sort(key=lambda e: e["topScore"], reverse=True)
    return {
        "date": today,
        "watchlist": watchlist.to_dict(),
        "alertThreshold": alert_threshold,
        "events": events_out,
        "alerts": [e for e in events_out if e["alert"]],
        "counts": {"events": len(events_out),
                   "alerts": sum(1 for e in events_out if e["alert"])},
    }


def save_digest(digest: dict, *, out_dir: Path = DIGEST_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"digest_{digest['date']}.json"
    path.write_text(json.dumps(digest, ensure_ascii=False, indent=1))
    return path


def list_digests(out_dir: Path = DIGEST_DIR) -> list[str]:
    if not out_dir.exists():
        return []
    return sorted((p.stem.removeprefix("digest_") for p in out_dir.glob("digest_*.json")),
                  reverse=True)


def load_digest(date: str, out_dir: Path = DIGEST_DIR) -> dict | None:
    p = out_dir / f"digest_{date}.json"
    return json.loads(p.read_text()) if p.exists() else None


def run_once(watchlist, *, k: int = 8, period: str = "1d", today: str | None = None,
             domain: str = "general", base_url: str = None, fetch_fn=None, extract_fn=None,
             save: bool = True) -> dict:
    """The full daily loop. Returns the digest dict (and writes it when ``save``)."""
    today = today or datetime.date.today().isoformat()
    store = kg_store_dir()
    structured = StructuredStore(store / "structured_store.json")
    registry = CanonicalRegistry(store / "canonical_registry.json")
    kg_entities = set(structured.entities)

    intake_kwargs = {"k": k, "period": period, "domain": domain}
    if base_url:
        intake_kwargs["base_url"] = base_url
    if fetch_fn:
        intake_kwargs["fetch_fn"] = fetch_fn
    if extract_fn:
        intake_kwargs["extract_fn"] = extract_fn
    day = daily_intake(watchlist, **intake_kwargs)

    intersected = intersect(day, registry, kg_entities, watchlist)
    digest = build_digest(intersected, structured, registry, watchlist, today=today)
    digest["intake"] = {"subjects": day["subjects"],
                        "articles": len(day["articles"]),
                        "extractedNodes": len(day["nodes"]),
                        "intersectedEvents": len(intersected)}
    if save:
        digest["savedTo"] = str(save_digest(digest))
    return digest
