"""Graph construction, scoring, and triangulation.

``build_graph`` is the core of the triangulation pipeline (coarse-grained
affiliation graph); ``score_graph_by_connectivity`` is the triangulation pass
that drops low-relevance / disconnected nodes and orphan edges.
"""

from __future__ import annotations

import math
import os
import uuid

import networkx as nx

from investigator.logging import get_logger

log = get_logger()

# Multi-source corroboration (standard fact-checking): independent sources that
# attest the SAME conclusion sharpen confidence toward certainty. The signal
# magnitude is multiplied by ``1 + CORRO_GAIN * log2(min(n_distinct_sources,
# CORRO_CAP))``, counting distinct sources only on the winning (net) side. One
# source -> factor 1.0 (no change). Tunable via env.
CORRO_GAIN = float(os.getenv("INVESTIGATOR_CORRO_GAIN", "0.35"))
CORRO_CAP = int(os.getenv("INVESTIGATOR_CORRO_CAP", "8"))


# --- Filtering & scoring ---------------------------------------------------


def filter_nodes_by_score(nodes: list[dict], score_threshold: float) -> list[dict]:
    filtered_nodes = [node for node in nodes if node.get("relevance_score", 0) >= score_threshold]
    log.info(
        f"Filtered nodes: {len(filtered_nodes)} out of {len(nodes)} using threshold {score_threshold}"
    )
    return filtered_nodes


def evidence_probability(evidences: list[dict]) -> float:
    """Confidence-weighted, signed probability that the hypothesis holds for an
    entity given its evidence, in ``[0, 1]`` (``0.5`` = net-neutral).

    Each evidence votes: ``hypothesis=True`` supports (+), ``False`` contradicts
    (−); ``strength`` is the magnitude and ``confidence`` is the weight::

        signal = Σ(sign·strength·confidence) / Σ confidence      # [−1, 1]
        prob   = (signal + 1) / 2                                 # [0, 1]

    Replaces the previous t-test scorer, which (TRIANGULATION_REVIEW F10):
      * cancelled out ``confidence`` (it divided it straight back out),
      * dropped contradicting evidence via a ``score > 0`` filter,
      * floored every result at 0.5 (``(x+1)/2`` over positives-only),
      * gated on a one-sample t-test against 0 over all-positive products — a
        foregone conclusion that did no real filtering.

    Contradiction-ready: with only supporting evidence (today's prompts) it
    returns ``≥ 0.5``; once extraction emits contradicting evidence the sign
    pulls it below 0.5. Returns ``0.0`` for no / zero-confidence evidence
    (i.e. "no credible evidence").

    Multi-source corroboration: after the signed average, the magnitude is
    sharpened toward certainty when independent sources attest the same
    conclusion (see ``CORRO_GAIN``/``CORRO_CAP``). Distinct sources are counted
    only on the winning side, and only when they carry a usable magnitude, so a
    single source (or unattributed evidence) leaves the result unchanged.
    """
    return assess_evidence(evidences)[0]


def assess_evidence(evidences: list[dict]) -> tuple[float, int]:
    """Like :func:`evidence_probability`, but also return the number of distinct
    corroborating sources -- the independent sources on the winning (net) side
    that drove the confidence boost. ``(prob, n_sources)``; ``(0.0, 0)`` for no
    credible evidence. The count is uncapped (true distinct sources) even though
    the boost itself saturates at ``CORRO_CAP``."""
    weighted_sum = 0.0
    total_confidence = 0.0
    pos_sources: set[str] = set()
    neg_sources: set[str] = set()
    for e in evidences:
        confidence = e.get("confidence") or 0.0
        if confidence <= 0:
            continue
        magnitude = e.get("strength")
        if not isinstance(magnitude, (int, float)):
            magnitude = 0.0   # no usable magnitude -> contribute no signal
        supports = bool(e.get("hypothesis", True))
        sign = 1.0 if supports else -1.0
        weighted_sum += sign * magnitude * confidence
        total_confidence += confidence
        # Track distinct corroborating sources per side. Same source counted
        # once (independence); zero-magnitude items don't corroborate anything.
        src = _evidence_source_key(e)
        if src and magnitude > 0:
            (pos_sources if supports else neg_sources).add(src)
    if total_confidence == 0:
        return 0.0, 0
    signal = weighted_sum / total_confidence
    winning = pos_sources if signal >= 0 else neg_sources
    n_src = len(winning)
    capped = min(n_src, CORRO_CAP)
    if capped > 1:
        factor = 1.0 + CORRO_GAIN * math.log2(capped)
        signal = max(-1.0, min(1.0, signal * factor))
    return (signal + 1.0) / 2.0, n_src


def corroboration_tier(n_sources: int) -> str:
    """Fact-checking strength label from distinct corroborating source count:
    >=3 strong, 2 moderate, otherwise weak (one or no identifiable source)."""
    if n_sources >= 3:
        return "strong"
    if n_sources == 2:
        return "moderate"
    return "weak"


