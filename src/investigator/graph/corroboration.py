"""Claim-level corroboration (read-time postprocessing).

Counts how many INDEPENDENT sources confirm the SAME claim about an entity --
real fact-checking, vs. merely counting sources that mention the entity (which
``operations.assess_evidence`` does for the confidence boost). Used by the UI
payload builder, so it works on any artifact's evidence list without re-running
the pipeline.

Steps:
  1. Keep credible evidence on the dominant (net) polarity that has a source and
     a claim text.
  2. Cluster claims by WordLlama cosine >= ``claim_sim`` (same fact, different
     wording -- exact match would miss paraphrases).
  3. Per cluster count distinct sources, collapsing near-identical text across
     sources (cosine >= ``syndication_sim``) into one: republished wire copy is
     not independent corroboration.
  4. Strength = the best-corroborated single claim (max independent sources):
     1 -> weak, 2 -> moderate, 3+ -> strong.
"""
from __future__ import annotations

import os

import numpy as np

from investigator.graph.dedup import _wl
from investigator.graph.operations import corroboration_tier

CLAIM_SIM = float(os.getenv("INVESTIGATOR_CLAIM_SIM", "0.78"))
SYNDICATION_SIM = float(os.getenv("INVESTIGATOR_SYNDICATION_SIM", "0.97"))
_MAX_POOL = 300   # bound the O(n^2) similarity work for very large evidence sets


def _claim_text(e: dict) -> str:
    reasoning = str(e.get("reasoning") or "")
    # The consolidator prepends a routing line ("Evidence through affiliations
    # A->B.\n") to supporting evidence. It is identical across an entity's routed
    # evidence, so it would artificially cluster distinct facts -- strip it.
    if reasoning.startswith("Evidence through affiliations"):
        nl = reasoning.find("\n")
        reasoning = reasoning[nl + 1:] if nl != -1 else ""
    parts = [reasoning]
    quotes = e.get("evidence") or []
    if isinstance(quotes, list):
        parts += [str(q) for q in quotes]
    return " ".join(p.strip() for p in parts if p and p.strip()).strip()


def _source_key(e: dict) -> str | None:
    md = e.get("metadata") or {}
    key = (e.get("doc_id") or md.get("source") or "").strip().lower()
    if not key:
        links = md.get("related_links") or []
        if links and isinstance(links[0], str):
            key = links[0].strip().lower()
    return key or None


def _trim(s: str, n: int = 220) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def corroborate(
    evidences: list[dict],
    *,
    claim_sim: float = CLAIM_SIM,
    syndication_sim: float = SYNDICATION_SIM,
) -> dict:
    """Cluster an entity's evidence into claims and measure independent-source
    corroboration at BOTH levels::

        {
          "node":  {tier, sources, claim, corroborated_claims},  # actor summary
          "items": [{tier, sources}, ...],                       # per evidence, by index
        }

    ``node`` summarises the best-corroborated claim on the dominant (net) side.
    ``items`` is aligned to ``evidences`` -- each evidence carries the corroboration
    of the claim it belongs to (how many independent sources confirm that claim),
    so the Evidence view can show which specific claims are weak vs strong.
    Claims are clustered within polarity (a support and a contradiction of the
    same proposition are different claims); near-identical/syndicated copies are
    collapsed to one independent source.
    """
    n_ev = len(evidences or [])
    per_item = [{"tier": "weak", "sources": 0} for _ in range(n_ev)]
    node = {"tier": "weak", "sources": 0, "claim": "", "corroborated_claims": 0}

    items: list[tuple[int, str, str, bool]] = []   # (orig_idx, text, source, supports)
    signed = 0.0
    for i, e in enumerate(evidences or []):
        conf = e.get("confidence") or 0.0
        mag = e.get("strength")
        if conf <= 0 or not isinstance(mag, (int, float)) or mag <= 0:
            continue
        supports = bool(e.get("hypothesis", True))
        signed += (1.0 if supports else -1.0) * mag * conf
        src = _source_key(e)
        txt = _claim_text(e)
        if src and txt:
            items.append((i, txt, src, supports))
    if not items:
        return {"node": node, "items": per_item}

    dominant = signed >= 0
    best_n, best_claim, dom_corroborated = 0, "", 0
    for pol in (True, False):
        grp = [it for it in items if it[3] == pol][:_MAX_POOL]
        if not grp:
            continue
        texts = [t for _, t, _, _ in grp]
        grp_sources = [s for _, _, s, _ in grp]
        if len(grp) == 1:
            clusters, sim = [[0]], None
        else:
            sim = _cosine_matrix(texts)
            clusters = _greedy_clusters(sim, claim_sim)
        for cl in clusters:
            n = 1 if sim is None else _independent_sources(cl, sim, grp_sources, syndication_sim)
            tier = corroboration_tier(n)
            for k in cl:
                per_item[grp[k][0]] = {"tier": tier, "sources": n}
            if pol == dominant:
                if n >= 2:
                    dom_corroborated += 1
                if n > best_n:
                    best_n, best_claim = n, texts[cl[0]]

    if best_n == 0:   # dominant side had only singleton claims
        dom = next((it for it in items if it[3] == dominant), None)
        best_n, best_claim = 1, (dom[1] if dom else "")
    node = {
        "tier": corroboration_tier(best_n),
        "sources": best_n,
        "claim": _trim(best_claim),
        "corroborated_claims": dom_corroborated,
    }
    return {"node": node, "items": per_item}


def claim_corroboration(
    evidences: list[dict],
    *,
    claim_sim: float = CLAIM_SIM,
    syndication_sim: float = SYNDICATION_SIM,
) -> dict:
    """Actor-level summary only -- ``corroborate(...)["node"]``. See
    :func:`corroborate` for the per-evidence breakdown."""
    return corroborate(evidences, claim_sim=claim_sim, syndication_sim=syndication_sim)["node"]


def _cosine_matrix(texts: list[str]) -> np.ndarray:
    vecs = np.asarray(_wl.embed(texts), dtype=np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = vecs / norms
    return unit @ unit.T


def _greedy_clusters(sim: np.ndarray, claim_sim: float) -> list[list[int]]:
    clusters: list[list[int]] = []
    for i in range(sim.shape[0]):
        for cl in clusters:
            if sim[i, cl[0]] >= claim_sim:
                cl.append(i)
                break
        else:
            clusters.append([i])
    return clusters


def _independent_sources(idxs: list[int], sim: np.ndarray, sources: list[str], syndication_sim: float) -> int:
    """Distinct sources in a claim cluster, collapsing near-identical text across
    sources (syndicated wire copy) into a single independent source."""
    rep_by_source: dict[str, int] = {}
    for i in idxs:
        rep_by_source.setdefault(sources[i], i)   # one representative item per source
    reps = list(rep_by_source.values())
    n = len(reps)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(n):
        for b in range(a + 1, n):
            if sim[reps[a], reps[b]] >= syndication_sim:
                parent[find(a)] = find(b)
    return len({find(i) for i in range(n)})
