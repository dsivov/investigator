"""CEP pattern matching: multi-event temporal patterns over the graph.

Phase-1 scores the impact of one fresh event. This detects *patterns* of events
over time -- e.g. "A sanctioned -> B linked to A -> C transacts with B within
30 days". The cumulative KG already has dated events with clean type categories,
canonical participants, and entity-entity edges, so a pattern is just a
chronological chain of typed events whose actors are connected.

A rule is an ordered list of steps + a window:

    {"name", "windowDays", "severity",
     "steps": [{"types": [...], "keywords": [...]}, ...]}

A step matches an event when ``event.type`` is in the step's types OR a keyword
is in the description. A rule matches when there's a chronological chain (one
event per step, in order) where each consecutive pair is within ``windowDays``
AND linked -- the events share a participant, or a participant of one is one hop
from a participant of the next in the KG. Pure / read-only.
"""
from __future__ import annotations

import datetime
import os

from investigator.graph.dedup import _parse_iso_date

_MAX_MATCHES = 200
# Linking two events through a high-degree hub (a country, a big agency) is
# spurious -- almost everything connects through such nodes. A meaningful "B
# linked to A" bridge is a specific actor, so we ignore hubs in the linkage test.
HUB_DEGREE = int(os.getenv("INVESTIGATOR_CEP_HUB_DEGREE", "25"))


def build_adjacency(structured) -> dict:
    """Entity -> set of 1-hop neighbours, from the structured-store edges."""
    adj: dict = {}
    for rec in structured.edges.values():
        s, d = rec.get("src"), rec.get("dst")
        if s and d and s != d:
            adj.setdefault(s, set()).add(d)
            adj.setdefault(d, set()).add(s)
    return adj


def events_from_store(structured) -> dict:
    """The sidecar's events in the matcher's shape ``{id: {dates, participants,
    type, description, id}}``."""
    return {eid: {"dates": e.get("dates"), "participants": e.get("participants"),
                  "type": e.get("type"), "description": e.get("description"), "id": eid}
            for eid, e in structured.events.items()}


def events_from_graph(nodes: list[dict], edges: list[dict]) -> dict:
    """Today's freshly-extracted graph (engine response) in the matcher's shape,
    so a fresh event can *complete* a pattern. Participants come from
    event_participation edges + the event's own data.participants."""
    parts: dict = {}
    for e in (edges or []):
        if e.get("type") == "event_participation":
            ev, actor = e.get("src_identifier"), e.get("dst_identifier")
            if ev and actor:
                parts.setdefault(ev, set()).add(actor)
    out: dict = {}
    for n in (nodes or []):
        if (n.get("node_type") or n.get("type")) != "event":
            continue
        eid = n["identifier"]
        d = n.get("data") or {}
        pp = set(parts.get(eid, set()))
        for p in (d.get("participants") or []):
            nm = p.get("name") if isinstance(p, dict) else p
            if nm:
                pp.add(str(nm))
        out[eid] = {"dates": d.get("date"), "participants": sorted(pp),
                    "type": d.get("event_type"), "description": d.get("description"), "id": eid}
    return out


def _day(dates) -> datetime.date | None:
    """Earliest day-precise date in a list, or None (year/month-only is skipped)."""
    best = None
    for s in (dates or []):
        p = _parse_iso_date(str(s))
        if not p or p[1] == 0 or p[2] == 0:
            continue
        try:
            d = datetime.date(p[0], p[1], p[2])
        except ValueError:
            continue
        if best is None or d < best:
            best = d
    return best


def _step_matches(step: dict, ev: dict) -> bool:
    types = {t.lower() for t in (step.get("types") or [])}
    if types and (ev.get("type") or "").lower() in types:
        return True
    kws = [k.lower() for k in (step.get("keywords") or [])]
    if kws:
        text = ((ev.get("description") or "") + " " + (ev.get("id") or "")).lower()
        if any(k in text for k in kws):
            return True
    return False


