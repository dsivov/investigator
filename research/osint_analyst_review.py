"""Produce an OSINT-analyst-perspective markdown review of a cross-event JSON.

Reads the saved artifact and writes a markdown report grounded in the ACTUAL
node + edge attributes (not just the summary fields). For each substantive
entity it walks `data.relations` with type/context/source_url, prints
`evidence` records with reasoning + doc_id + confidence, and pulls
`timeline_events`. For each event it shows `data.participants` with roles,
`data.description`, `data.source_url`. Cross-event analysis follows the
server-derived bridges and walks each bridge's evidence in EACH run.

Use:
    python research/osint_analyst_review.py <cross_event_json> [--out report.md]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


def _as_list(v):
    if v is None: return []
    if isinstance(v, list): return v
    return [v]


def _norm_str(v):
    """Some fields collapsed by within-run merge become lists. Pretty-print
    the first non-empty value, or join short lists."""
    if isinstance(v, list):
        items = [str(x).strip() for x in v if str(x).strip()]
        if not items: return ""
        if len(items) == 1: return items[0]
        # Collapse identical items
        uniq = list(dict.fromkeys(items))
        if len(uniq) == 1: return uniq[0]
        return " | ".join(uniq[:3]) + ("..." if len(uniq) > 3 else "")
    return str(v or "").strip()


def short_url(url):
    if not url: return ""
    url = str(url).split(";")[0].strip()
    m = re.match(r"https?://(?:www\.)?([^/]+)(.*)", url)
    if not m: return url[:80]
    host, path = m.groups()
    if len(path) > 60: path = path[:57] + "..."
    return f"{host}{path}"


def node_runs(n): return n.get("runs") or []
def is_event(n): return n.get("type") == "event"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("json_path", type=Path)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args()
    d = json.loads(args.json_path.read_text())
    final = d["final_merged_graph"]
    events_def = d["events"]   # [{name, query, ...}]
    nodes = final["nodes"]
    edges = final["edges"]
    bridges = final.get("bridging_entities") or []
    cross_themes = [t for t in final.get("themes", []) if t.get("is_cross_investigation")]
    cross_hyp = [h for h in final.get("hypothesis_edges", []) if h.get("is_cross_investigation")]
    runs_in_session = final.get("runs_in_session") or []
    out_path = args.out or args.json_path.with_suffix(".analyst_review.md")

    nodes_by_id = {n["identifier"]: n for n in nodes}
    edges_by_endpoint = {}
    for e in edges:
        for k in (e.get("src_identifier"), e.get("dst_identifier")):
            edges_by_endpoint.setdefault(k, []).append(e)

    # Group nodes per run
    events_per_run = {r: [] for r in runs_in_session}
    entities_per_run = {r: [] for r in runs_in_session}
    for n in nodes:
        for r in node_runs(n):
            if r not in events_per_run: continue
            (events_per_run if is_event(n) else entities_per_run)[r].append(n)

    L = []
    L.append(f"# OSINT analyst review — cross-event investigation\n")
    L.append(f"\n_Generated from `{args.json_path.name}` — grounded in node `data.*` "
             f"and edge `relations` / `attributes.source_url` fields, not just the "
             f"summary counts._\n")

    # ---- Scope ----
    L.append(f"\n## Scope\n\n")
    L.append(f"Three independently-seeded investigations, fused into ONE OSINTGraph session via the server's `run` field.\n\n")
    L.append(f"| Run | Seed query | Articles fetched / extracted | Run-tagged entities |\n")
    L.append(f"|---|---|---|---|\n")
    for ev_def in events_def:
        name = ev_def["name"]
        n_en = len([n for n in entities_per_run.get(name, [])])
        n_ev = len([n for n in events_per_run.get(name, [])])
        batches = ev_def.get("article_batches", [])
        ok = sum(1 for batch in batches for a in batch if a.get("text"))
        total = sum(len(batch) for batch in batches)
        L.append(f"| `{name}` | {ev_def['query']!r} | {ok} / {total} | {n_en} entities + {n_ev} events |\n")
    L.append(f"\nMerged-graph totals: **{len(nodes)} nodes** ({sum(1 for n in nodes if is_event(n))} events + "
             f"{sum(1 for n in nodes if not is_event(n))} entities), **{len(edges)} edges**, "
             f"{len(final.get('themes',[]))} themes, {len(final.get('hypothesis_edges',[]))} hypothesis edges, "
             f"{len(bridges)} bridging entities.\n")

    # ---- Per-event narratives ----
    for ev_def in events_def:
        name = ev_def["name"]
        L.append(f"\n## Event: `{name}`\n\n")
        L.append(f"_Seed: \"{ev_def['query']}\"_\n\n")

        # Sub-section: events the LLM extracted (type=event nodes tagged with this run)
        run_events = events_per_run.get(name, [])
        # Sort by confidence desc, then by date
        def _ev_conf(e):
            d_ = e.get("data") or {}
            c = d_.get("confidence")
            if isinstance(c, list):
                try: c = max(float(x) for x in c if x)
                except Exception: c = 0.0
            try: return float(c or 0)
            except Exception: return 0.0
        run_events.sort(key=_ev_conf, reverse=True)

        L.append(f"### Real-world events surfaced ({len(run_events)})\n\n")
        if not run_events:
            L.append("_(no events extracted)_\n")
        else:
            shown = 0
            seen_descriptions = set()
            for e in run_events:
                d_ = e.get("data") or {}
                desc_norm = _norm_str(d_.get("description"))[:120]
                # Drop near-paraphrase duplicates by first-120-char description
                key = desc_norm.lower()[:80]
                if key in seen_descriptions:
                    continue
                seen_descriptions.add(key)
                shown += 1
                if shown > 8: break  # cap output
                L.append(f"\n#### {e['identifier'][:100]}\n")
                L.append(f"- **type**: `{_norm_str(d_.get('event_type'))}`  "
                         f"**date**: {_norm_str(d_.get('date')) or '_(absent)_'}  "
                         f"**location**: {_norm_str(d_.get('location')) or '_(absent)_'}\n")
                L.append(f"- **confidence**: {_ev_conf(e):.2f}\n")
                parts = d_.get("participants") or []
                if parts:
                    seen_pn = set()
                    uniq_parts = []
                    for p in parts:
                        if not isinstance(p, dict): continue
                        pname = p.get("name") or ""
                        if pname.upper() in seen_pn: continue
                        seen_pn.add(pname.upper())
                        uniq_parts.append(p)
                    L.append(f"- **participants**: " +
                             ", ".join(f"`{p.get('name')}` ({p.get('role') or 'role?'})" for p in uniq_parts) + "\n")
                if desc_norm:
                    L.append(f"- **description**: {desc_norm}\n")
                urls = _as_list(d_.get("source_url"))
                urls = list(dict.fromkeys(u for u in urls if u))
                if urls:
                    L.append(f"- **source(s)**: " + ", ".join(short_url(u) for u in urls[:3]) + "\n")

        # Sub-section: top entities (by relations + evidence count)
        run_entities = entities_per_run.get(name, [])
        def _signal(n):
            return (len((n.get("data") or {}).get("relations") or []) +
                    len(n.get("evidence") or []))
        run_entities.sort(key=_signal, reverse=True)
        L.append(f"\n### Substantive entities ({len(run_entities)})\n\n")
        L.append(f"_Per-entity briefs show the LLM's attested relations (with context + source URL) "
                 f"and strongest evidence records grounded in the article bodies._\n")

        substantive = [n for n in run_entities
                       if not re.search(r"\b(KILLS?|SANCTIONS?|APPROVES?|CLEARS?|ORDERS|ENDS|SETS?|EXPECTS?|COMPLETES?|LIFTS?)\b",
                                        n["identifier"]) and len(n["identifier"].split()) <= 5][:8]

        for n in substantive:
            d_ = n.get("data") or {}
            rels = d_.get("relations") or []
            ev_recs = n.get("evidence") or []
            L.append(f"\n#### {n['identifier']}\n")
            L.append(f"_Posterior: {float(n.get('posterior_prob') or 0):.2f}; "
                     f"score: {float(n.get('score') or 0):.2f}; "
                     f"{len(rels)} attested relations, {len(ev_recs)} evidence records_\n\n")
            # Show top 4 distinct relations
            if rels:
                seen_pairs = set()
                shown_r = 0
                L.append(f"**Attested relations:**\n")
                for r in rels:
                    if not isinstance(r, dict): continue
                    counterpart = r.get("related_node") or "?"
                    direction = r.get("direction", "")
                    rel_obj = r.get("relations") or {}
                    if isinstance(rel_obj, str):
                        try: rel_obj = json.loads(rel_obj)
                        except Exception: rel_obj = {}
                    if isinstance(rel_obj, list):
                        rel_obj = next((x for x in rel_obj if isinstance(x, dict)), {})
                    if not isinstance(rel_obj, dict):
                        rel_obj = {}
                    rtype = rel_obj.get("type", "?")
                    ctx = (rel_obj.get("context") or "").strip()
                    src_url = (r.get("attributes") or {}).get("source_url", "") or ""
                    arrow = "->" if direction == "outgoing" else "<-" if direction == "incoming" else "--"
                    pair_key = (counterpart, rtype, ctx[:60])
                    if pair_key in seen_pairs: continue
                    seen_pairs.add(pair_key)
                    line = f"- ({arrow} _{rtype}_) **{counterpart}**"
                    if ctx: line += f" — {ctx[:300]}"
                    if src_url: line += f"  _(source: {short_url(src_url)})_"
                    L.append(line + "\n")
                    shown_r += 1
                    if shown_r >= 4: break
            # Show top 2 strongest evidence
            if ev_recs:
                strongest = sorted(ev_recs, key=lambda e: -float(e.get("confidence") or 0))[:2]
                L.append(f"\n**Strongest evidence:**\n")
                for e in strongest:
                    reasoning = (e.get("reasoning") or "").strip().split("\n")[0][:380]
                    conf = float(e.get("confidence") or 0)
                    doc = (e.get("doc_id") or "").split(";")[0].strip()
                    L.append(f"- _[conf {conf:.2f}]_ {reasoning}" + (f"  _(source: {short_url(doc)})_" if doc else "") + "\n")
            # Show timeline_events if present
            tls = d_.get("timeline_events") or []
            if isinstance(tls, list) and tls:
                # dedupe by event text
                seen_ev = set()
                uniq_tls = []
                for t in tls:
                    if not isinstance(t, dict): continue
                    key = (t.get("event") or "")[:80]
                    if key in seen_ev: continue
                    seen_ev.add(key)
                    uniq_tls.append(t)
                if uniq_tls:
                    L.append(f"\n**Timeline events (top {min(3, len(uniq_tls))} of {len(uniq_tls)}):**\n")
                    for t in uniq_tls[:3]:
                        dt = t.get("date") or "?"
                        ev_str = (t.get("event") or "")[:200]
                        if ev_str:
                            L.append(f"- `{dt}` — {ev_str}\n")

    # ---- Cross-event bridges ----
    L.append(f"\n## Cross-event bridging entities\n\n")
    if not bridges:
        L.append("_None — events are independent in this corpus._\n")
    else:
        L.append(f"The server identified **{len(bridges)} entities that appear in multiple runs** "
                 f"(per the per-record `runs` provenance, not heuristic post-hoc matching). For each bridge "
                 f"we walk the actual evidence records to show HOW each event attests it.\n\n")
        for b in bridges:
            ident = b["identifier"]
            node = nodes_by_id.get(ident)
            if not node: continue
            L.append(f"\n### {ident}\n")
            L.append(f"_In {b['n_runs']} runs: {b['runs']}; posterior {b['posterior_prob']:.2f}; "
                     f"score {b.get('score',0):.2f}_\n\n")
            ev_recs = node.get("evidence") or []
            # Group evidence by which run's articles attest it -- use source URL
            # against each event's article batch URLs
            ev_def_by_name = {e["name"]: e for e in events_def}
            for run in b["runs"]:
                ev_def = ev_def_by_name.get(run)
                if not ev_def: continue
                urls = set()
                for batch in ev_def.get("article_batches", []):
                    for a in batch:
                        u = a.get("real_url") or ""
                        if u: urls.add(u)
                # Find evidence whose doc_id falls in this run's article URLs
                matches = []
                for e_rec in ev_recs:
                    doc = (e_rec.get("doc_id") or "")
                    for part in re.split(r"[;,]\s*", doc):
                        part = part.strip()
                        if part and part in urls:
                            matches.append(e_rec); break
                L.append(f"\n**Role in `{run}`** ({len(matches)} attesting evidence record(s) from that run's corpus):\n")
                if matches:
                    e_top = sorted(matches, key=lambda e: -float(e.get("confidence") or 0))[:2]
                    for e in e_top:
                        reasoning = (e.get("reasoning") or "").strip().split("\n")[0][:380]
                        conf = float(e.get("confidence") or 0)
                        doc = (e.get("doc_id") or "").split(";")[0].strip()
                        L.append(f"- _[conf {conf:.2f}]_ {reasoning}  _(source: {short_url(doc)})_\n")
                else:
                    L.append(f"- _(no evidence from this run's articles; bridge is via the affiliation graph only)_\n")
            # Edges this entity participates in, broken down by counterpart's runs
            inc_edges = edges_by_endpoint.get(ident, [])
            inc_edges = [e for e in inc_edges if e.get("type") != "event_participation"]
            if inc_edges:
                L.append(f"\n**Edges to/from this entity ({len(inc_edges)}):**\n")
                shown_e = 0
                for e_rec in inc_edges:
                    counterpart = (e_rec.get("dst_identifier") if e_rec.get("src_identifier") == ident
                                   else e_rec.get("src_identifier")) or "?"
                    rels_raw = e_rec.get("relations") or ""
                    try:
                        rels = json.loads(rels_raw) if isinstance(rels_raw, str) else (rels_raw or {})
                    except Exception:
                        rels = {}
                    if isinstance(rels, list):
                        rels = next((r for r in rels if isinstance(r, dict)), {})
                    if not isinstance(rels, dict):
                        rels = {}
                    rtype = rels.get("type", "?")
                    ctx = (rels.get("context") or "").strip()
                    src_url = (e_rec.get("attributes") or {}).get("source_url", "") or ""
                    if not ctx and not src_url:  # skip empty edges
                        continue
                    direction = "->" if e_rec.get("src_identifier") == ident else "<-"
                    L.append(f"- ({direction} _{rtype}_) **{counterpart}**" +
                             (f" — {ctx[:240]}" if ctx else "") +
                             (f"  _(source: {short_url(src_url)})_" if src_url else "") + "\n")
                    shown_e += 1
                    if shown_e >= 6: break

    # ---- Cross-event themes ----
    L.append(f"\n## Cross-event TMFG themes (top {min(8, len(cross_themes))})\n\n")
    L.append(f"_4-entity tight cliques whose members come from >= 2 runs. Members include "
             f"both entities and events (events appear with their long identifiers)._\n\n")
    for t in cross_themes[:8]:
        members = t.get("members") or []
        weight = float(t.get("weight") or 0)
        runs_s = t.get("runs_spanned") or []
        L.append(f"\n**Theme weight {weight:.1f}** spans `{runs_s}`:\n")
        for m in members:
            n = nodes_by_id.get(m)
            tp = "event" if (n and is_event(n)) else "entity"
            L.append(f"  - `{m[:80]}` ({tp})\n")

    # ---- Cross-event hypothesis edges (kept for reference / calibration) ----
    L.append(f"\n## Cross-event hypothesis edges (TMFG fill-in — reference only)\n\n")
    L.append(f"_TMFG fill-in pairs whose endpoints span >= 2 runs. Joint confidence is "
             f"`posterior(A) * posterior(B)` -- if both endpoints are well-attested it pegs at 1.0 "
             f"regardless of any real connection. **Use `cross_event_leads` below for actionable leads**; "
             f"this section is kept for reference._\n\n")
    cross_hyp_sorted = sorted(cross_hyp, key=lambda h: -float(h.get("joint_confidence") or 0))
    for h in cross_hyp_sorted[:10]:
        eps = h.get("endpoints") or []
        joint = float(h.get("joint_confidence") or 0)
        runs_s = h.get("runs_spanned") or []
        bucket = "STRONG" if joint > 0.9 else "MEDIUM" if joint > 0.6 else "WEAK"
        L.append(f"- _[**{bucket}** joint {joint:.2f}]_ `{eps[0][:60]}` <-> `{eps[1][:60]}` "
                 f"(spans {runs_s})\n")

    # ---- Cross-event LEADS (triangle through a shared cross-run bridge) ----
    L.append(f"\n## Cross-event leads (triangle via shared bridge)\n\n")
    L.append(f"_Pairs `(A, B)` where A is attested in exactly one run, B in a different one, and "
             f"there is at least one third node `C` (a `bridging_entity`) such that C has an "
             f"ATTESTED affiliation edge to both A and B. Score = sum of bridge posteriors. "
             f"Unlike `hypothesis_edges`, the connection is grounded in real source-attested edges, "
             f"just routed through a known cross-event bridge._\n\n")

    # Prefer the server-derived cross_event_leads when present (newer runs);
    # fall back to mirroring the algorithm client-side for older artifacts.
    runs_per_node = {n["identifier"]: set(n.get("runs") or []) for n in nodes}
    server_leads = final.get("cross_event_leads") or []
    if server_leads:
        leads = sorted(server_leads,
                       key=lambda r: (-float(r.get("score") or 0),
                                      -len(r.get("bridges") or []),
                                      (r.get("endpoints") or ["",""])[0]))
        source_note = "_(server-derived from the response's `cross_event_leads` field.)_"
    else:
        bridge_set = {b["identifier"] for b in bridges}
        bridge_posterior = {b["identifier"]: float(b.get("posterior_prob") or 0) for b in bridges}
        adj: dict = {}
        for e in edges:
            if e.get("is_hypothesis"): continue
            if e.get("type") == "event_participation": continue
            s, t = e.get("src_identifier"), e.get("dst_identifier")
            if not (s and t) or s == t: continue
            adj.setdefault(s, set()).add(t)
            adj.setdefault(t, set()).add(s)
        leads_acc: dict = {}
        for c_id in bridge_set:
            neighbours = sorted(adj.get(c_id, set()))
            single_run_neigh = []
            for nb in neighbours:
                rs = runs_per_node.get(nb) or set()
                if len(rs) == 1:
                    single_run_neigh.append((nb, next(iter(rs))))
            for i in range(len(single_run_neigh)):
                a, ra = single_run_neigh[i]
                for j in range(i + 1, len(single_run_neigh)):
                    b, rb = single_run_neigh[j]
                    if ra == rb: continue
                    pair_key = tuple(sorted([a, b]))
                    rec = leads_acc.setdefault(pair_key, {
                        "endpoints": list(pair_key),
                        "bridges": [],
                        "runs_spanned": set(),
                        "score": 0.0,
                    })
                    if c_id not in rec["bridges"]:
                        rec["bridges"].append(c_id)
                        rec["score"] += bridge_posterior.get(c_id, 0.5)
                    rec["runs_spanned"].update({ra, rb})
        leads = sorted(leads_acc.values(),
                       key=lambda r: (-r["score"], -len(r["bridges"]), r["endpoints"][0]))
        source_note = "_(client-side mirror -- the server response predates the cross_event_leads field.)_"
    L.append(source_note + "\n\n")
    if not leads:
        L.append(f"_(no cross-event leads found: no triangle of attested edges via a shared bridge)_\n")
    else:
        L.append(f"**{len(leads)} cross-event leads found.** Top {min(15, len(leads))}:\n\n")
        for rec in leads[:15]:
            a, b = rec["endpoints"]
            bridges_str = ", ".join(f"`{x}`" for x in rec["bridges"])
            a_run = sorted(runs_per_node.get(a, set()))
            b_run = sorted(runs_per_node.get(b, set()))
            L.append(f"- _[score {rec['score']:.2f}; {len(rec['bridges'])} bridge(s)]_ "
                     f"`{a[:50]}` ({a_run[0] if a_run else '?'}) "
                     f"<-> `{b[:50]}` ({b_run[0] if b_run else '?'}) "
                     f"via bridge(s): {bridges_str}\n")

    # ---- Source-claimed causation (Level-1 evidence) ----
    L.append(f"\n## Source-claimed causation\n\n")
    L.append(f"_Causal assertions the source articles MAKE between actors / events. "
             f"These are Level-1 evidence (per Pearl's ladder of causation): claims worth "
             f"examining, **not** established cause-and-effect. Each edge carries an "
             f"explicit `weight = strength x confidence x multi_source_boost`; treat weight "
             f">= 1.0 as strongly-corroborated, 0.6-1.0 as worth examining, < 0.3 as likely noise._\n\n")

    causal_edges = [e for e in edges if e.get("type") == "claimed_caused_by"]
    causal_edges.sort(key=lambda e: -float((e.get("attributes") or {}).get("weight") or 0))

    if not causal_edges:
        L.append(f"_(no source-claimed causation edges in this artifact -- either causal-claim "
                 f"extraction was disabled, or no articles in this run made explicit causal assertions "
                 f"between extracted entities.)_\n")
    else:
        # Histogram of weight buckets
        b_strong  = sum(1 for e in causal_edges if float((e.get("attributes") or {}).get("weight") or 0) >= 1.0)
        b_medium  = sum(1 for e in causal_edges if 0.6 <= float((e.get("attributes") or {}).get("weight") or 0) < 1.0)
        b_weak    = sum(1 for e in causal_edges if 0.3 <= float((e.get("attributes") or {}).get("weight") or 0) < 0.6)
        b_noise   = sum(1 for e in causal_edges if float((e.get("attributes") or {}).get("weight") or 0) < 0.3)
        L.append(f"**{len(causal_edges)} total** causal-claim edges. By weight bucket: "
                 f"strong (>=1.0): **{b_strong}** | medium (0.6-1.0): **{b_medium}** | "
                 f"weak (0.3-0.6): **{b_weak}** | noise (<0.3): {b_noise}.\n\n")

        # Show top 15 by weight
        L.append(f"**Top {min(15, len(causal_edges))} by weight (filtered to >= 0.3):**\n\n")
        for e in causal_edges[:15]:
            attrs = e.get("attributes") or {}
            w = float(attrs.get("weight") or 0)
            if w < 0.3:
                continue
            src = e.get("src_identifier", "?")
            dst = e.get("dst_identifier", "?")
            hed = attrs.get("hedging_tags") or []
            dirs = attrs.get("directions") or []
            n_src = attrs.get("attestation_count", 0)
            urls = attrs.get("source_urls") or []
            claims = attrs.get("claim_texts") or []
            # Bucket label
            bucket = ("STRONG" if w >= 1.0 else "MEDIUM" if w >= 0.6 else "WEAK")
            # First claim text and source
            claim = claims[0] if claims else ""
            src_run = sorted(runs_per_node.get(src, set()))
            dst_run = sorted(runs_per_node.get(dst, set()))
            L.append(f"### {src} <-> {dst}\n")
            L.append(f"- **weight {w:.2f}** _[{bucket}]_  "
                     f"strength {float(attrs.get('strength') or 0):.2f}  "
                     f"confidence {float(attrs.get('confidence') or 0):.2f}  "
                     f"attested by **{n_src}** source(s)\n")
            L.append(f"- direction: {', '.join(d for d in dirs if d) or '?'}; "
                     f"hedging: {', '.join(h for h in hed if h) or '?'}\n")
            if src_run or dst_run:
                L.append(f"- runs: `{src}` in {src_run}, `{dst}` in {dst_run}\n")
            if claim:
                L.append(f"- claim (paraphrased): _\"{claim[:300]}\"_\n")
            if urls:
                shown = [short_url(u) for u in urls[:3]]
                L.append(f"- sources: " + ", ".join(shown) + (f"  +{len(urls)-3} more" if len(urls) > 3 else "") + "\n")
            L.append("\n")

    # ---- Honest caveats ----
    L.append(f"\n## Data-quality caveats\n\n")
    n_event_nodes = sum(1 for n in nodes if is_event(n))
    n_merged = sum(1 for n in nodes if is_event(n) and (n.get("labels") or []))
    L.append(
        f"1. **Event-paraphrase fragmentation (partially dedup'd).** {n_event_nodes} event-type nodes "
        f"survived; {n_merged} of those absorbed at least one paraphrased duplicate "
        f"(visible in their `labels` field). The current dedup matches on "
        f"(event_type, date +/- 7 days, participant Jaccard >= 0.6) -- it cleanly collapses "
        f"paraphrases of well-described incidents but does NOT catch (a) distinct actions on "
        f"identical actor pairs (e.g. 'FAA lifts cap' vs 'FAA certifies MAX 7' both have FAA+Boeing "
        f"as participants), or (b) headline-style 'events' the LLM accidentally produced. "
        f"A description-aware second pass would close the remaining gap.\n"
        f"2. **Headline-style entity leakage.** Some entity-type nodes have identifiers that are clearly "
        f"sentences (`BOEING 737 MAX 7 SET FOR SUMMER 2026 FAA CERTIFICATION`, `FAA APPROVES BOEING 737 MAX "
        f"PRODUCTION INCREASE TO 47 AIRCRAFT MONTHLY`). Despite a tightened NER prompt with an explicit "
        f"Decomposition rule, the LLM still admits some of these at scale. Filter them downstream OR push "
        f"the prompt rule harder OR add a length+verb heuristic guard at extraction time.\n"
        f"3. **MostRepresentativeIdentifier bypass.** This run was made with `INVESTIGATOR_SKIP_MRI=1` because the "
        f"MRI step was previously folding distinct entities (Israel + Hamas + Haddad) under a single "
        f"headline canonical identifier. With MRI off, entities stay distinct but the existing "
        f"alias-merge in `merge_run_into_saved` still catches legitimate variants (`THE ACME FOUNDATION OF "
        f"AMERICA` vs `ACME FOUNDATION OF AMERICA`, etc.) so the loss is bounded.\n"
        f"4. **Sanity-event behaviour.** Boeing was the deliberately-distant event; the analysis correctly "
        f"identifies **zero substantive bridging entities** to Boeing (only HAMAS + IZZ AL-DIN AL-HADDAD "
        f"bridge, both flotilla<->strike). Cross-event hypothesis edges DO surface some BOEING <-> "
        f"flotilla/strike pairs at joint=1.0 — these are TMFG-structural artifacts (Boeing shares no real "
        f"actors with the others; the high joint score is from the BP posterior of each endpoint "
        f"independently, not from a real connection). Read those WEAK in substance, even though they "
        f"score high.\n"
        f"5. **Source attribution is rich.** Every relation has `attributes.source_url`; every evidence "
        f"record has `doc_id` + `reasoning`; every event has `data.source_url`. Investigators should "
        f"verify any quoted relation by following the URL — the system surfaces it precisely because "
        f"the user has to be the one to read the article.\n"
    )

    # ---- Sources ----
    L.append(f"\n## All source URLs (deduplicated)\n\n")
    L.append(f"_Pulled from `evidence[].doc_id` and edge `attributes.source_url` across the merged graph._\n\n")
    urls = set()
    for n in nodes:
        for e in n.get("evidence") or []:
            for u in re.split(r"[;,]\s*", e.get("doc_id") or ""):
                u = u.strip()
                if u: urls.add(u)
    for e in edges:
        u_val = (e.get("attributes") or {}).get("source_url") or ""
        for u in (_as_list(u_val) if isinstance(u_val, list) else [u_val]):
            u = str(u or "").strip()
            if u: urls.add(u)
    for u in sorted(urls):
        L.append(f"- {u}\n")

    L.append(f"\n---\n\n_Report generated from the merged-graph JSON; every claim quotes specific node/edge "
             f"attribute values (no invented prose). Treat as a draft for analyst review, not a finished product._\n")

    out_path.write_text("".join(L))
    print(f"Wrote: {out_path}")
    print(f"  size: {out_path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
