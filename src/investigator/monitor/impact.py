"""The impact model: how a fresh event ripples through the global graph.

A new event is fresh evidence on the entities it touches. We scope the affected
neighbourhood around those entities (direct + indirect reach), treat the event as
raising the touched entities' belief, and run the SAME junction-tree belief
propagation we use per-investigation over a SMALL LOCAL TMFG -- so the posterior
*deltas* across the neighbourhood are the impact, decaying with distance by
construction. Belief-shift (BP) + topological reach (the local scope) combined,
exactly the doc's lean.

Pure / read-only: reads the cumulative KG's structured graph, never mutates it.
"""
from __future__ import annotations

import os

import networkx as nx

from investigator.graph.tmfg import construct_tmfg
from investigator.graph.junction_tree import propagate

IMPACT_RADIUS = int(os.getenv("INVESTIGATOR_MONITOR_RADIUS", "2"))   # hops of reach
MAX_LOCAL = int(os.getenv("INVESTIGATOR_MONITOR_MAXLOCAL", "60"))    # cap local subgraph
RECENCY_HALFLIFE_DAYS = float(os.getenv("INVESTIGATOR_MONITOR_HALFLIFE", "30"))
WEIGHT_CAP = float(os.getenv("INVESTIGATOR_MONITOR_WEIGHTCAP", "5"))  # weight->[0,1] squash
WATCH_BOOST = float(os.getenv("INVESTIGATOR_MONITOR_WATCHBOOST", "1.5"))
BETA = float(os.getenv("INVESTIGATOR_MONITOR_BETA", "0.4"))          # BP coupling (low = discriminating)
_TOUCHED_PRIOR = 0.95   # the new event makes the touched entity (near-)certain


def global_graph_dicts(structured) -> tuple[list[dict], list[dict]]:
    """The cumulative KG as plain node/edge dicts (relationship edges only), read
    from the ``StructuredStore`` sidecar -- entities carry prob/posterior; edges
    carry weight/observed_dates/active_window. Reading the sidecar directly avoids
    booting LightRAG for a read-only monitor pass."""
    nodes = [{"id": name, "prob": rec.get("prob"), "posterior": rec.get("posterior_prob"),
              "type": "entity"}
             for name, rec in structured.entities.items()]
    edges = []
    for rec in structured.edges.values():
        if not (rec.get("src") and rec.get("dst")) or rec["src"] == rec["dst"]:
            continue
        # The KG sidecar doesn't carry a numeric weight; use attestation breadth
        # (distinct citing sources / investigations) as the link strength, floored
        # at 1 so every attested edge couples in belief propagation.
        attest = max(len(rec.get("sources") or []), len(rec.get("investigations") or []), 1)
        edges.append({"source": rec["src"], "target": rec["dst"],
                      "weight": float(rec.get("weight") or attest)})
    return nodes, edges


def _norm_w(w) -> float:
    """Edge weight (attestation count, unbounded) -> [0,1] for the BP Ising factor."""
    try:
        w = float(w)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, w / WEIGHT_CAP)) if WEIGHT_CAP > 0 else max(0.0, min(1.0, w))


def recency_decay(event_date: str, today: str, *, halflife_days: float = RECENCY_HALFLIFE_DAYS) -> float:
    """0..1 weight: 1 for a fresh event, halving every ``halflife_days``. Undated
    events get a neutral 0.5 (we can't date their freshness)."""
    from investigator.graph.dedup import _parse_iso_date
    import datetime
    pe, pt = _parse_iso_date(event_date or ""), _parse_iso_date(today or "")
    if not pe or not pt or pe[1] == 0 or pt[1] == 0:
        return 0.5
    try:
        de = datetime.date(pe[0], pe[1], pe[2] or 1)
        dt = datetime.date(pt[0], pt[1], pt[2] or 1)
    except ValueError:
        return 0.5
    age = abs((dt - de).days)
    return 0.5 ** (age / halflife_days) if halflife_days > 0 else 1.0