def _evidence_source_key(e: dict) -> str | None:
    """Stable distinct-source key for corroboration counting: the source/doc
    identifier, else the first related link. None when no provenance is known
    (such evidence cannot count as independent corroboration)."""
    md = e.get("metadata") or {}
    key = (e.get("doc_id") or md.get("source") or "").strip()
    if not key:
        links = md.get("related_links") or []
        key = (links[0].strip() if links and isinstance(links[0], str) else "")
    return key.lower() or None


# --- Graph construction ----------------------------------------------------


# Symmetric (mutual) affiliation types are direction-agnostic, so A<->B is a
# single undirected edge; directional types keep the asserted A->B
# (TRIANGULATION_REVIEW §3 F9).
_SYMMETRIC_AFFILIATIONS = {"affiliation", "partnership", "coalition", "non_direct"}


def build_graph(entity_groups, working_state, representatives=None):
    log.info("Building coarse-grained graph from chunk affiliations...")
    chunks = working_state.get("chunks", [])
    graph = nx.DiGraph()

    # Resolve raw affiliation names -> canonical (representative) id. Built from
    # the entity records (identifier + labels) AND the representative groups
    # (relevant_identifiers): affiliation endpoints often use a variant name that
    # only the representative grouping knows, so without it ~13% of endpoints look
    # like phantoms when only ~1% truly are (TRIANGULATION_REVIEW F7).
    raw_to_canonical: dict = {}
    for record in entity_groups:
        rep = record["representative_identifier"].upper()
        raw_to_canonical[record["identifier"].upper()] = rep
        for label in record.get("labels", []):
            raw_to_canonical[label.upper()] = rep
    for rep in representatives or []:
        canon = rep["identifier"].upper()
        for variant in rep.get("relevant_identifiers", []):
            raw_to_canonical.setdefault(variant.upper(), canon)

    # One edge per (canonicalised) pair; merge distinct relation types into a
    # label list (F8). Endpoints that don't resolve to an extracted entity are
    # dropped (F7/decision) rather than kept as phantom nodes.
    # Aggregate per-pair across chunks:
    #   * `chunks`        — distinct chunk uuids attesting the pair (source_count)
    #   * `strengths`     — per-chunk affiliation strength values
    #   * `confidences`   — per-chunk affiliation confidence values
    # Symmetric pairs accumulate across either direction (we sort the endpoints
    # before keying); directional pairs do not, by design.
    pair_edges: dict = {}
    for chunk in chunks:
        for affiliation in chunk.get("affiliations") or []:
            src = raw_to_canonical.get(affiliation["entityA"].upper())
            dst = raw_to_canonical.get(affiliation["entityB"].upper())
            if src is None or dst is None or src == dst:
                continue
            rel = affiliation["affiliation_type"]
            a, b = tuple(sorted((src, dst))) if rel in _SYMMETRIC_AFFILIATIONS else (src, dst)
            entry = pair_edges.setdefault(
                (a, b),
                {"chunk_id": chunk["uuid"], "chunks": set(), "labels": set(),
                 "strengths": [], "confidences": []},
            )
            entry["chunks"].add(chunk["uuid"])
            entry["labels"].add(rel)
            s = affiliation.get("strength")
            c = affiliation.get("confidence")
            if isinstance(s, (int, float)):
                entry["strengths"].append(float(s))
            if isinstance(c, (int, float)):
                entry["confidences"].append(float(c))

    def _mean(xs):
        return sum(xs) / len(xs) if xs else 0.0

    coarse_grained_edges = []
    for (a, b), entry in pair_edges.items():
        coarse_grained_edges.append({"nodes": (a, b), "chunk_id": entry["chunk_id"]})
        mean_strength = _mean(entry["strengths"])
        mean_confidence = _mean(entry["confidences"])
        graph.add_edge(
            a, b,
            chunk_id=entry["chunk_id"],
            label=sorted(entry["labels"]),
            source_count=len(entry["chunks"]),
            mean_strength=mean_strength,
            mean_confidence=mean_confidence,
            # TMFG-ready scalar weight: support × confidence × corroboration.
            # Falls back gracefully to source_count when the LLM hasn't emitted
            # strength/confidence (legacy prompt or pre-Phase-0 fixture).
            weight=(mean_strength * mean_confidence) if (mean_strength and mean_confidence) else float(len(entry["chunks"])),
        )

    degrees = dict(graph.degree())
    top_degrees = sorted(degrees.items(), key=lambda x: x[1], reverse=True)
    most_connected_node = max(degrees, key=degrees.get) if degrees else None
    highest_degrees = [n for n, d in top_degrees if d > 1]
    lowest_degrees = [n for n, d in top_degrees if d <= 1]

    log.info(
        f"Coarse affiliation graph: {graph.number_of_nodes()} nodes, "
        f"{graph.number_of_edges()} edges; root={most_connected_node}"
        + (f" (degree {degrees[most_connected_node]})" if most_connected_node is not None else "")
    )
    return coarse_grained_edges, most_connected_node, highest_degrees, lowest_degrees, graph


