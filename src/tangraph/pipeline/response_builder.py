"""Build the network-analysis sections of the API response (Phase 3).

Turns TMFG + Phase-2 BP outputs into the JSON shape the investigator's app
consumes (see research/investigator_response_format.md). Backwards-compatible:
everything here is layered on top of the existing nodes/edges arrays --
clients that ignore it keep working.

Top-level sections:
  * ``themes``            — TMFG tetrahedra surfaced as candidate
                            investigation themes, ranked by total internal
                            edge weight (heaviest first).
  * ``promoted_entities`` — entities whose BP posterior moved >=
                            PROMOTION_THRESHOLD over their evidence prior,
                            with a structural reason naming their highest-weight
                            clique-mates.
  * ``hypothesis_edges``  — TMFG fill-in edges (pairs that share a 4-clique
                            but are not directly attested in the source text)
                            ranked by joint BP posterior.
  * ``runs_in_session``   — sorted list of distinct run labels (from the
                            per-entity ``runs`` provenance). Empty when no
                            POST has carried a ``run`` field.
  * ``bridging_entities`` — entities whose ``runs`` span >= 2 distinct run
                            labels. Sorted by n_runs desc, posterior desc.
                            These are the structural backbone of any
                            cross-run connection claim.
  * ``cross_event_leads`` — single-run-each entity pairs that share a
                            triangle through a bridging entity. For pair
                            (A, B) where A is only in run R_A and B only
                            in R_B (R_A != R_B), a lead fires when there
                            exists C with: C is a bridging_entity AND C has
                            ATTESTED edges (not TMFG fill-ins, not
                            event_participation) to both A and B. Score is
                            sum of bridges' posteriors. Differs from
                            ``hypothesis_edges`` in that the connection is
                            attested-via-shared-bridge, not TMFG-fill-in.

Plus per-node ``themes`` (list of tetrahedron indices), per-edge
``is_hypothesis`` (true for TMFG fill-ins, false for LLM-attested edges),
and on themes + hypothesis_edges an ``is_cross_investigation`` flag that
fires whenever the members/endpoints' combined runs list spans >= 2
distinct run labels.
"""

from __future__ import annotations

from typing import Iterable

from tangraph.graph.junction_tree import BeliefPropagationResult
from tangraph.graph.tmfg import TMFGResult, tetrahedron_weight


PROMOTION_THRESHOLD = 0.10        # delta >= this -> "promoted" by network analysis
HYPOTHESIS_TOP_K = 30             # cap the hypothesis-edges list size in the response


