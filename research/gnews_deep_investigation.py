"""Two-stage GNews-driven OSINT investigation.

Stage 1: broad seed query -> N articles -> POST -> initial graph.
Stage 2: programmatically pick top-K non-publisher entities by score from
         Stage 1's response; query GNews for each one; COMBINE all the
         resulting articles into a single payload and POST WITH THE SAME
         session_id so the orchestrator merges into the saved state.

Result: ONE Stage 2 call carrying entity-focused articles for several
high-value entities at once. The orchestrator does cross-stage merge +
cross-stage alias-aware dedup on the combined Stage-2 payload.

Goal: produce a news-corpus investigation at comparable volume to the curated
test dossiers (~95 nodes Globalaid+Acme) so we can fairly assess the
research-added network-analysis layer on real news data.

Run (server must be up on :5003 with TANGRAPH_TMFG=1):
    PYTHONPATH=.:src:/home/dsivov/Work/tangos_mvp /home/dsivov/.conda/envs/tangos/bin/python \\
      research/gnews_deep_investigation.py \\
      --seed-query "Hamas terror financing US charities 2026" \\
      --stage1-articles 50 --stage2-articles-per-entity 20 \\
      --top-n-entities 4 --period 30d
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

import networkx as nx
import requests

# Re-use the publisher filter + fetch_news + build_payload from the existing runner.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import evaluate_tangraph_server as ev   # noqa: E402
from tangraph.graph.dedup import _is_valid_canonical   # noqa: E402


def _connectivity_report(response: dict) -> dict:
    """Return how many connected components the merged graph has (treating
    edges as undirected). Used as a post-Stage-2 sanity check -- if Stage 2's
    extraction didn't include any Stage-1 entities, the merged graph will have
    >1 component and the network analysis will be split."""
    g = nx.Graph()
    for n in response.get("nodes", []):
        g.add_node(n["identifier"])
    for e in response.get("edges", []):
        s, t = e.get("src_identifier"), e.get("dst_identifier")
        if s and t:
            g.add_edge(s, t)
    components = list(nx.connected_components(g))
    components.sort(key=len, reverse=True)
    return {
        "n_components": len(components),
        "largest_size": len(components[0]) if components else 0,
        "all_sizes": [len(c) for c in components],
    }

BASE = "http://127.0.0.1:5003/api/v1"
HYPOTHESIS_TF = (
    "Does the entity maintain relationships or conduct activities with entities, individuals, "
    "or groups that are designated, suspected of, or known to be affiliated with terrorism, "
    "where such relationships or activities meet the threshold of material support for terrorism?"
)


def _build_combined_payload(seed_query: str, articles_by_entity: dict[str, list]) -> dict:
    """Stage-2: combine articles from several entity-queries into one payload.

    Shape mirrors the OSINT-fixture / `build_payload` pattern: outer key is the
    seed query (the investigation subject); each inner entry is one article
    record carrying an inline `query` field set to the Stage-2 entity that
    drove its fetch. When the chunker walks this, every chunk's serialised
    text includes both the seed query (outer path) and the per-article entity
    framing -- mirroring what the LLM saw on the original curated OSINT input.

    Article-key format: `<publisher>__<entity>__<idx>` so deterministic + the
    entity name is visible in the JSON path even if the inline `query` field
    is somehow stripped."""
    body = {}
    for ent, articles in articles_by_entity.items():
        for i, a in enumerate(articles):
            if not a["text"]:
                continue
            key = f"{a['publisher'] or 'unknown'}__{ent}__{i}"
            body[key] = {
                "query": ent,
                "title": a["title"],
                "publisher": a["publisher"],
                "url": a["real_url"],
                "published_date": a["published_date"],
                "text": a["text"],
            }
    return {seed_query: body}


def _post(session_id: str, query: str, payload_text: str, *,
          run: str | None = None,
          hypothesis: str | None = None,
          domain: str = "terror_financing",
          relevance_threshold: float = 0.6,
          base_url: str = BASE) -> dict:
    payload = {
        "session_id": session_id,
        "text": payload_text,
        "query": query,
        "hypotests": hypothesis if hypothesis is not None else HYPOTHESIS_TF,
        "domain": domain,
        "use_regular_triangulation": False,
        "relevance_threshold": relevance_threshold,
    }
    # Cross-run provenance: when this POST is part of a multi-run session
    # (cross-event experiment), name the run so the server stamps every
    # extracted/re-attested record. The server's per-entity `runs` field +
    # `bridging_entities` / `runs_spanned` sections are derived from these
    # labels.
    if run:
        payload["run"] = run
    t0 = time.time()
    r = requests.post(f"{base_url}/get_nodes", json=payload, timeout=1800)
    r.raise_for_status()
    resp = r.json()
    print(f"      POST -> status={resp.get('status')}  nodes={len(resp.get('nodes',[]))}  "
          f"edges={len(resp.get('edges',[]))}  themes={len(resp.get('themes',[]))}  "
          f"({time.time()-t0:.1f}s)")
    return resp


def _apply_filters(response: dict, articles: list) -> dict:
    """Run the news-flow publisher + timeline filters."""
    response = ev.filter_publishers(response, articles)
    response = ev.filter_timeline_events(response)
    return response


def _pick_top_entities(response: dict, top_n: int, *, exclude: set,
                       restrict_to_run: str | None = None) -> list[str]:
    """Pick `top_n` Stage-2 query entities with spatial diversity.

    Strategy: walk the themes (each a 4-entity tight-clique surfaced by TMFG),
    round-robin picking the highest-scoring not-yet-picked non-publisher
    entity from each. This avoids drawing all picks from one tight cluster
    around the seed -- different themes anchor different sub-graphs, so
    distributing picks across themes broadens the Stage-2 query set.
    Falls back to score-only when themes are unavailable / exhausted.

    `restrict_to_run`: when set, only consider entities whose per-node
    `runs` field includes this label. Critical for cross-run (cross-event)
    sessions where the response is the CUMULATIVE merged graph (including
    prior runs' entities) -- without restriction, the picker leaks across
    runs and Stage-2 fetches articles for the wrong subjects."""
    nodes_by_id = {n["identifier"]: n for n in response.get("nodes", [])}
    themes = response.get("themes") or []

    def _eligible(ident: str) -> bool:
        if ident in exclude:
            return False
        if ev.is_publisher(ident):
            return False
        if ident not in nodes_by_id:
            return False
        node = nodes_by_id[ident]
        # Events are incident descriptions, not subjects to deepen. Querying
        # GNews for an event identifier returns articles about that same
        # incident, not expansion. Reject them as Stage-2 seeds entirely.
        if node.get("type") == "event":
            return False
        # Skip headline-shaped entity identifiers (long, verb-laden) -- they
        # slip through NER occasionally and burn Stage-2's article budget on
        # one-shot queries that mirror the original headline rather than
        # widening the corpus around a real entity.
        if not _is_valid_canonical(ident):
            return False
        if restrict_to_run is not None:
            node_runs = node.get("runs") or []
            if restrict_to_run not in node_runs:
                return False
        return True

    # Per-theme score-ordered candidate lists. Themes are dicts with a
    # `members` list (4-clique surfaced by TMFG); fall back gracefully to
    # `entities` or raw list for forward-/backward-compat.
    theme_lists = []
    for theme in themes:
        if isinstance(theme, dict):
            members = theme.get("members") or theme.get("entities") or []
        else:
            members = theme
        if not members:
            continue
        ranked = sorted(
            (m for m in members if _eligible(m)),
            key=lambda i: -float(nodes_by_id[i].get("score") or 0.0),
        )
        if ranked:
            theme_lists.append(ranked)

    picked: list[str] = []
    seen: set[str] = set()

    # Round-robin across themes -- one fresh entity per theme each lap.
    while len(picked) < top_n and theme_lists:
        for tl in list(theme_lists):
            while tl and tl[0] in seen:
                tl.pop(0)
            if not tl:
                theme_lists.remove(tl)
                continue
            ident = tl.pop(0)
            picked.append(ident); seen.add(ident)
            if len(picked) >= top_n:
                break

    # Fallback: fill any remaining slots from the global score ranking.
    if len(picked) < top_n:
        global_ranked = sorted(
            (i for i in nodes_by_id if _eligible(i) and i not in seen),
            key=lambda i: -float(nodes_by_id[i].get("score") or 0.0),
        )
        for ident in global_ranked:
            picked.append(ident); seen.add(ident)
            if len(picked) >= top_n:
                break
    return picked[:top_n]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--seed-query", required=True)
    p.add_argument("--stage1-articles", type=int, default=50)
    p.add_argument("--stage2-articles-per-entity", type=int, default=20)
    p.add_argument("--top-n-entities", type=int, default=8,
                   help="How many top-scoring entities from Stage 1 to query for in Stage 2 "
                        "(picked round-robin across themes for spatial diversity).")
    p.add_argument("--period", default="30d")
    p.add_argument("--output-dir", default="news_investigations/deep")
    args = p.parse_args()

    session_id = str(uuid.uuid4())
    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = ev._slug(args.seed_query)
    print(f"\n{'='*72}\nDEEP NEWS INVESTIGATION (2 stages)  session={session_id[:8]}\n"
          f"  seed query (Stage 1): {args.seed_query!r}\n"
          f"  Stage 1 articles:     {args.stage1_articles}\n"
          f"  Stage 2:              top-{args.top_n_entities} entities x "
          f"{args.stage2_articles_per_entity} articles each (ONE combined POST)\n"
          f"  period:               {args.period}\n{'='*72}")

    artifacts = {"session_id": session_id, "seed_query": args.seed_query, "params": vars(args), "stages": []}

    # === STAGE 1 ===========================================================
    print(f"\n[STAGE 1] Fetching {args.stage1_articles} articles for seed query")
    s1_articles = ev.fetch_news(args.seed_query, max_articles=args.stage1_articles, period=args.period)
    s1_ok = sum(1 for a in s1_articles if a["text"])
    print(f"      extracted {s1_ok}/{len(s1_articles)} articles "
          f"({sum(len(a['text']) for a in s1_articles):,} chars total)")
    if s1_ok == 0:
        print("Stage 1: no articles extracted; abort.")
        return 1
    print(f"[STAGE 1] POST to {BASE}/get_nodes")
    s1_payload_text = json.dumps(ev.build_payload(args.seed_query, s1_articles))
    s1_response = _post(session_id, args.seed_query, s1_payload_text)
    s1_response = _apply_filters(s1_response, s1_articles)
    print(f"      after filters: nodes={len(s1_response.get('nodes', []))}  "
          f"themes={len(s1_response.get('themes', []))}  "
          f"promoted={len(s1_response.get('promoted_entities', []))}  "
          f"hypothesis={len(s1_response.get('hypothesis_edges', []))}")
    s1_ids = {n["identifier"] for n in s1_response.get("nodes", [])}
    artifacts["stages"].append({
        "stage": 1, "query": args.seed_query, "articles": s1_articles, "response": s1_response,
        "entity_ids": sorted(s1_ids),
    })

    # === Pick top-N entities to drive Stage 2 ==============================
    # CRITICAL: every Stage-2 query MUST be an entity that already exists in
    # Stage 1's saved state. Otherwise Stage 2's articles introduce a brand-new
    # entity cluster with NO bridge back to Stage 1 -- the merged graph would
    # end up with disconnected components, and the network analysis would
    # split into two unrelated halves.
    seed_tokens = {t.upper() for t in args.seed_query.split()}
    top_entities = _pick_top_entities(s1_response, args.top_n_entities, exclude=seed_tokens)
    # Assertion: every picked entity must be in Stage 1's node set.
    missing = [e for e in top_entities if e not in s1_ids]
    assert not missing, f"picked entities not in Stage 1: {missing}"
    print(f"\n[STAGE 1 -> 2] Top {len(top_entities)} entities picked for Stage 2 follow-up:")
    print(f"        (all verified to exist in Stage 1's node set -> Stage 2 will share these IDs)")
    for ent in top_entities:
        node = next((n for n in s1_response["nodes"] if n["identifier"] == ent), None)
        s = node.get("score", 0) if node else 0
        p = node.get("posterior_prob", 0) if node else 0
        print(f"        {ent}  (score={s:.3f}, posterior={p:.3f})")

    # Stage-1 connectivity baseline for comparison
    s1_conn = _connectivity_report(s1_response)
    print(f"\n[Stage 1 connectivity] {s1_conn['n_components']} component(s), largest size {s1_conn['largest_size']}")

    # === STAGE 2 (one combined POST with same session_id) ==================
    print(f"\n[STAGE 2] Fetching {args.stage2_articles_per_entity} articles for each entity "
          f"({len(top_entities)} entities, combined into ONE payload)")
    articles_by_entity: dict[str, list] = {}
    for i, ent in enumerate(top_entities, 1):
        print(f"      [{i}/{len(top_entities)}] {ent}")
        ents_articles = ev.fetch_news(ent, max_articles=args.stage2_articles_per_entity, period=args.period)
        ok = sum(1 for a in ents_articles if a["text"])
        chars = sum(len(a["text"]) for a in ents_articles)
        print(f"            extracted {ok}/{len(ents_articles)} articles ({chars:,} chars)")
        articles_by_entity[ent] = ents_articles

    flat_articles = [a for batch in articles_by_entity.values() for a in batch]
    total_ok = sum(1 for a in flat_articles if a["text"])
    print(f"      Stage 2 total: {total_ok} articles across {len(top_entities)} entity queries "
          f"({sum(len(a['text']) for a in flat_articles):,} chars)")
    if total_ok == 0:
        print("Stage 2: no articles extracted; skipping.")
    else:
        # Reuse the seed query as the Stage-2 framing. The investigation
        # (and its `hypotests`) has not changed -- only the corpus has --
        # so the LLM should evaluate Stage-2 articles against the same
        # question it used in Stage 1. The picked entities are just how we
        # SOURCED the articles via GNews; the question being asked stays.
        s2_query = args.seed_query
        s2_payload_text = json.dumps(_build_combined_payload(args.seed_query, articles_by_entity))
        print(f"[STAGE 2] POST to {BASE}/get_nodes  (same session_id, query={s2_query!r}, "
              f"combined payload from {len(top_entities)} entity follow-ups)")
        s2_response = _post(session_id, s2_query, s2_payload_text)
        s2_response = _apply_filters(s2_response, flat_articles)
        merged_ids = {n["identifier"] for n in s2_response.get("nodes", [])}
        new_ids = merged_ids - s1_ids
        print(f"      after filters: nodes={len(s2_response.get('nodes', []))}  "
              f"themes={len(s2_response.get('themes', []))}  "
              f"promoted={len(s2_response.get('promoted_entities', []))}  "
              f"hypothesis={len(s2_response.get('hypothesis_edges', []))}")
        print(f"      Stage 2 added {len(new_ids)} new entities to the merged graph")
        if new_ids:
            print(f"        sample: {sorted(new_ids)[:8]}")

        # Connectivity guard: how many Stage-1 entities also appear in the
        # merged response? (If zero, Stage 2 produced a disjoint cluster.)
        overlap = sorted(s1_ids & merged_ids)
        s2_conn = _connectivity_report(s2_response)
        print(f"\n[Stage 2 connectivity] {s2_conn['n_components']} component(s), "
              f"sizes {s2_conn['all_sizes'][:6]}{'...' if len(s2_conn['all_sizes']) > 6 else ''}")
        print(f"[Stage 1 <-> Stage 2 overlap] {len(overlap)}/{len(s1_ids)} Stage-1 entities "
              f"reappear in merged state")
        for ent in top_entities:
            mark = "OK" if ent in merged_ids else "MISSING"
            print(f"        {mark:8s} {ent}")
        if s2_conn["n_components"] > 1:
            print(f"  WARNING: merged graph is NOT fully connected ({s2_conn['n_components']} "
                  f"components). Network analysis (TMFG/BP) only runs on the largest one.")

        artifacts["stages"].append({
            "stage": 2,
            "query": s2_query,
            "stage2_entity_subqueries": top_entities,
            "articles_by_entity": {e: a for e, a in articles_by_entity.items()},
            "response": s2_response,
            "new_entity_ids": sorted(new_ids),
            "connectivity": s2_conn,
            "stage1_stage2_overlap": overlap,
            "query_entities_in_merged": {e: (e in merged_ids) for e in top_entities},
        })
        artifacts["final_response"] = s2_response

    # === Save ==============================================================
    if "final_response" not in artifacts:
        artifacts["final_response"] = s1_response
    out_path = out_dir / f"{slug}_{ts}.json"
    out_path.write_text(json.dumps(artifacts, indent=2, ensure_ascii=False))
    final = artifacts["final_response"]
    print(f"\n{'='*72}\nFINAL MERGED STATE  ({len(final.get('nodes',[]))} nodes, "
          f"{len(final.get('edges',[]))} edges, "
          f"{len(final.get('themes',[]))} themes, "
          f"{len(final.get('promoted_entities',[]))} promoted, "
          f"{len(final.get('hypothesis_edges',[]))} hypothesis edges)\n"
          f"Saved to: {out_path}  ({out_path.stat().st_size:,} bytes)\n{'='*72}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