_RELEVANCE_DECAY = 0.7        # relevance = _RELEVANCE_DECAY ** hops_to_root
_EVIDENCE_HOP_COST = 2        # an evidence-only link to root costs this many hops


def score_graph_by_connectivity(investigator, edges_enrichment_results, merged_entities, root=None,
                                 keep_low_prob_evidenced: bool = False):
    """Triangulation (TRIANGULATION_REVIEW §4): keep entities with **credible
    evidence**, score them by proximity to root, and ensure every survivor is
    connected to root.

    * Survival = credible evidence (``prob > 0``). No evidence → dropped.
      ``relevance`` is **not** the gate (the old relevance-threshold filter
      dropped ~80% of evidenced entities because the LLM relevance was noisy).
      ``root`` (the investigation's main entity) is always kept as the anchor.

    ``keep_low_prob_evidenced`` (research mode, off by default): when True,
    keep every entity that has *any* evidence regardless of prob sign --
    including entities the consolidator considered cleared (prob ≤ 0). This
    is what Phase-2 BP needs to see the "cleared" pole; production runs use
    the default (drop prob ≤ 0).
    * ``relevance_score = _RELEVANCE_DECAY ** hops_to_root`` over the affiliation
      graph; an evidenced entity with no affiliation path to root pays
      ``_EVIDENCE_HOP_COST`` (Decision B).
    * ``score = relevance_score × prob`` (Decision C).
    * Orphan edges (an endpoint dropped) are removed; any survivor left
      unconnected to root is wired to it with a typed ``evidence`` edge (G8: the
      output is fully connected to root).
    """
    undirected = investigator.to_undirected()
    if root is None and undirected.number_of_nodes():
        deg = dict(undirected.degree())
        root = max(deg, key=deg.get)
    hops = (
        nx.single_source_shortest_path_length(undirected, root)
        if root is not None and undirected.has_node(root)
        else {}
    )

    survivors = []
    for node in merged_entities:
        ident = node["identifier"]
        is_root = ident == root
        is_event = node.get("type") == "event"
        # Events do not flow through evidence extraction / the consolidator,
        # so they will never have `evidence` or `prob`. Skip those gates for
        # them -- their survival is governed by extraction (the Event NER
        # decided this is an event), not by per-entity evidence records.
        # Disconnection in the affiliation graph is acceptable for events;
        # the G8 wiring below attaches any survivor to root via a synthetic
        # evidence edge, so events stay reachable in the final graph.
        if not is_root and not is_event and not node.get("evidence"):
            continue   # no evidence -> dropped under either mode
        if not is_root and not is_event and not keep_low_prob_evidenced and node.get("prob", 0) <= 0:
            continue   # standard mode: survival = credible evidence (prob > 0)
        if is_root:
            relevance, node["score"] = 1.0, 1.0
        elif is_event:
            # Score events by hops_to_root if reachable, else hop-cost; their
            # `prob` is taken from data.confidence (the LLM's event-extraction
            # confidence) when not otherwise set.
            relevance = _RELEVANCE_DECAY ** hops.get(ident, _EVIDENCE_HOP_COST)
            ev_conf = float((node.get("data") or {}).get("confidence") or 0.0)
            if not node.get("prob"):
                node["prob"] = ev_conf
            node["score"] = relevance * (node.get("prob") or ev_conf)
        else:
            relevance = _RELEVANCE_DECAY ** hops.get(ident, _EVIDENCE_HOP_COST)
            node["score"] = relevance * node.get("prob", 0.0)
        node.setdefault("data", {})["relevance_score"] = relevance
        survivors.append(node)

    survivor_ids = {n["identifier"] for n in survivors}
    uid_of = {n["identifier"]: n.get("unique_identifier", "") for n in survivors}

    # drop orphan edges (an endpoint didn't survive)
    kept_edges = [
        e
        for e in edges_enrichment_results
        if e.get("src_identifier") in survivor_ids and e.get("dst_identifier") in survivor_ids
    ]

    # G8: every survivor must be connected to root; wire the rest via an evidence edge
    if root in survivor_ids:
        out_graph = nx.Graph()
        out_graph.add_nodes_from(survivor_ids)
        out_graph.add_edges_from((e["src_identifier"], e["dst_identifier"]) for e in kept_edges)
        reachable = set(nx.single_source_shortest_path_length(out_graph, root))
        for ident in survivor_ids - reachable:
            kept_edges.append(
                {
                    "unique_identifier": str(uuid.uuid4()),
                    "src_identifier": ident,
                    "dst_identifier": root,
                    "src_unique_identifier": uid_of.get(ident, ""),
                    "dst_unique_identifier": uid_of.get(root, ""),
                    "type": "evidence",
                    "relations": "[]",
                    "attributes": {},
                    "metadata": {},
                    "source": "evidence",
                }
            )

    return survivors, kept_edges, list(survivor_ids)
