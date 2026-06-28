"""Intersection filter: keep only fresh events that touch the global graph.

Most daily news is noise. An event is worth scoring only if at least one of its
actors is an entity we already know -- i.e. it canonicalises (match-only, no
minting) to a name present in the cumulative KG. Watched entities are flagged so
the impact scorer can weight them. Everything else is dropped.
"""
from __future__ import annotations


def _event_participants(event_id: str, ev_node: dict, edges: list[dict]) -> list[str]:
    """Actor surface-names taking part in an event: the event_participation edges
    (event -> actor) plus any names on the event's own ``data.participants``."""
    names: list[str] = []
    for e in edges:
        if e.get("type") == "event_participation" and e.get("src_identifier") == event_id:
            dst = e.get("dst_identifier")
            if dst:
                names.append(dst)
    for p in ((ev_node.get("data") or {}).get("participants") or []):
        nm = p.get("name") if isinstance(p, dict) else p
        if nm:
            names.append(str(nm))
    # de-dupe, preserve order
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def intersect(day_graph: dict, registry, kg_entities, watchlist) -> list[dict]:
    """Filter a day's extracted graph to events that touch the KG.

    Args:
        day_graph: ``{"nodes": [...], "edges": [...]}`` from the engine -- the
            freshly extracted, canonicalised graph for today's news.
        registry: a ``CanonicalRegistry`` (uses ``.lookup`` -- match-only).
        kg_entities: a set/dict of canonical names present in the global KG.
        watchlist: a ``Watchlist`` (for the ``watched`` flag).

    Returns one record per surviving event::

        {"event": {"id", "date", "type", "description"},
         "touched": [canonical, ...],     # known KG entities the event hit
         "watched": [canonical, ...]}     # subset that is on the watchlist
    """
    nodes = day_graph.get("nodes") or []
    edges = day_graph.get("edges") or []
    known = set(kg_entities)
    # Resolve watchlist entries to their KG canonical (so a watched long-form that
    # is an alias of the canonical still matches the event's resolved actor).
    watched_canon = {registry.lookup(w) or w.strip().upper() for w in watchlist.entities}
    out = []
    for n in nodes:
        if (n.get("node_type") or n.get("type")) != "event":
            continue
        eid = n["identifier"]
        touched, seen = [], set()
        for actor in _event_participants(eid, n, edges):
            canon = registry.lookup(actor)
            if canon and canon in known and canon not in seen:
                seen.add(canon)
                touched.append(canon)
        if not touched:
            continue  # noise -- no known entity involved
        data = n.get("data") or {}
        out.append({
            "event": {
                "id": eid,
                "date": data.get("date") or "",
                "type": data.get("event_type") or "",
                "description": (data.get("description") or "")[:300],
                "confidence": data.get("confidence"),
            },
            "touched": touched,
            "watched": [c for c in touched if c in watched_canon],
        })
    return out