def _ego(g: nx.Graph, seeds: list[str], radius: int) -> set:
    """Union of the ``radius``-hop neighbourhoods of the seed nodes."""
    keep: set = set()
    for s in seeds:
        if g.has_node(s):
            keep |= set(nx.single_source_shortest_path_length(g, s, cutoff=radius))
    return keep


def impact_of_event(kg_nodes: list[dict], kg_edges: list[dict], touched: list[str], *,
                    event_strength: float = 1.0, event_date: str = "", today: str = "",
                    watched=None, radius: int = IMPACT_RADIUS, beta: float = BETA) -> dict:
    """Score the ripple of one event (touching ``touched`` entities) on the graph.

    Returns ``{"touched", "impacted": [{entity, delta, hops, isBroker, watched,
    score}], "usedBP": bool}`` -- impacted nodes ranked by score (the blended
    belief-shift × reach × recency × watch-relevance). Falls back to a topological
    score when the local neighbourhood is too small for a TMFG (< 4 nodes).
    """
    watched_set = set(watched or ())
    is_watched = watched_set.__contains__
    g = nx.Graph()
    prob = {}
    for n in kg_nodes:
        g.add_node(n["id"])
        prob[n["id"]] = n.get("posterior") if n.get("posterior") is not None else \
            (n.get("prob") if n.get("prob") is not None else 0.5)
    for e in kg_edges:
        s, t = e.get("source"), e.get("target")
        if s in g and t in g and s != t:
            g.add_edge(s, t, weight=_norm_w(e.get("weight")))

    seeds = [c for c in touched if g.has_node(c)]
    if not seeds:
        return {"touched": touched, "impacted": [], "usedBP": False}

    hops = {}
    for s in seeds:
        for n, d in nx.single_source_shortest_path_length(g, s, cutoff=radius).items():
            hops[n] = min(hops.get(n, d), d)
    keep = set(hops)
    # Hubs blow the ego-graph up to hundreds of nodes and the ripple washes out;
    # focus on the nearest neighbourhood (seeds + closest MAX_LOCAL nodes).
    if len(keep) > MAX_LOCAL:
        nearest = sorted((n for n in keep if n not in seeds),
                         key=lambda n: (hops[n], -g.degree(n)))[:MAX_LOCAL]
        keep = set(seeds) | set(nearest)
    sub = g.subgraph(keep).copy()
    rec = recency_decay(event_date, today)

    # Belief shift: raise touched entities' prior, propagate over the local TMFG.
    deltas, used_bp = {}, False
    if sub.number_of_nodes() >= 4 and sub.number_of_edges() >= 6:
        try:
            tmfg = construct_tmfg(sub, weight_attr="weight")
            if tmfg.tetrahedra:
                priors = dict(prob)
                for s in seeds:
                    priors[s] = _TOUCHED_PRIOR
                bp = propagate(tmfg, {n: priors.get(n, 0.5) for n in tmfg.graph.nodes()}, beta=beta)
                deltas = bp.delta
                used_bp = True
        except Exception:  # noqa: BLE001 -- BP must never break the digest
            deltas, used_bp = {}, False

    try:
        bet = nx.betweenness_centrality(sub) if sub.number_of_nodes() > 2 else {}
    except Exception:  # noqa: BLE001
        bet = {}
    top_bet = max(bet.values()) if bet else 0.0

    impacted = []
    for n in keep:
        if n in seeds:
            continue
        delta = float(deltas.get(n, 0.0))
        h = hops.get(n, radius)
        proximity = 0.7 ** h                       # decay with distance (topological reach)
        # belief-shift × reach: distance always discounts, so near + strongly-moved
        # nodes rank above far ones even when BP saturates.
        magnitude = (abs(delta) if used_bp else 1.0) * proximity
        score = magnitude * event_strength * rec * (WATCH_BOOST if is_watched(n) else 1.0)
        impacted.append({
            "entity": n,
            "delta": round(delta, 4),
            "hops": h,
            "isBroker": bool(top_bet) and bet.get(n, 0.0) >= 0.5 * top_bet,
            "watched": is_watched(n),
            "score": round(score, 4),
        })
    impacted.sort(key=lambda x: x["score"], reverse=True)
    return {"touched": touched, "impacted": impacted, "usedBP": used_bp}
