"""Cross-event OSINTGraph investigation (server-side provenance).

Takes 2+ known events, runs a 2-stage news investigation on each into the
SAME session_id with a server-side `run` label per POST (one run-label per
event). The server's merge stamps every record's `runs` list at extraction
time and unions across alias matches, so the final response carries native
cross-event analytics:

  - per-node `runs`: which run-labels attested this entity
  - `runs_in_session`: every run-label the session has seen
  - `bridging_entities`: entities in >= 2 runs (the structural backbone of
                         any cross-event connection claim)
  - themes / hypothesis_edges: gain `runs_spanned` + `is_cross_investigation`

The `run` server field carries the user-facing event NAME (e.g.
"haddad_strike"). The server uses "runs" terminology to avoid confusion
with first-class graph nodes of type="event" that the Event NER may
introduce -- those are richer, data-extracted incidents with date /
location / participants.

This script just orchestrates the GNews fetch + POSTs; analytics are read
straight from the response (no more conflated client-side computation).
The Stage-2 picker is run-restricted via the server `runs` field so
follow-up entity queries don't leak across events.

CLAIM-FRAMING DISCIPLINE: claims are structural / correlational /
source-claimed only. Joint posterior is a structural plausibility score,
not a causal counterfactual.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

import requests  # noqa: F401  -- kept for potential ad-hoc HTTP probes

# Reuse the gnews fetcher + filters + diversified picker from the single-event runner.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluate_tangraph_server as ev  # noqa: E402
from gnews_deep_investigation import (  # noqa: E402
    _apply_filters,
    _build_combined_payload,
    _connectivity_report,
    _pick_top_entities,
    _post,
    BASE,
)


# ---------------------------------------------------------------------------
# Direct cross-event text-reference scan (the server can't do this; it's a
# text search across the per-event article corpora, NOT the merged graph).
# ---------------------------------------------------------------------------

# Minimum length for a single-token needle. Short words like "gaza" (4 chars)
# match too liberally across unrelated events; require >= 6 to keep the scan
# meaningful. Multi-word phrases stay at >= 7 chars.
_MIN_SINGLE_TOKEN_LEN = 6
_MIN_PHRASE_LEN = 7
_PHRASE_STOPS = {"is", "a", "the", "of", "to", "in", "on", "and", "by", "may", "for", "as"}


def find_cross_event_text_references(events: list[dict]) -> list[dict]:
    """Articles in event B that explicitly mention event A's distinctive
    seed-query terms (and vice-versa). Naive but useful: ASCII-fold +
    case-insensitive substring search over the article bodies/titles."""

    def _norm(s: str) -> str:
        return re.sub(r"\s+", " ", (s or "").lower())

    needles_by_event: dict[str, list[str]] = {}
    for e in events:
        tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z0-9'-]+", e["query"])
                  if t.lower() not in _PHRASE_STOPS and len(t) >= _MIN_SINGLE_TOKEN_LEN]
        phrases = []
        clean_q = re.sub(r"\s+", " ", e["query"]).strip()
        words = clean_q.split()
        for n in (3, 2):
            for i in range(len(words) - n + 1):
                phrase = " ".join(words[i:i+n])
                if len(phrase) >= _MIN_PHRASE_LEN and phrase not in phrases:
                    phrases.append(phrase)
        needles_by_event[e["name"]] = [t.lower() for t in tokens] + [p.lower() for p in phrases]

    rows = []
    for source_e in events:
        for batch_articles in source_e.get("article_batches", []):
            for a in batch_articles:
                text_n = _norm(a.get("text", ""))
                title_n = _norm(a.get("title", ""))
                if not text_n and not title_n:
                    continue
                for target_e in events:
                    if target_e["name"] == source_e["name"]:
                        continue
                    matches = [n for n in needles_by_event[target_e["name"]]
                               if n in text_n or n in title_n]
                    if matches:
                        rows.append({
                            "source_event": source_e["name"],
                            "target_event": target_e["name"],
                            "article_url": a.get("real_url", ""),
                            "article_title": a.get("title", ""),
                            "matched_phrases": sorted(set(matches))[:5],
                        })
    return rows


# ---------------------------------------------------------------------------
# Per-event 2-stage runner -- server stamps `event` per POST, picker scoped.
# ---------------------------------------------------------------------------

def run_event(*, session_id: str, event_name: str, event_query: str,
              s1_articles_n: int, s2_articles_n: int, top_n: int, period: str,
              hypothesis: str | None = None, domain: str = "terror_financing",
              relevance_threshold: float = 0.6) -> dict:
    """Run a single event's 2-stage flow into the given session, with
    `run=event_name` passed through both POSTs (the user-facing event name
    becomes the server-side run-label).

    Stage-2 picker is restricted to nodes whose server-side `runs` field
    includes `event_name` -- so the picker can't drift to entities from
    prior events the session already accumulated.
    """
    print(f"\n{'='*72}\nEVENT '{event_name}'  query={event_query!r}\n{'='*72}")
    state = {"event_name": event_name, "query": event_query, "article_batches": []}

    # === STAGE 1 ===========================================================
    print(f"\n[S1] Fetching {s1_articles_n} articles for the event")
    s1_articles = ev.fetch_news(event_query, max_articles=s1_articles_n, period=period)
    s1_ok = sum(1 for a in s1_articles if a.get("text"))
    print(f"     extracted {s1_ok}/{len(s1_articles)} articles "
          f"({sum(len(a.get('text','')) for a in s1_articles):,} chars)")
    state["article_batches"].append(s1_articles)
    if s1_ok == 0:
        print("[S1] no articles; skipping this event."); state["skipped"] = True; return state

    s1_payload_text = json.dumps(ev.build_payload(event_query, s1_articles))
    print(f"[S1] POST  session={session_id[:8]}  run={event_name!r}")
    s1_response = _post(session_id, event_query, s1_payload_text, run=event_name,
                        hypothesis=hypothesis, domain=domain,
                        relevance_threshold=relevance_threshold)
    s1_response = _apply_filters(s1_response, s1_articles)
    state["response_after_S1"] = s1_response
    s1_event_ids = {
        n["identifier"] for n in s1_response.get("nodes", [])
        if event_name in (n.get("runs") or [])
    }
    print(f"     after filters: nodes={len(s1_response.get('nodes',[]))}  "
          f"themes={len(s1_response.get('themes',[]))}  "
          f"of those tagged with '{event_name}': {len(s1_event_ids)}")

    # === Pick top-N (run-restricted + theme-diversified) ===================
    exclude_tokens = {t.upper() for t in event_query.split()}
    top_entities = _pick_top_entities(
        s1_response, top_n, exclude=exclude_tokens,
        restrict_to_run=event_name,
    )
    print(f"\n[S1 -> S2] picked {len(top_entities)} run-scoped entities: {top_entities}")
    state["stage2_subqueries"] = top_entities
    if not top_entities:
        print("[S1 -> S2] no run-scoped entities to follow up on; skipping S2.")
        state["entity_ids_in_event"] = sorted(s1_event_ids); return state

    # === STAGE 2: combined entity follow-ups ===============================
    print(f"\n[S2] fetching {s2_articles_n} articles per entity ({len(top_entities)} entities)")
    articles_by_entity = {}
    for i, ent in enumerate(top_entities, 1):
        print(f"     [{i}/{len(top_entities)}] {ent}")
        arts = ev.fetch_news(ent, max_articles=s2_articles_n, period=period)
        ok = sum(1 for a in arts if a.get("text"))
        print(f"           extracted {ok}/{len(arts)}  ({sum(len(a.get('text','')) for a in arts):,} chars)")
        articles_by_entity[ent] = arts
    flat = [a for batch in articles_by_entity.values() for a in batch]
    state["article_batches"].append(flat)
    if not any(a.get("text") for a in flat):
        print("[S2] no articles; keeping S1 state."); state["response_after_S2"] = s1_response
        state["entity_ids_in_event"] = sorted(s1_event_ids); return state
    s2_payload_text = json.dumps(_build_combined_payload(event_query, articles_by_entity))
    print(f"[S2] POST  session={session_id[:8]}  run={event_name!r}  (combined payload)")
    s2_response = _post(session_id, event_query, s2_payload_text, run=event_name,
                        hypothesis=hypothesis, domain=domain,
                        relevance_threshold=relevance_threshold)
    s2_response = _apply_filters(s2_response, flat)
    state["response_after_S2"] = s2_response
    s2_event_ids = {
        n["identifier"] for n in s2_response.get("nodes", [])
        if event_name in (n.get("runs") or [])
    }
    state["entity_ids_in_event"] = sorted(s2_event_ids)
    print(f"     after filters: nodes={len(s2_response.get('nodes',[]))}  "
          f"themes={len(s2_response.get('themes',[]))}  "
          f"hyp={len(s2_response.get('hypothesis_edges',[]))}  "
          f"tagged with '{event_name}': {len(s2_event_ids)}")
    return state


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    from domain_presets import PRESETS, list_domains, resolve as resolve_domain
    p = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Available --domain presets:\n" + list_domains(),
    )
    p.add_argument("--event", action="append", required=True,
                   metavar="NAME:QUERY",
                   help="Repeatable. Each is 'short_name:gnews_query'. Min 2 required.")
    p.add_argument("--stage1-articles", type=int, default=30)
    p.add_argument("--stage2-articles-per-entity", type=int, default=15)
    p.add_argument("--top-n-entities", type=int, default=6)
    p.add_argument("--period", default="30d")
    p.add_argument("--output-dir", default="news_investigations/cross_event")
    p.add_argument("--domain", default="terror_financing",
                   choices=list(PRESETS.keys()),
                   help="Domain preset (sets hypothesis text + relevance threshold).")
    p.add_argument("--hypothesis", default=None,
                   help="Override the domain preset's hypothesis text.")
    p.add_argument("--relevance-threshold", type=float, default=None,
                   help="Override the domain preset's relevance threshold (0-1).")
    args = p.parse_args()

    hypothesis, threshold, domain_label = resolve_domain(
        args.domain,
        override_hypothesis=args.hypothesis,
        override_threshold=args.relevance_threshold,
    )

    events = []
    for spec in args.event:
        name, _, q = spec.partition(":")
        name = name.strip(); q = q.strip()
        if not name or not q:
            print(f"bad --event spec: {spec!r}", file=sys.stderr); return 2
        events.append({"name": name, "query": q})
    if len(events) < 1:
        print("need at least 1 --event entry", file=sys.stderr); return 2
    # A single event is a valid "single-query" investigation: it produces a
    # normal merged graph with no cross-event analytics (no entity can be in
    # >= 2 runs, so bridging_entities is empty and every theme is within-run).
    # The UI exposes this as single-query mode.

    session_id = str(uuid.uuid4())
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    slug = "cross_" + "_".join(re.sub(r"\W+","_",e["name"].lower()) for e in events)[:80]

    print(f"\n{'#'*72}\n## CROSS-EVENT INVESTIGATION (server-side provenance)\n##   "
          f"session={session_id[:8]}  events={[e['name'] for e in events]}\n{'#'*72}")

    artifacts: dict = {
        "session_id": session_id, "params": vars(args),
        "events": events, "per_event_states": [],
    }

    print(f"##   domain={domain_label!r}  relevance_threshold={threshold}")
    print(f"##   hypothesis: {hypothesis[:120]}{'...' if len(hypothesis) > 120 else ''}")
    for e in events:
        st = run_event(
            session_id=session_id,
            event_name=e["name"], event_query=e["query"],
            s1_articles_n=args.stage1_articles,
            s2_articles_n=args.stage2_articles_per_entity,
            top_n=args.top_n_entities, period=args.period,
            hypothesis=hypothesis, domain=domain_label,
            relevance_threshold=threshold,
        )
        e["article_batches"] = st.get("article_batches", [])
        artifacts["per_event_states"].append(st)

    # The merged graph after the FINAL event's S2 POST IS the cross-event
    # graph; the server's response carries native cross-event analytics.
    final = artifacts["per_event_states"][-1].get("response_after_S2") or \
            artifacts["per_event_states"][-1].get("response_after_S1") or {}
    artifacts["final_merged_graph"] = final

    # === Cross-event analytics =============================================
    print(f"\n{'='*72}\nCROSS-EVENT ANALYTICS (server-derived)\n{'='*72}")

    runs_in_session = final.get("runs_in_session", []) or []
    bridges = final.get("bridging_entities", []) or []
    themes = final.get("themes", []) or []
    cross_themes = [t for t in themes if t.get("is_cross_investigation")]
    hyp_edges = final.get("hypothesis_edges", []) or []
    cross_hyp = [h for h in hyp_edges if h.get("is_cross_investigation")]
    text_refs = find_cross_event_text_references(events)

    artifacts["analytics"] = {
        "runs_in_session": runs_in_session,
        "bridging_entities": bridges,
        "cross_event_themes": cross_themes,
        "cross_event_hypothesis_edges": cross_hyp,
        "direct_cross_event_text_references": text_refs,
        "connectivity": _connectivity_report(final),
    }

    print(f"\nruns_in_session: {runs_in_session}")
    print(f"\nBridging entities (server-derived, in >= 2 runs): {len(bridges)}")
    for b in bridges[:20]:
        print(f"  {b['identifier']:<48s}  in {b['n_runs']} runs "
              f"({b['runs']})  posterior={b['posterior_prob']:.2f}")
    print(f"\nCross-event themes (runs_spanned >= 2): {len(cross_themes)} of {len(themes)}")
    for t in cross_themes[:8]:
        print(f"  runs={t.get('runs_spanned')}  weight={t.get('weight',0):.2f}  "
              f"members={t.get('members')}")
    print(f"\nCross-event hypothesis edges: {len(cross_hyp)} of {len(hyp_edges)}")
    for h in cross_hyp[:8]:
        print(f"  runs={h.get('runs_spanned')}  endpoints={h.get('endpoints')}  "
              f"joint={h.get('joint_confidence',0):.3f}")
    print(f"\nDirect cross-event text references: {len(text_refs)}")
    for r in text_refs[:8]:
        print(f"  {r['source_event']} article references {r['target_event']}: "
              f"{r['article_title'][:70]!r}")
        print(f"      matched: {r['matched_phrases']}")

    out_path = out_dir / f"{slug}_{ts}.json"
    out_path.write_text(json.dumps(artifacts, indent=2, ensure_ascii=False))
    print(f"\nSaved: {out_path}  ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