def _pair_bridges(a: dict, b: dict, adjacency: dict, hubs: set) -> set:
    """Bridging entities linking two events (empty = not linked): shared
    participants, or a participant of one that is a 1-hop KG neighbour of a
    participant of the other -- hub entities excluded (linking through a country
    or a big agency is not a meaningful connection)."""
    pa = {p for p in (a.get("participants") or []) if p not in hubs}
    pb = {p for p in (b.get("participants") or []) if p not in hubs}
    bridges = pa & pb                                   # shared specific actor
    for x in pa:                                        # 1-hop bridge (x in A, y in B, x-y edge)
        nb = (adjacency.get(x) or set()) & pb
        if nb:
            bridges.add(x)
            bridges |= nb
    return bridges


def match_rules(events: dict, adjacency: dict, rules: list[dict], *,
                watched=None, recent_since: str | None = None) -> list[dict]:
    """Find chronological event chains satisfying each rule.

    Args:
        events: ``{event_id: {dates, participants, type, description}}``.
        adjacency: ``{entity: set(neighbours)}`` (see ``build_adjacency``).
        rules: list of rule dicts.
        watched: optional set of canonical names -- a chain must involve >=1.
        recent_since: ISO date -- keep only chains whose FINAL event is on/after it
            (surfaces newly completed patterns, not the whole back-catalogue).

    Returns de-duped matches, most-recent first::

        {rule, severity, events:[{id,date,type}], bridges:[entity],
         span:{from,to,days}}
    """
    watched_set = set(watched or ())
    hubs = {e for e, nb in adjacency.items() if len(nb) > HUB_DEGREE}
    since = None
    if recent_since:
        p = _parse_iso_date(recent_since)
        if p and p[1] and p[2]:
            since = datetime.date(p[0], p[1], p[2])

    # Pre-compute each event's day + drop undated ones (a chain needs ordering).
    dated = []
    for eid, ev in events.items():
        d = _day(ev.get("dates"))
        if d is not None:
            dated.append((d, eid, ev))
    dated.sort(key=lambda t: t[0])

    out: list[dict] = []
    seen_chains: set = set()
    for rule in (rules or []):
        steps = rule.get("steps") or []
        if not steps:
            continue
        window = int(rule.get("windowDays") or 30)
        # candidate events per step (id, date, ev)
        per_step = [[(d, eid, ev) for (d, eid, ev) in dated if _step_matches(st, ev)]
                    for st in steps]
        if any(not c for c in per_step):
            continue

        def _extend(chain, bridges):
            i = len(chain)
            if i == len(steps):
                last_day = chain[-1][0]
                if since and last_day < since:
                    return
                ids = tuple(c[1] for c in chain)
                if ids in seen_chains:
                    return
                parts = set()
                for _, _, ev in chain:
                    parts |= set(ev.get("participants") or [])
                if watched_set and not (parts & watched_set):
                    return
                seen_chains.add(ids)
                shown = sorted((bridges & watched_set) or bridges)
                out.append({
                    "rule": rule.get("name") or "rule",
                    "severity": rule.get("severity") or "medium",
                    "events": [{"id": eid, "date": d.isoformat(), "type": ev.get("type") or ""}
                               for (d, eid, ev) in chain],
                    "bridges": shown[:8],
                    "span": {"from": chain[0][0].isoformat(), "to": chain[-1][0].isoformat(),
                             "days": (chain[-1][0] - chain[0][0]).days},
                })
                return
            prev = chain[-1] if chain else None
            for cand in per_step[i]:
                d, eid, ev = cand
                if prev is None:
                    _extend([cand], set())
                else:
                    gap = (d - prev[0]).days
                    if gap < 0 or gap > window or eid == prev[1]:
                        continue
                    pb = _pair_bridges(prev[2], ev, adjacency, hubs)
                    if not pb:
                        continue
                    _extend(chain + [cand], bridges | pb)
                if len(out) >= _MAX_MATCHES:
                    return

        for seed in per_step[0]:
            _extend([seed], set())
            if len(out) >= _MAX_MATCHES:
                break

    out.sort(key=lambda m: m["span"]["to"], reverse=True)
    return out