def build_network_analysis_payload(
    tmfg: TMFGResult,
    bp: BeliefPropagationResult,
    entities: list[dict],
    edges: list[dict],
) -> dict:
    """Compose the network-analysis sections of the API response.

    Cross-run flags + bridging-entities + runs_in_session are derived from
    each entity's ``runs`` list (the per-record provenance the orchestrator
    stamps at extraction time and merge_run_into_saved unions across alias
    matches). Entities without ``runs`` are treated as belonging to a single
    implicit run; the cross_investigation flag fires only when a theme /
    hypothesis-edge's members span >= 2 *distinct* run labels.

    Parameters
    ----------
    tmfg : TMFGResult
        Output of ``construct_tmfg``. Carries the tetrahedra (themes) and the
        ``fill_in_edges`` set used to flag hypothesis pairs.
    bp : BeliefPropagationResult
        Output of ``propagate``. Provides posterior + delta per entity.
    entities : list[dict]
        The merged-entities list (what the response's ``nodes`` will mirror).
        Mutated in place to add ``themes`` (tetra ids).
        ``posterior_prob`` / ``posterior_delta`` are expected to already be
        on the entity from the Phase-2 block.
    edges : list[dict]
        The enriched edges (the response's ``edges``). Mutated in place to add
        ``is_hypothesis`` (always False for these LLM-attested edges; the
        hypothesis pairs go into the top-level ``hypothesis_edges`` section).

    Returns
    -------
    {
        "themes": [...],
        "promoted_entities": [...],
        "hypothesis_edges": [...],
        "runs_in_session": [...],
        "bridging_entities": [...],
    }
    """
    # --- Per-entity runs provenance ----------------------------------------
    # Build identifier -> set(runs). Entities without a `runs` field
    # contribute an empty set (legacy / single-run flow). all_runs collects
    # every distinct run label that touched any entity.
    entity_runs: dict[str, set] = {}
    all_runs: set = set()
    for n in entities:
        r_list = n.get("runs") or []
        s = set(r_list)
        entity_runs[n.get("identifier")] = s
        all_runs |= s

    def _runs_spanned(identifiers: Iterable[str]) -> set:
        out: set = set()
        for ident in identifiers:
            out |= entity_runs.get(ident, set())
        return out

    # --- Membership index: entity id -> list of tetra ids it belongs to ----
    membership: dict[str, list[int]] = {}
    for tid, members in enumerate(tmfg.tetrahedra):
        for m in members:
            membership.setdefault(m, []).append(tid)

    # Attach per-entity themes (in place). Skipped silently for nodes not in
    # any tetra (e.g. isolates).
    for n in entities:
        ident = n.get("identifier")
        if ident in membership:
            n["themes"] = membership[ident]

    # Tag LLM-attested edges as non-hypothesis. (The hypothesis stubs go into
    # the top-level hypothesis_edges section -- we don't inject them into the
    # edges array to avoid breaking clients that don't filter by is_hypothesis.)
    for e in edges:
        e.setdefault("is_hypothesis", False)

    # --- themes ------------------------------------------------------------
    themes_payload = []
    for tid, members in enumerate(tmfg.tetrahedra):
        mlist = sorted(members)
        w = tetrahedron_weight(tmfg.graph, members)
        post_vals = [bp.posterior.get(m, 0.5) for m in mlist]
        post_mean = sum(post_vals) / len(post_vals) if post_vals else 0.0
        spans = _runs_spanned(mlist)
        theme_dict = {
            "id": tid,
            "members": mlist,
            "weight": round(w, 3),
            "posterior_mean": round(post_mean, 3),
            "is_cross_investigation": len(spans) >= 2,
        }
        if spans:
            theme_dict["runs_spanned"] = sorted(spans)
        themes_payload.append(theme_dict)
    themes_payload.sort(key=lambda t: -t["weight"])

    # --- promoted_entities -------------------------------------------------
    promoted_payload = []
    for ident, delta in bp.delta.items():
        if delta < PROMOTION_THRESHOLD:
            continue
        # Pick the heaviest clique this entity belongs to -- the structural
        # "promoter".
        tids = membership.get(ident, [])
        if not tids:
            continue
        best_tid = max(tids, key=lambda i: tetrahedron_weight(tmfg.graph, tmfg.tetrahedra[i]))
        mates = sorted(m for m in tmfg.tetrahedra[best_tid] if m != ident)
        promoter_lines = ", ".join(f"{m} (posterior={bp.posterior.get(m, 0.5):.2f})" for m in mates)
        promoted_payload.append({
            "identifier": ident,
            "prior": round(bp.prior.get(ident, 0.5), 3),
            "posterior": round(bp.posterior[ident], 3),
            "delta": round(delta, 3),
            "promoted_by_theme": best_tid,
            "reason": (
                f"Network position contradicts the moderate per-entity evidence: "
                f"clique-mates in theme #{best_tid} -- {promoter_lines} -- are all at high confidence."
            ),
        })
    promoted_payload.sort(key=lambda p: -p["delta"])

    # --- hypothesis_edges --------------------------------------------------
    hypothesis_payload = []
    for pair in tmfg.fill_in_edges:
        u, v = tuple(pair)
        # Find a tetrahedron containing both endpoints (any will do for a
        # rationale; pick the heaviest if multiple).
        candidates = [i for i, members in enumerate(tmfg.tetrahedra) if u in members and v in members]
        via_tid = max(candidates, key=lambda i: tetrahedron_weight(tmfg.graph, tmfg.tetrahedra[i])) if candidates else None
        pu = bp.posterior.get(u, 0.5)
        pv = bp.posterior.get(v, 0.5)
        spans = _runs_spanned([u, v])
        hyp_dict = {
            "endpoints": sorted([u, v]),
            "joint_confidence": round(pu * pv, 3),
            "via_theme": via_tid,
            "is_cross_investigation": len(spans) >= 2,
            "rationale": (
                f"Co-located in theme #{via_tid} but not directly attested in the source text; "
                f"likely worth examining together (joint confidence {pu*pv:.2f})."
                if via_tid is not None
                else "Pair appears in TMFG but no shared clique was identified."
            ),
        }
        if spans:
            hyp_dict["runs_spanned"] = sorted(spans)
        hypothesis_payload.append(hyp_dict)
    hypothesis_payload.sort(key=lambda h: -h["joint_confidence"])
    hypothesis_payload = hypothesis_payload[:HYPOTHESIS_TOP_K]

    # --- bridging_entities + runs_in_session -------------------------------
    bridging_payload = []
    bridge_idents: set = set()
    for n in entities:
        ident = n.get("identifier")
        runs_set = entity_runs.get(ident, set())
        if len(runs_set) < 2:
            continue
        bridging_payload.append({
            "identifier": ident,
            "runs": sorted(runs_set),
            "n_runs": len(runs_set),
            "posterior_prob": round(bp.posterior.get(ident, 0.5), 3),
            "score": float(n.get("score") or 0.0),
        })
        bridge_idents.add(ident)
    bridging_payload.sort(key=lambda b: (-b["n_runs"], -b["posterior_prob"], b["identifier"]))

    # --- cross_event_leads -------------------------------------------------
    # For each bridge C, the neighbors (via ATTESTED edges, not hypothesis-
    # fill-in, not event_participation synthesis) form candidate triangle
    # endpoints. A pair (A, B) of C's neighbors qualifies as a cross-event
    # lead iff:
    #   - A is in exactly one run R_A, B is in exactly one run R_B, R_A != R_B
    #   - both A and B are NEIGHBOURS of C via attested edges
    # Multiple bridges between the same (A, B) sum into the score.
    cross_event_leads = []
    if bridge_idents and len(all_runs) >= 2:
        # Build adjacency from attested edges only. Skip TMFG fill-ins
        # (is_hypothesis True), synthetic participant edges
        # (type=event_participation), and synthetic event-event temporal
        # edges (type=event_followed_by, type=event_coincident) -- all of
        # those represent structure / synthesis, not source-attested
        # relationships between actors.
        SYNTHESIZED = {"event_participation", "event_followed_by", "event_coincident",
                       "claimed_caused_by"}
        adj: dict[str, set] = {}
        for e in edges:
            if e.get("is_hypothesis"):
                continue
            if e.get("type") in SYNTHESIZED:
                continue
            s, t = e.get("src_identifier"), e.get("dst_identifier")
            if not (s and t) or s == t:
                continue
            adj.setdefault(s, set()).add(t)
            adj.setdefault(t, set()).add(s)

        # For pair-deduplication and bridge accumulation
        leads_acc: dict[tuple[str, str], dict] = {}
        for bridge in bridging_payload:
            c_id = bridge["identifier"]
            c_post = float(bridge["posterior_prob"])
            neighbours = sorted(adj.get(c_id, set()))
            # Restrict to neighbours that are in EXACTLY one run (single-run
            # endpoint) so the lead actually crosses events
            single_run_neigh = []
            for nb in neighbours:
                runs_nb = entity_runs.get(nb, set())
                if len(runs_nb) == 1:
                    single_run_neigh.append((nb, next(iter(runs_nb))))
            # Now enumerate pairs that span different runs
            for i in range(len(single_run_neigh)):
                a, ra = single_run_neigh[i]
                for j in range(i + 1, len(single_run_neigh)):
                    b, rb = single_run_neigh[j]
                    if ra == rb:
                        continue
                    pair_key = tuple(sorted([a, b]))
                    rec = leads_acc.setdefault(pair_key, {
                        "endpoints": list(pair_key),
                        "bridges": [],
                        "runs_spanned": set(),
                        "score": 0.0,
                    })
                    if c_id not in rec["bridges"]:
                        rec["bridges"].append(c_id)
                        rec["score"] += c_post
                    rec["runs_spanned"].update({ra, rb})

        for pair_key, rec in leads_acc.items():
            cross_event_leads.append({
                "endpoints": rec["endpoints"],
                "bridges": rec["bridges"],
                "runs_spanned": sorted(rec["runs_spanned"]),
                "score": round(rec["score"], 3),
                "rationale": (
                    f"`{rec['endpoints'][0]}` (in run `{sorted(entity_runs.get(rec['endpoints'][0], []))[0]}`) and "
                    f"`{rec['endpoints'][1]}` (in run `{sorted(entity_runs.get(rec['endpoints'][1], []))[0]}`) "
                    f"are not directly attested together, but each has an attested edge to the cross-run bridge "
                    f"{('entity' if len(rec['bridges']) == 1 else 'entities')} "
                    f"{', '.join('`'+b+'`' for b in rec['bridges'])}."
                ),
            })
        cross_event_leads.sort(key=lambda r: (-r["score"], -len(r["bridges"]), r["endpoints"][0]))

    return {
        "themes": themes_payload,
        "promoted_entities": promoted_payload,
        "hypothesis_edges": hypothesis_payload,
        "runs_in_session": sorted(all_runs),
        "bridging_entities": bridging_payload,
        "cross_event_leads": cross_event_leads,
    }
