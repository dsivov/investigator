"""Self-contained unified-UI prototype generator.

Emits a single HTML file that demonstrates the full Investigation view
from docs/UI_API.md: Overview / Graph / TMFG themes / Data / Report /
Sources tabs, sharing one Cytoscape instance and one payload.

Reads the cross-event investigation JSON and -- if it exists -- the
matching `.customer_report.md` for the Report tab. Falls back to
auto-generating the customer report by reusing
research/build_customer_report.py.

No build step, no server -- open in any modern browser.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse

# Reuse the customer-report logic so the Report tab tracks the same
# voice + sections we deliver to a client.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_customer_report as bcr   # noqa: E402


# ---------------------------------------------------------------------------
# Payload assembly
# ---------------------------------------------------------------------------

def _publisher(url: str) -> str:
    if not url:
        return "unknown"
    try:
        return urlparse(url).netloc.lower().removeprefix("www.") or "unknown"
    except Exception:
        return "unknown"


def _payload(d: dict, report_md: str) -> dict:
    final = d["final_merged_graph"]
    nodes = final["nodes"]
    edges_all = final["edges"]
    themes = final.get("themes", []) or []
    bridges = final.get("bridging_entities", []) or []
    bridge_set = {b["identifier"] for b in bridges}
    run_ids = [ev.get("name") if isinstance(ev, dict) else str(ev)
               for ev in d.get("events", [])]
    n_runs_total = len(run_ids)

    # Dedupe entity/event identifier collisions (NER artefact)
    by_id: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        by_id[n["identifier"]].append(n)
    canonical: dict[str, dict] = {}
    for ident, group in by_id.items():
        ev_first = [n for n in group if n.get("type") == "event"]
        canonical[ident] = ev_first[0] if ev_first else group[0]

    # ---- Coverage stats ----
    fetched = sum(len(b) for s in d["per_event_states"] for b in s.get("article_batches", []))
    body_ok = sum(1 for s in d["per_event_states"] for b in s.get("article_batches", [])
                  for a in b if a.get("text"))
    headline_only = sum(1 for s in d["per_event_states"] for b in s.get("article_batches", [])
                        for a in b if not a.get("text") and (a.get("title") or "").strip())
    publishers_set = sorted({a.get("publisher") for s in d["per_event_states"]
                             for b in s.get("article_batches", []) for a in b
                             if a.get("publisher")})

    # ---- Per-thread node counts (for the asymmetry banner + Overview) ----
    nodes_per_run: dict[str, int] = {r: 0 for r in run_ids}
    for n in canonical.values():
        for r in n.get("runs") or []:
            if r in nodes_per_run:
                nodes_per_run[r] += 1

    asymmetric = False
    sparse_threads = [r for r, c in nodes_per_run.items() if c <= 5]
    rich_threads = [r for r, c in nodes_per_run.items() if c >= 20]
    if sparse_threads and rich_threads:
        asymmetric = True

    # ---- Edges (semantic only, deduped) ----
    edges_out = []
    sources_index: dict[str, list[dict]] = defaultdict(list)
    pair_attested: set[frozenset] = set()
    for e in edges_all:
        s = e.get("src_identifier"); t = e.get("dst_identifier")
        if not (s and t) or s == t:
            continue
        if s not in canonical or t not in canonical:
            continue
        etype = e.get("type") or ""
        if etype not in ("affiliation", "event_participation",
                         "event_followed_by", "event_coincident",
                         "claimed_caused_by"):
            continue
        # context + relation type from the merged 'relations' field
        rel = e.get("relations")
        if isinstance(rel, str):
            try: rel = json.loads(rel)
            except Exception: rel = {}
        if not isinstance(rel, dict):
            rel = {}
        # source URL
        url = ""
        src_field = e.get("source")
        if isinstance(src_field, str) and src_field.startswith("http"):
            url = src_field
        elif isinstance((e.get("attributes") or {}).get("source_url"), str) and (e.get("attributes") or {})["source_url"].startswith("http"):
            url = (e.get("attributes") or {})["source_url"]
        edges_out.append({
            "id": str(len(edges_out)),
            "source": s, "target": t,
            "type": etype, "rtype": rel.get("type") or "",
            "context": (rel.get("context") or "").strip()[:400],
            "url": url,
            "publisher": e.get("source") if isinstance(e.get("source"), str) and not (e.get("source") or "").startswith("http") else "",
        })
        pair_attested.add(frozenset((s, t)))
        if url:
            sources_index[url].append({"backs": f"{s} → {t}", "publisher": _publisher(url)})

    # ---- Nodes for the Graph payload ----
    nodes_out = []
    for ident, n in canonical.items():
        labels = n.get("labels") or []
        clean_labels: list[str] = []
        for lab in labels:
            if isinstance(lab, list) and lab:
                lab = lab[0]
            lab = str(lab).strip()
            if lab and lab.upper() != ident.upper() and lab not in clean_labels:
                clean_labels.append(lab)
        data = (n.get("data") or {})
        nodes_out.append({
            "id": ident,
            "label": ident if len(ident) <= 36 else ident[:33] + "…",
            "type": n.get("type") or "entity",
            "runs": n.get("runs") or [],
            "isBridge": ident in bridge_set,
            "labels": clean_labels[:6],
            "evidenceCount": int(n.get("evidence_count") or 0),
            "posterior": float(n.get("posterior_prob") or 0.0),
            "score": float(n.get("score") or 0.0),
            "data": {k: v for k, v in data.items()
                     if k in ("position", "location", "address", "type",
                              "date", "event_type", "description", "participants")
                     and v and v not in ("Not found", "Unknown")},
        })

    # ---- Themes (for the TMFG tab) ----
    themes_sorted = sorted(themes,
                           key=lambda t: (-len(t.get("runs_spanned") or []),
                                          -(t.get("weight") or 0.0)))
    themes_payload = []
    member_set: set[str] = set()
    seen_sigs: set[tuple] = set()
    for t in themes_sorted:
        members = list(t.get("members") or [])[:4]
        if len(members) < 3:
            continue
        sig = tuple(sorted(members))
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        # Attesting URLs from edges between members
        urls = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pair = frozenset((members[i], members[j]))
                for e in edges_out:
                    if frozenset((e["source"], e["target"])) == pair and e["url"]:
                        urls.append(e["url"])
        # Dedup URLs preserving order
        seen_u = set(); deduped_urls = []
        for u in urls:
            if u not in seen_u:
                deduped_urls.append(u); seen_u.add(u)
        themes_payload.append({
            "idx": len(themes_payload),
            "members": members,
            "weight": float(t.get("weight") or 0.0),
            "runs": t.get("runs_spanned") or [],
            "isCross": bool(t.get("is_cross_investigation")),
            "urls": deduped_urls[:8],
        })
        member_set.update(members)
        if len(themes_payload) >= 20:
            break

    # ---- Bridges (for Overview cards) ----
    bridges_payload = []
    for b in bridges:
        ident = b["identifier"]
        n_b = len(b.get("runs") or [])
        bridges_payload.append({
            "id": ident,
            "runs": b.get("runs") or [],
            "score": float(b.get("score") or 0.0),
            "posterior": float(b.get("posterior_prob") or 0.0),
            "confidence": bcr._bridge_confidence(b, n_runs_total),
            "scope": "all threads" if n_b >= n_runs_total else f"{n_b} of {n_runs_total}",
            "evidenceCount": int((canonical.get(ident) or {}).get("evidence_count") or 0),
        })

    # ---- Sources (Sources tab) ----
    publisher_groups: dict[str, list[dict]] = defaultdict(list)
    for url, rows in sources_index.items():
        publisher_groups[rows[0]["publisher"]].append({
            "url": url,
            "backs": list({r["backs"] for r in rows}),
        })
    sources_payload = sorted(
        ({"publisher": p, "count": sum(1 for _ in rs), "items": rs}
         for p, rs in publisher_groups.items()),
        key=lambda x: -x["count"],
    )
    total_citations = sum(s["count"] for s in sources_payload)
    top3_count = sum(s["count"] for s in sources_payload[:3])
    top3_share = (top3_count / total_citations) if total_citations else 0.0

    # ---- Timeline (mini sparkline of event counts per month) ----
    bucket_counts: Counter = Counter()
    for n in canonical.values():
        if n.get("type") != "event":
            continue
        date = (n.get("data") or {}).get("date") or ""
        if isinstance(date, str) and len(date) >= 7 and date[:7].count("-") == 1:
            bucket_counts[date[:7]] += 1
    timeline = [{"month": k, "count": v}
                for k, v in sorted(bucket_counts.items())]

    return {
        "title": _title_from_runs(run_ids),
        "ref": bcr._ref_id(Path(d.get("session_id") or "investigation")),
        "runs": run_ids,
        "domain": (d.get("params") or {}).get("domain") or "general",
        "period": (d.get("params") or {}).get("period") or "30d",
        "domainLabel": bcr.DOMAIN_LABELS.get((d.get("params") or {}).get("domain") or "general", "Network analysis"),
        "summary": {
            "fetched": fetched,
            "body": body_ok,
            "headline": headline_only,
            "publishers": len(publishers_set),
            "nodes": len(nodes_out),
            "edges": len(edges_out),
            "bridges": len(bridges_payload),
            "themesCross": sum(1 for t in themes_payload if t["isCross"]),
            "asymmetric": asymmetric,
            "sparseThreads": sparse_threads,
            "richThreads": rich_threads,
            "perThreadNodes": nodes_per_run,
        },
        "bridges": bridges_payload,
        "nodes": nodes_out,
        "edges": edges_out,
        "themes": themes_payload,
        "themeMembers": sorted(member_set),
        "timeline": timeline,
        "sources": sources_payload,
        "sourcesMeta": {
            "totalCitations": total_citations,
            "top3Share": round(top3_share, 3),
            "publisherCount": len(sources_payload),
        },
        "report": report_md or "_Customer report not yet generated for this investigation._",
    }


def _title_from_runs(run_ids: list[str]) -> str:
    parts = [r.replace("_", " ").title() for r in run_ids]
    if len(parts) <= 1:
        return parts[0] if parts else "Investigation"
    return " · ".join(parts)


def _get_report_md(json_path: Path, d: dict) -> str:
    """Return the customer-report markdown for this investigation. If a
    `.customer_report.md` sibling exists, use it; otherwise call the
    build_customer_report module to produce one in-memory."""
    md_path = json_path.with_suffix(".customer_report.md")
    if md_path.exists():
        return md_path.read_text()
    # Auto-generate to a temp path
    out = bcr.build_report(json_path)
    return out.read_text()


# ---------------------------------------------------------------------------
# HTML template (single self-contained page)
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r"""<!doctype html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__ — OSINTGraph</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js"></script>
<script src="https://unpkg.com/layout-base@2.0.1/layout-base.js"></script>
<script src="https://unpkg.com/cose-base@2.2.0/cose-base.js"></script>
<script src="https://unpkg.com/cytoscape-fcose@2.2.0/cytoscape-fcose.js"></script>
<script src="https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Serif:wght@400;600&display=swap" rel="stylesheet">
<style>
  html, body { background: #0b1220; color: #e2e8f0; font-family: 'IBM Plex Sans', system-ui, sans-serif; }
  .mono { font-family: 'IBM Plex Mono', ui-monospace, monospace; }
  .serif { font-family: 'IBM Plex Serif', ui-serif, serif; }
  #cy, #cyTmfg { background: #0b1220; }
  #polygons { position: absolute; inset: 0; pointer-events: none; }
  .stage { position: relative; }
  .chip { user-select: none; }
  .chip-on  { background: #1e3a8a; color: #dbeafe; border-color: #2563eb; }
  .chip-off { background: #1e293b; color: #64748b; border-color: #334155; }
  .scrollbar::-webkit-scrollbar { width: 8px; height: 8px; }
  .scrollbar::-webkit-scrollbar-track { background: #0f172a; }
  .scrollbar::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
  .scrollbar::-webkit-scrollbar-thumb:hover { background: #475569; }
  .theme-row.selected { background: #0f2a1f; border-color: #10b981; }
  /* Markdown report styling */
  .report-prose h1 { font-size: 1.6rem; color:#f1f5f9; font-weight:700; margin: 1.5rem 0 .5rem; font-family:'IBM Plex Serif',serif; }
  .report-prose h2 { font-size: 1.2rem; color:#e2e8f0; font-weight:600; margin: 1.3rem 0 .5rem; border-bottom: 1px solid #1e293b; padding-bottom: .25rem; font-family:'IBM Plex Serif',serif;}
  .report-prose h3 { font-size: 1.05rem; color:#cbd5e1; font-weight:600; margin: 1rem 0 .5rem; }
  .report-prose p, .report-prose li { color:#cbd5e1; line-height:1.55; margin:.4rem 0; }
  .report-prose strong { color:#e2e8f0; }
  .report-prose em { color:#94a3b8; }
  .report-prose code { background:#1e293b; color:#fbbf24; padding:0 .25rem; border-radius:.25rem; font-size:.9em; }
  .report-prose a { color:#34d399; }
  .report-prose a:hover { text-decoration: underline; }
  .report-prose hr { border-color:#1e293b; margin:1.5rem 0; }
  .report-prose ul { padding-left: 1.25rem; list-style: disc; }
  .report-prose ol { padding-left: 1.25rem; list-style: decimal; }
  .report-prose blockquote { border-left:3px solid #334155; padding-left:1rem; color:#94a3b8; margin:.5rem 0; font-style: italic; }
  .tab-pane { display: none; }
  .tab-pane.active { display: flex; flex-direction: column; }
  .table-row:hover { background: #0f1f3a; }
  .sortable { cursor: pointer; }
  .sortable.asc::after { content: ' ↑'; color:#10b981; }
  .sortable.desc::after { content: ' ↓'; color:#10b981; }
  .spark-bar { fill: #475569; }
  .spark-bar:hover { fill: #10b981; }
</style>
</head>
<body class="h-screen flex flex-col overflow-hidden">

<!-- Header -->
<header class="flex items-center justify-between border-b border-slate-800 bg-slate-900 px-5 py-3 flex-shrink-0">
  <div class="flex items-center gap-3 min-w-0">
    <div class="text-emerald-400 font-bold tracking-tight text-lg">OSINTGraph</div>
    <div class="text-slate-500">›</div>
    <div class="text-sm text-slate-300 truncate" id="invTitle">—</div>
  </div>
  <div class="flex items-center gap-4 text-xs text-slate-400">
    <span id="invMeta" class="mono">—</span>
  </div>
</header>

<!-- Tabs -->
<nav class="flex items-center gap-1 border-b border-slate-800 bg-slate-900 px-5 flex-shrink-0">
  <button data-tab="overview" class="tab-btn px-3 py-2 text-sm font-medium">Overview</button>
  <button data-tab="graph"    class="tab-btn px-3 py-2 text-sm font-medium">Graph</button>
  <button data-tab="tmfg"     class="tab-btn px-3 py-2 text-sm font-medium">TMFG themes</button>
  <button data-tab="data"     class="tab-btn px-3 py-2 text-sm font-medium">Data</button>
  <button data-tab="report"   class="tab-btn px-3 py-2 text-sm font-medium">Report</button>
  <button data-tab="sources"  class="tab-btn px-3 py-2 text-sm font-medium">Sources</button>
</nav>

<main class="flex-1 flex min-h-0">
  <!-- OVERVIEW -->
  <section data-pane="overview" class="tab-pane active flex-1 overflow-y-auto scrollbar p-6">
    <div id="overviewBanner" class="hidden mb-5 rounded-lg border border-amber-700/60 bg-amber-900/15 p-4 flex items-start gap-3 text-sm">
      <span class="text-amber-400 text-xl leading-none">⚠</span>
      <div>
        <div class="font-semibold text-amber-200">Asymmetric corpus detected</div>
        <div class="mt-1 text-amber-100/80" id="bannerBody"></div>
      </div>
    </div>

    <div class="grid grid-cols-12 gap-5">
      <!-- Bridges -->
      <div class="col-span-7 rounded-xl border border-slate-800 bg-slate-900 p-5">
        <div class="flex items-center justify-between mb-3">
          <div class="text-slate-200 font-semibold">Cross-thread bridges</div>
          <div class="text-xs text-slate-500" id="bridgeCountLabel"></div>
        </div>
        <div id="bridgeList" class="space-y-2 text-sm"></div>
      </div>

      <!-- Coverage -->
      <div class="col-span-5 rounded-xl border border-slate-800 bg-slate-900 p-5">
        <div class="text-slate-200 font-semibold mb-3">Coverage</div>
        <div class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          <div class="text-slate-400">Articles fetched</div><div class="mono text-slate-200 text-right" id="covFetched">—</div>
          <div class="text-slate-400">  ↳ full body</div>   <div class="mono text-slate-300 text-right" id="covBody">—</div>
          <div class="text-slate-400">  ↳ headline only</div><div class="mono text-slate-300 text-right" id="covHeadline">—</div>
          <div class="text-slate-400">Publishers</div>      <div class="mono text-slate-200 text-right" id="covPubs">—</div>
          <div class="text-slate-400">Body-fetch failure</div><div class="mono text-slate-200 text-right" id="covFailPct">—</div>
        </div>
        <div class="mt-4 pt-3 border-t border-slate-800">
          <div class="text-xs text-slate-500 mb-2">Per thread (nodes extracted)</div>
          <div id="perThreadList" class="space-y-1.5 text-sm"></div>
        </div>
      </div>

      <!-- Themes -->
      <div class="col-span-7 rounded-xl border border-slate-800 bg-slate-900 p-5">
        <div class="flex items-center justify-between mb-3">
          <div class="text-slate-200 font-semibold">Top cross-thread themes</div>
          <button class="tab-jump text-xs text-emerald-400 hover:underline" data-jump="tmfg">all →</button>
        </div>
        <ol class="space-y-1.5 text-sm" id="themeShortList"></ol>
      </div>

      <!-- Timeline mini -->
      <div class="col-span-5 rounded-xl border border-slate-800 bg-slate-900 p-5">
        <div class="text-slate-200 font-semibold mb-3">Event density over time</div>
        <svg id="timelineSpark" width="100%" height="80" viewBox="0 0 300 80"></svg>
        <div id="timelineRange" class="text-xs text-slate-500 mt-1 mono">—</div>
      </div>

      <!-- Recommendations -->
      <div class="col-span-12 rounded-xl border border-slate-800 bg-slate-900 p-5">
        <div class="text-slate-200 font-semibold mb-3">Recommended follow-up</div>
        <ul id="recList" class="list-disc list-inside space-y-1 text-sm text-slate-300"></ul>
      </div>
    </div>
  </section>

  <!-- GRAPH -->
  <section data-pane="graph" class="tab-pane flex-1 flex-col min-h-0">
    <div class="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/60 px-5 py-2 text-xs flex-shrink-0">
      <div class="flex items-center gap-1">
        <span class="text-slate-500 mr-1">Threads</span><div id="threadChips" class="flex gap-1"></div>
      </div>
      <span class="text-slate-700">·</span>
      <div class="flex items-center gap-1">
        <span class="text-slate-500 mr-1">Types</span>
        <button data-type="entity" class="chip chip-on rounded-md border px-2 py-1">Actor</button>
        <button data-type="event"  class="chip chip-on rounded-md border px-2 py-1">Event</button>
      </div>
      <span class="text-slate-700">·</span>
      <div class="flex items-center gap-2">
        <span class="text-slate-500">Min articles</span>
        <input id="minEv" type="range" min="0" max="20" value="0" class="accent-emerald-500" />
        <span id="minEvVal" class="mono w-6 text-slate-300">0</span>
      </div>
      <span class="text-slate-700">·</span>
      <div class="flex items-center gap-1">
        <span class="text-slate-500 mr-1">Layout</span>
        <select id="layout" class="bg-slate-800 border border-slate-700 rounded px-2 py-1">
          <option value="fcose">Force (fcose)</option>
          <option value="cose">Spring (cose)</option>
          <option value="concentric">Concentric</option>
          <option value="breadthfirst">Hierarchy</option>
          <option value="circle">Circle</option>
        </select>
      </div>
      <button id="fitBtn" class="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300">Fit</button>
      <div class="ml-auto text-slate-400 mono" id="filterStatus">—</div>
    </div>
    <div class="flex-1 flex min-h-0">
      <div id="cy" class="flex-1"></div>
      <aside id="side" class="w-[420px] flex-shrink-0 border-l border-slate-800 bg-slate-900 overflow-y-auto scrollbar p-5 text-sm">
        <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Selection</div>
        <div id="selectionEmpty" class="text-slate-500 italic">
          Click an entity in the graph to see its attested role, sources, and relationships.
        </div>
        <div id="selectionPanel" class="hidden"></div>
      </aside>
    </div>
  </section>

  <!-- TMFG -->
  <section data-pane="tmfg" class="tab-pane flex-1 flex-col min-h-0">
    <div class="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/60 px-5 py-2 text-xs flex-shrink-0">
      <div class="flex items-center gap-1">
        <span class="text-slate-500 mr-1">Show top</span>
        <select id="tmfgTopN" class="bg-slate-800 border border-slate-700 rounded px-2 py-1">
          <option value="5">5 themes</option>
          <option value="8" selected>8 themes</option>
          <option value="12">12 themes</option>
          <option value="0">All</option>
        </select>
      </div>
      <span class="text-slate-700">·</span>
      <div class="flex items-center gap-1">
        <span class="text-slate-500 mr-1">Edges</span>
        <button data-edgekind="attested" class="chip chip-on rounded-md border px-2 py-1">Attested</button>
        <button data-edgekind="fillin"   class="chip chip-on rounded-md border px-2 py-1">TMFG fill-in</button>
      </div>
      <span class="text-slate-700">·</span>
      <button id="tmfgFitBtn"   class="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300">Fit</button>
      <button id="tmfgReLayout" class="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300">Re-layout</button>
      <div class="ml-auto text-slate-400 mono" id="tmfgStatus">—</div>
    </div>
    <div class="flex-1 flex min-h-0">
      <div class="stage flex-1">
        <div id="cyTmfg" class="absolute inset-0"></div>
        <svg id="polygons" xmlns="http://www.w3.org/2000/svg"></svg>
      </div>
      <aside class="w-[440px] flex-shrink-0 border-l border-slate-800 bg-slate-900 overflow-y-auto scrollbar p-5 text-sm">
        <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Themes</div>
        <div id="themeList" class="space-y-2"></div>
        <hr class="border-slate-800 my-4"/>
        <div id="themeDetail" class="text-slate-400 italic text-xs">
          Click a theme to see its members and attesting source articles.
        </div>
      </aside>
    </div>
  </section>

  <!-- DATA -->
  <section data-pane="data" class="tab-pane flex-1 flex-col min-h-0">
    <div class="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/60 px-5 py-2 text-xs flex-shrink-0">
      <div class="flex items-center gap-1">
        <span class="text-slate-500 mr-1">View</span>
        <button data-view="entities"      class="chip chip-on rounded-md border px-2 py-1">Actors</button>
        <button data-view="events"        class="chip chip-off rounded-md border px-2 py-1">Events</button>
        <button data-view="relationships" class="chip chip-off rounded-md border px-2 py-1">Relationships</button>
      </div>
      <span class="text-slate-700">·</span>
      <input id="dataSearch" type="text" placeholder="Search…" class="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-200 placeholder-slate-500 w-64"/>
      <div class="ml-auto text-slate-400 mono" id="dataStatus">—</div>
    </div>
    <div class="flex-1 overflow-auto scrollbar p-4">
      <table id="dataTable" class="min-w-full text-sm border-separate border-spacing-0">
        <thead class="sticky top-0 bg-slate-900 z-10">
          <tr id="dataHead"></tr>
        </thead>
        <tbody id="dataBody"></tbody>
      </table>
    </div>
  </section>

  <!-- REPORT -->
  <section data-pane="report" class="tab-pane flex-1 flex-col min-h-0">
    <div class="flex-1 flex min-h-0">
      <aside class="w-64 flex-shrink-0 border-r border-slate-800 bg-slate-900 overflow-y-auto scrollbar p-4 text-sm">
        <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Contents</div>
        <ul id="reportToc" class="space-y-1"></ul>
      </aside>
      <article id="reportBody" class="report-prose flex-1 overflow-y-auto scrollbar p-8 max-w-4xl mx-auto"></article>
    </div>
  </section>

  <!-- SOURCES -->
  <section data-pane="sources" class="tab-pane flex-1 flex-col overflow-hidden">
    <div class="border-b border-slate-800 bg-slate-900 p-5 flex items-center gap-6 text-sm flex-shrink-0">
      <div>
        <div class="text-2xl font-bold text-slate-100 mono" id="srcPubCount">—</div>
        <div class="text-xs text-slate-500">Publishers</div>
      </div>
      <div>
        <div class="text-2xl font-bold text-slate-100 mono" id="srcCiteCount">—</div>
        <div class="text-xs text-slate-500">Citations</div>
      </div>
      <div>
        <div class="text-2xl font-bold text-slate-100 mono" id="srcTop3">—</div>
        <div class="text-xs text-slate-500">Top-3 share</div>
      </div>
      <div class="ml-auto text-xs text-slate-500" id="srcDiversity">—</div>
    </div>
    <div class="flex-1 overflow-y-auto scrollbar p-5">
      <table class="min-w-full text-sm">
        <thead class="text-slate-500 text-xs uppercase tracking-wider"><tr>
          <th class="text-left py-1">Publisher</th><th class="text-right py-1">Citations</th><th class="text-left py-1 pl-6">Backs</th>
        </tr></thead>
        <tbody id="srcBody"></tbody>
      </table>
    </div>
  </section>
</main>

<footer class="border-t border-slate-800 bg-slate-900 px-5 py-2 text-xs text-slate-500 mono flex-shrink-0">
  <span id="footerStats">—</span>
</footer>

<script>
const PAYLOAD = __PAYLOAD__;
</script>

<script>
// ===================== Shared state =====================
const THREAD_PALETTE = ['#3b82f6','#ef4444','#f59e0b','#a855f7','#10b981','#06b6d4','#ec4899'];
const POLYGON_PALETTE = ['#10b981','#ef4444','#3b82f6','#f59e0b','#a855f7','#06b6d4','#ec4899','#f43f5e','#84cc16','#0ea5e9','#fb7185','#22c55e'];
const ETYPE_COLOR = {
  affiliation: '#475569', event_participation: '#b45309',
  event_followed_by: '#7e22ce', event_coincident: '#7e22ce',
  claimed_caused_by: '#dc2626'
};

const threadColour = {};
PAYLOAD.runs.forEach((r, i) => threadColour[r] = THREAD_PALETTE[i % THREAD_PALETTE.length]);

document.getElementById('invTitle').textContent = PAYLOAD.title;
document.getElementById('invMeta').textContent =
  `${PAYLOAD.runs.length} threads · ${PAYLOAD.domain.replace(/_/g,' ')} · ${PAYLOAD.period} · ${PAYLOAD.ref}`;
document.getElementById('footerStats').textContent =
  `${PAYLOAD.summary.nodes} nodes · ${PAYLOAD.summary.edges} edges · ${PAYLOAD.summary.bridges} bridges · ${PAYLOAD.summary.themesCross} cross-thread themes`;

// ===================== Tab routing =====================
function activateTab(name) {
  document.querySelectorAll('.tab-btn').forEach(b => {
    const on = b.dataset.tab === name;
    b.classList.toggle('text-emerald-300', on);
    b.classList.toggle('text-slate-500', !on);
    b.classList.toggle('border-b-2', on);
    b.classList.toggle('border-emerald-400', on);
  });
  document.querySelectorAll('.tab-pane').forEach(p => {
    p.classList.toggle('active', p.dataset.pane === name);
  });
  if (name === 'graph') ensureGraphCy();
  if (name === 'tmfg')  { ensureTmfgCy(); requestAnimationFrame(drawPolygons); }
  if (name === 'report' && !reportRendered) renderReport();
}
document.querySelectorAll('.tab-btn').forEach(b => b.addEventListener('click', () => activateTab(b.dataset.tab)));
document.querySelectorAll('.tab-jump').forEach(b => b.addEventListener('click', () => activateTab(b.dataset.jump)));
activateTab('overview');

// ===================== OVERVIEW =====================
function renderOverview() {
  // Banner
  if (PAYLOAD.summary.asymmetric) {
    document.getElementById('overviewBanner').classList.remove('hidden');
    const sparse = PAYLOAD.summary.sparseThreads.map(s => `<code class="mono text-amber-200">${escapeHtml(s)}</code>`).join(', ');
    document.getElementById('bannerBody').innerHTML =
      `Thread(s) ${sparse} returned five or fewer extracted entities. Any hypothesis that depends on the sparse thread cannot be tested from this corpus alone; the absence of bridges is not evidence the connection does not exist.`;
  }
  // Bridges
  const bl = document.getElementById('bridgeList');
  document.getElementById('bridgeCountLabel').textContent =
    `${PAYLOAD.bridges.length} bridge(s) · ${PAYLOAD.summary.themesCross} cross-thread themes`;
  if (PAYLOAD.bridges.length === 0) {
    bl.innerHTML = '<div class="text-slate-500 italic text-sm">No actor was attested across multiple threads.</div>';
  } else {
    bl.innerHTML = PAYLOAD.bridges.slice(0, 8).map(b => {
      const dots = b.runs.map(r => `<span class="inline-block w-2 h-2 rounded-full" title="${escapeAttr(r)}" style="background:${threadColour[r] || '#64748b'}"></span>`).join(' ');
      return `<div class="flex items-center justify-between border-b border-slate-800/60 py-1 last:border-0">
        <div class="flex items-center gap-2">
          <span class="text-slate-100 font-semibold">${escapeHtml(b.id)}</span>
          <span class="flex gap-0.5">${dots}</span>
          <span class="text-xs text-slate-500">${escapeHtml(b.scope)}</span>
        </div>
        <span class="text-xs px-2 py-0.5 rounded-md ${b.confidence === 'Almost certain' ? 'bg-emerald-900/50 text-emerald-300' : b.confidence === 'Highly likely' ? 'bg-emerald-900/30 text-emerald-300' : b.confidence === 'Likely' ? 'bg-blue-900/40 text-blue-300' : 'bg-slate-800 text-slate-400'}">${escapeHtml(b.confidence)}</span>
      </div>`;
    }).join('');
  }
  // Coverage
  document.getElementById('covFetched').textContent  = PAYLOAD.summary.fetched;
  document.getElementById('covBody').textContent     = PAYLOAD.summary.body;
  document.getElementById('covHeadline').textContent = PAYLOAD.summary.headline;
  document.getElementById('covPubs').textContent     = PAYLOAD.summary.publishers;
  const failPct = PAYLOAD.summary.fetched ? (PAYLOAD.summary.headline / PAYLOAD.summary.fetched * 100).toFixed(0) : '0';
  document.getElementById('covFailPct').textContent = failPct + '%';
  // Per-thread bars
  const max = Math.max(...Object.values(PAYLOAD.summary.perThreadNodes), 1);
  document.getElementById('perThreadList').innerHTML = Object.entries(PAYLOAD.summary.perThreadNodes).map(([r, c]) => {
    const pct = (c / max * 100).toFixed(0);
    return `<div class="flex items-center gap-2">
      <span class="text-xs w-44 truncate" title="${escapeAttr(r)}" style="color:${threadColour[r] || '#64748b'}">${escapeHtml(r)}</span>
      <div class="flex-1 bg-slate-800 rounded h-2"><div class="h-2 rounded" style="background:${threadColour[r] || '#64748b'};width:${pct}%"></div></div>
      <span class="mono text-xs text-slate-400 w-8 text-right">${c}</span>
    </div>`;
  }).join('');
  // Themes shortlist
  const tl = document.getElementById('themeShortList');
  const top = PAYLOAD.themes.filter(t => t.isCross).slice(0, 6);
  tl.innerHTML = top.map((t, i) => {
    const colour = POLYGON_PALETTE[i % POLYGON_PALETTE.length];
    return `<li class="flex items-baseline gap-2">
      <span class="inline-block w-2.5 h-2.5 rounded-sm flex-shrink-0" style="background:${colour}; opacity:.5; border:1px solid ${colour}"></span>
      <span class="text-slate-300 truncate flex-1">${t.members.map(escapeHtml).join(' · ')}</span>
      <span class="text-xs text-slate-500 mono">w ${t.weight.toFixed(1)}</span>
    </li>`;
  }).join('') || '<li class="text-slate-500 italic text-sm">No cross-thread themes.</li>';
  // Timeline sparkline
  drawTimelineSpark();
  // Recommendations
  const recs = [];
  if (PAYLOAD.summary.sparseThreads.length) {
    recs.push(`Deepen sourcing on the sparse thread(s) (${PAYLOAD.summary.sparseThreads.join(', ')}): consider additional named queries, an extended timeframe, or non-news sourcing.`);
  }
  if (PAYLOAD.bridges.filter(b => b.scope === 'all threads').length === 0) {
    recs.push(`No actor bridged every thread. Open a fourth query anchored on the strongest two-thread bridge to test whether the chain closes.`);
  }
  recs.push(`Triangulate cross-thread bridges against non-news sources (corporate registries, sanctions lists, court dockets, vessel-tracking) before any operational conclusion.`);
  recs.push(`Re-run with a 3-year window to capture long-lived intermediaries (financial conduits, shell entities) the rolling-${PAYLOAD.period} window may miss.`);
  document.getElementById('recList').innerHTML = recs.map(r => `<li>${escapeHtml(r)}</li>`).join('');
}

function drawTimelineSpark() {
  const svg = document.getElementById('timelineSpark');
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const data = PAYLOAD.timeline;
  if (!data.length) {
    document.getElementById('timelineRange').textContent = 'No dated events.';
    return;
  }
  const W = 300, H = 80, P = 6;
  const max = Math.max(...data.map(d => d.count), 1);
  const w = (W - P*2) / data.length;
  data.forEach((d, i) => {
    const bh = (d.count / max) * (H - 2*P);
    const x = P + i * w;
    const y = H - P - bh;
    const r = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    r.setAttribute('class', 'spark-bar');
    r.setAttribute('x', x); r.setAttribute('y', y);
    r.setAttribute('width', Math.max(1, w - 1));
    r.setAttribute('height', bh);
    const t = document.createElementNS('http://www.w3.org/2000/svg', 'title');
    t.textContent = `${d.month}: ${d.count} event(s)`;
    r.appendChild(t);
    svg.appendChild(r);
  });
  document.getElementById('timelineRange').textContent = `${data[0].month}  →  ${data[data.length-1].month}  (peak ${max})`;
}

renderOverview();

// ===================== GRAPH tab =====================
let cy = null;
function ensureGraphCy() {
  if (cy) return;
  // Filter chips
  const tcEl = document.getElementById('threadChips');
  const threadsOn = new Set(PAYLOAD.runs);
  PAYLOAD.runs.forEach(r => {
    const b = document.createElement('button');
    b.dataset.thread = r;
    b.className = 'chip chip-on rounded-md border px-2 py-1';
    b.innerHTML = `<span class="inline-block w-2 h-2 rounded-full mr-1 align-middle" style="background:${threadColour[r]}"></span>${escapeHtml(r)}`;
    b.onclick = () => { if (threadsOn.has(r)) { threadsOn.delete(r); b.classList.replace('chip-on','chip-off'); } else { threadsOn.add(r); b.classList.replace('chip-off','chip-on'); } applyGraphFilters(threadsOn); };
    tcEl.appendChild(b);
  });
  const typesOn = new Set(['entity','event']);
  document.querySelectorAll('[data-type]').forEach(b => {
    b.onclick = () => { const t = b.dataset.type; if (typesOn.has(t)) { typesOn.delete(t); b.classList.replace('chip-on','chip-off'); } else { typesOn.add(t); b.classList.replace('chip-off','chip-on'); } applyGraphFilters(threadsOn, typesOn); };
  });
  document.getElementById('minEv').oninput = e => { document.getElementById('minEvVal').textContent = e.target.value; applyGraphFilters(threadsOn, typesOn); };
  document.getElementById('layout').onchange = e => runLayout(e.target.value);
  document.getElementById('fitBtn').onclick = () => cy.fit(undefined, 40);

  cy = cytoscape({
    container: document.getElementById('cy'),
    wheelSensitivity: 0.2,
    elements: [
      ...PAYLOAD.nodes.map(n => ({
        data: { ...n, faceColour: n.isBridge ? '#10b981' : (threadColour[n.runs[0]] || '#64748b') },
        classes: (n.type === 'event' ? 'is-event' : 'is-actor') + (n.isBridge ? ' is-bridge' : '')
      })),
      ...PAYLOAD.edges.map(e => ({ data: { ...e, edgeColour: ETYPE_COLOR[e.type] || '#475569' } })),
    ],
    style: [
      { selector: 'node', style: { 'background-color': 'data(faceColour)', 'label': 'data(label)', 'color': '#e2e8f0', 'text-margin-y': -10, 'font-size': 10, 'font-family': 'IBM Plex Sans, system-ui, sans-serif', 'text-outline-color': '#0b1220', 'text-outline-width': 2, 'border-width': 1.5, 'border-color': '#0f172a', 'width': 26, 'height': 26 }},
      { selector: 'node.is-event', style: { 'shape': 'diamond', 'width': 22, 'height': 22 }},
      { selector: 'node.is-bridge', style: { 'border-color': '#10b981', 'border-width': 3.5, 'width': 38, 'height': 38, 'font-size': 12, 'font-weight': 700 }},
      { selector: 'node:selected', style: { 'border-color': '#fde68a', 'border-width': 4 }},
      { selector: 'node.dim', style: { 'opacity': 0.18 }},
      { selector: 'edge', style: { 'curve-style': 'bezier', 'line-color': 'data(edgeColour)', 'target-arrow-color': 'data(edgeColour)', 'target-arrow-shape': 'triangle', 'width': 1.2, 'arrow-scale': 0.9, 'opacity': 0.75 }},
      { selector: 'edge[type = "event_followed_by"], edge[type = "event_coincident"]', style: { 'line-style': 'dashed' }},
      { selector: 'edge[type = "claimed_caused_by"]', style: { 'width': 2.5 }},
      { selector: 'edge.dim', style: { 'opacity': 0.1 }},
    ],
  });
  cy.on('tap', 'node', evt => showEntity(evt.target.id()));
  cy.on('tap', evt => { if (evt.target === cy) clearHighlight(); });
  applyGraphFilters(threadsOn, typesOn);
  runLayout('fcose');
}

function applyGraphFilters(threadsOn, typesOn = new Set(['entity','event'])) {
  if (!cy) return;
  const minEv = +document.getElementById('minEv').value;
  let visible = 0;
  cy.nodes().forEach(n => {
    const d = n.data();
    const show = d.runs.some(r => threadsOn.has(r)) && typesOn.has(d.type) && d.evidenceCount >= minEv;
    n.style('display', show ? 'element' : 'none');
    if (show) visible++;
  });
  cy.edges().forEach(e => {
    const sVis = e.source().style('display') !== 'none';
    const tVis = e.target().style('display') !== 'none';
    e.style('display', (sVis && tVis) ? 'element' : 'none');
  });
  document.getElementById('filterStatus').textContent = `${visible} of ${PAYLOAD.nodes.length} nodes visible`;
}

function runLayout(name) {
  if (!cy) return;
  const cfg = name === 'fcose'
    ? { name: 'fcose', animate: true, randomize: false, nodeRepulsion: 7000, idealEdgeLength: 90, gravity: 0.25 }
    : { name, animate: true, padding: 40 };
  cy.layout(cfg).run();
}

function showEntity(id) {
  const n = PAYLOAD.nodes.find(n => n.id === id);
  if (!n) return;
  cy.elements().addClass('dim');
  const target = cy.getElementById(id);
  target.closedNeighborhood().removeClass('dim');

  const incoming = PAYLOAD.edges.filter(e => e.target === id);
  const outgoing = PAYLOAD.edges.filter(e => e.source === id);

  const side = document.getElementById('selectionPanel');
  document.getElementById('selectionEmpty').classList.add('hidden');
  side.classList.remove('hidden');

  const bridgePill = n.isBridge ? `<span class="inline-block bg-emerald-900/40 text-emerald-300 text-xs rounded px-2 py-0.5 ml-2">Bridge · ${n.runs.length} threads</span>` : '';
  const typePill = `<span class="inline-block bg-slate-700/60 text-slate-300 text-xs rounded px-2 py-0.5 ml-2">${n.type === 'event' ? 'Event' : 'Actor'}</span>`;
  const threadDots = n.runs.map(r => `<span class="inline-block w-2 h-2 rounded-full mr-1" title="${escapeAttr(r)}" style="background:${threadColour[r] || '#64748b'}"></span>`).join('');
  const labelsHtml = n.labels.length ? `<div class="mt-2 text-xs text-slate-400"><span class="text-slate-500">Also known as:</span> ${n.labels.map(l => `<span class="mono text-slate-300">${escapeHtml(l)}</span>`).join(' · ')}</div>` : '';

  const rows = [];
  if (n.data.position)   rows.push(['Role', n.data.position]);
  if (n.data.location)   rows.push(['Location', n.data.location]);
  if (n.data.event_type) rows.push(['Event type', n.data.event_type]);
  if (n.data.date)       rows.push(['Date', n.data.date]);
  if (n.data.description) rows.push(['Description', n.data.description.slice(0,260) + (n.data.description.length>260 ? '…' : '')]);
  const dataHtml = rows.length ? `<div class="mt-3 grid grid-cols-[110px_1fr] gap-y-1 text-xs">${rows.map(([k,v]) => `<div class="text-slate-500">${escapeHtml(k)}</div><div class="text-slate-200">${escapeHtml(String(v))}</div>`).join('')}</div>` : '';

  function relRow(e, dir) {
    const other = dir === 'out' ? e.target : e.source;
    const arrow = dir === 'out' ? '→' : '←';
    const ctx = e.context ? `<div class="text-xs text-slate-400 mt-0.5 leading-snug">"${escapeHtml(e.context)}"</div>` : '';
    const src = e.url ? `<a class="text-emerald-400 hover:underline text-xs mono" target="_blank" href="${e.url}">${publisherOf(e.url)}</a>` : (e.publisher ? `<span class="text-slate-500 text-xs">${escapeHtml(e.publisher)}</span>` : '');
    const rt = e.rtype ? `<span class="text-xs text-slate-500 italic">${escapeHtml(e.rtype)}</span>` : '';
    return `<li class="border-l-2 pl-3 py-1" style="border-color:${ETYPE_COLOR[e.type] || '#475569'}">
      <div class="text-slate-200"><span class="text-slate-500 mono">${arrow}</span> <a href="#" class="hover:underline" data-goto="${escapeAttr(other)}">${escapeHtml(other)}</a> ${rt}</div>
      ${ctx} ${src ? `<div class="mt-0.5">${src}</div>` : ''}
    </li>`;
  }

  const total = outgoing.length + incoming.length;
  side.innerHTML = `
    <div class="flex items-center justify-between gap-2">
      <div class="text-lg font-semibold text-slate-100 leading-tight">${escapeHtml(n.id)}</div>
      <button id="closeSel" class="text-slate-500 hover:text-slate-200">×</button>
    </div>
    <div class="mt-1 flex items-center gap-1">${threadDots}${typePill}${bridgePill}</div>
    <div class="mt-2 text-xs text-slate-400"><span class="mono">${n.evidenceCount}</span> attesting article(s) · structural score <span class="mono">${(n.score).toFixed(2)}</span></div>
    ${labelsHtml}${dataHtml}
    ${total ? `<div class="mt-4"><div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Attested relationships (${total})</div><ul class="space-y-1">${outgoing.map(e => relRow(e, 'out')).join('')}${incoming.map(e => relRow(e, 'in')).join('')}</ul></div>` : `<div class="mt-4 text-slate-500 text-sm italic">No attested relationships in this corpus.</div>`}
  `;
  document.getElementById('closeSel').onclick = clearHighlight;
  side.querySelectorAll('[data-goto]').forEach(a => {
    a.onclick = ev => { ev.preventDefault(); const ele = cy.getElementById(a.dataset.goto); if (ele.length) { cy.center(ele); ele.select(); showEntity(a.dataset.goto); } };
  });
}

function clearHighlight() {
  if (!cy) return;
  cy.elements().removeClass('dim');
  cy.elements().unselect();
  document.getElementById('selectionPanel').classList.add('hidden');
  document.getElementById('selectionEmpty').classList.remove('hidden');
}

// ===================== TMFG tab =====================
let cyTmfg = null;
let selectedThemeIdx = null;
const tmfgEdgeKinds = new Set(['attested','fillin']);
let tmfgCrossOnly = true;

function currentThemes() {
  const topN = +document.getElementById('tmfgTopN').value;
  const arr = tmfgCrossOnly ? PAYLOAD.themes.filter(t => (t.runs || []).length >= 2) : PAYLOAD.themes;
  return topN > 0 ? arr.slice(0, topN) : arr;
}

function ensureTmfgCy() {
  if (cyTmfg) { drawPolygons(); return; }
  document.querySelectorAll('[data-edgekind]').forEach(btn => {
    btn.onclick = () => {
      const k = btn.dataset.edgekind;
      if (tmfgEdgeKinds.has(k)) { tmfgEdgeKinds.delete(k); btn.classList.replace('chip-on','chip-off'); } else { tmfgEdgeKinds.add(k); btn.classList.replace('chip-off','chip-on'); }
      applyTmfgEdgeFilter();
    };
  });
  document.getElementById('tmfgTopN').onchange = rebuildTmfg;
  document.getElementById('tmfgFitBtn').onclick = () => cyTmfg && cyTmfg.fit(undefined, 50);
  document.getElementById('tmfgReLayout').onclick = runTmfgLayout;
  rebuildTmfg();
}

function rebuildTmfg() {
  const themesShown = currentThemes();
  const memberSet = new Set();
  themesShown.forEach(t => t.members.forEach(m => memberSet.add(m)));
  const nodes = PAYLOAD.nodes.filter(n => memberSet.has(n.id));
  // Build edges: attested between members + fill-in for tetrahedron-internal unattested pairs
  const attestedPairs = new Set();
  const attestedList = PAYLOAD.edges.filter(e => memberSet.has(e.source) && memberSet.has(e.target));
  attestedList.forEach(e => attestedPairs.add(pairKey(e.source, e.target)));
  const fillinList = [];
  themesShown.forEach(t => {
    const m = t.members;
    for (let i = 0; i < m.length; i++) {
      for (let j = i+1; j < m.length; j++) {
        const k = pairKey(m[i], m[j]);
        if (!attestedPairs.has(k) && memberSet.has(m[i]) && memberSet.has(m[j])) {
          attestedPairs.add(k);
          fillinList.push({ source: m[i], target: m[j], kind: 'fillin' });
        }
      }
    }
  });
  const attestedMarked = attestedList.map((e, i) => ({...e, kind:'attested', id:'a'+i }));
  const fillinMarked = fillinList.map((e, i) => ({...e, kind:'fillin', id:'f'+i, type:'tmfg_hypothesis' }));

  if (cyTmfg) cyTmfg.destroy();
  cyTmfg = cytoscape({
    container: document.getElementById('cyTmfg'),
    wheelSensitivity: 0.2,
    elements: [
      ...nodes.map(n => ({ data:{...n, faceColour: n.isBridge ? '#10b981' : (threadColour[n.runs[0]] || '#64748b')}, classes:(n.type==='event'?'is-event':'is-actor')+(n.isBridge?' is-bridge':'') })),
      ...attestedMarked.map(e => ({ data:{...e, edgeColour:'#52525b' } })),
      ...fillinMarked.map(e => ({ data:{...e, edgeColour:'#fb923c' } })),
    ],
    style: [
      { selector:'node', style:{ 'background-color':'data(faceColour)','label':'data(label)','color':'#e2e8f0','font-size':10,'text-margin-y':-10,'font-family':"IBM Plex Sans, system-ui, sans-serif",'text-outline-color':'#0b1220','text-outline-width':2,'border-width':1.5,'border-color':'#0f172a','width':26,'height':26 }},
      { selector:'node.is-event', style:{ 'shape':'diamond','width':22,'height':22 }},
      { selector:'node.is-bridge', style:{ 'border-color':'#10b981','border-width':3.5,'width':40,'height':40,'font-size':12,'font-weight':700 }},
      { selector:'edge', style:{ 'curve-style':'bezier','line-color':'data(edgeColour)','target-arrow-color':'data(edgeColour)','width':1.2,'opacity':0.75 }},
      { selector:'edge[kind = "fillin"]', style:{ 'line-style':'dashed','target-arrow-shape':'none' }},
      { selector:'edge[kind = "attested"]', style:{ 'target-arrow-shape':'triangle','arrow-scale':0.9 }},
      { selector:'.dim', style:{ 'opacity':0.12 }},
    ],
  });
  cyTmfg.on('pan zoom resize layoutstop', drawPolygons);
  cyTmfg.on('tap', evt => { if (evt.target === cyTmfg) selectTheme(null); });
  applyTmfgEdgeFilter();
  runTmfgLayout();
  renderThemeList(themesShown);
  selectTheme(null);
  document.getElementById('tmfgStatus').textContent = `${themesShown.length} themes · ${nodes.length} actors · ${attestedMarked.length + fillinMarked.length} edges (${fillinMarked.length} fill-in)`;
}

function runTmfgLayout() {
  if (!cyTmfg) return;
  cyTmfg.layout({ name:'fcose', animate:true, randomize:false, nodeRepulsion:6500, idealEdgeLength:90, gravity:0.3 }).run();
}

function applyTmfgEdgeFilter() {
  if (!cyTmfg) return;
  cyTmfg.edges().forEach(e => {
    e.style('display', tmfgEdgeKinds.has(e.data('kind')) ? 'element' : 'none');
  });
}

function renderThemeList(themesShown) {
  const wrap = document.getElementById('themeList');
  if (!themesShown.length) { wrap.innerHTML = '<div class="text-slate-500 italic text-xs">No themes match.</div>'; return; }
  wrap.innerHTML = themesShown.map((t, i) => {
    const colour = POLYGON_PALETTE[i % POLYGON_PALETTE.length];
    const runs = t.runs.map(r => `<span class="inline-block w-2 h-2 rounded-full" style="background:${threadColour[r] || '#64748b'}"></span>`).join(' ');
    return `<div class="theme-row border border-slate-800 rounded-md p-2 cursor-pointer hover:border-slate-600" data-theme="${i}">
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2">
          <span class="inline-block w-3 h-3 rounded" style="background:${colour}; opacity:.4; border:1px solid ${colour}"></span>
          <span class="text-slate-300 text-xs font-semibold">Theme ${i+1}</span>
          <span class="text-slate-500 text-[10px] mono">w ${t.weight.toFixed(1)}</span>
        </div>
        <div class="flex gap-0.5">${runs}</div>
      </div>
      <div class="mt-1 text-[11px] text-slate-400 truncate">${t.members.map(escapeHtml).join(' · ')}</div>
    </div>`;
  }).join('');
  wrap.querySelectorAll('.theme-row').forEach(r => { r.onclick = () => selectTheme(+r.dataset.theme); });
}

function selectTheme(idx) {
  selectedThemeIdx = idx;
  document.querySelectorAll('.theme-row').forEach(r => r.classList.toggle('selected', +r.dataset.theme === idx));
  drawPolygons();
  const det = document.getElementById('themeDetail');
  if (idx == null) {
    det.innerHTML = `<div class="text-slate-400 italic text-xs">Click a theme to see its members and attesting source articles.</div>`;
    if (cyTmfg) cyTmfg.nodes().removeClass('dim');
    return;
  }
  const t = currentThemes()[idx];
  if (!t) return;
  if (cyTmfg) { cyTmfg.nodes().addClass('dim'); t.members.forEach(m => cyTmfg.getElementById(m).removeClass('dim')); }
  const colour = POLYGON_PALETTE[idx % POLYGON_PALETTE.length];
  const urls = t.urls.map(u => `<li class="text-[11px] mono"><a class="text-emerald-400 hover:underline" href="${u}" target="_blank">${publisherOf(u)}</a></li>`).join('');
  det.innerHTML = `
    <div class="flex items-center gap-2">
      <span class="inline-block w-4 h-4 rounded" style="background:${colour}; opacity:.4; border:1px solid ${colour}"></span>
      <span class="text-slate-200 font-semibold">Theme ${idx+1}</span>
      <span class="text-slate-500 text-[11px] mono">weight ${t.weight.toFixed(1)}</span>
    </div>
    <div class="mt-2 text-slate-400 text-xs">${t.isCross ? `Spans <b class="text-slate-200">${t.runs.length}</b> investigative thread(s)` : 'Within a single thread'}</div>
    <div class="mt-3 text-slate-500 text-[11px] uppercase tracking-wider">Members</div>
    <ul class="mt-1 space-y-1">${t.members.map(m => `<li><a href="#" data-goto="${escapeAttr(m)}" class="text-slate-200 hover:underline">${escapeHtml(m)}</a></li>`).join('')}</ul>
    ${urls ? `<div class="mt-3 text-slate-500 text-[11px] uppercase tracking-wider">Attesting articles (${t.urls.length})</div><ul class="mt-1 space-y-0.5">${urls}</ul>` : '<div class="mt-3 text-slate-500 text-[11px] italic">No external article URLs attached.</div>'}
  `;
  det.querySelectorAll('[data-goto]').forEach(a => {
    a.onclick = ev => { ev.preventDefault(); const ele = cyTmfg.getElementById(a.dataset.goto); if (ele.length) cyTmfg.center(ele); };
  });
}

function drawPolygons() {
  if (!cyTmfg) return;
  const svg = document.getElementById('polygons');
  const cyRect = document.getElementById('cyTmfg').getBoundingClientRect();
  svg.setAttribute('width', cyRect.width); svg.setAttribute('height', cyRect.height);
  svg.setAttribute('viewBox', `0 0 ${cyRect.width} ${cyRect.height}`);
  while (svg.firstChild) svg.removeChild(svg.firstChild);
  const themesShown = currentThemes();
  themesShown.forEach((t, i) => {
    const colour = POLYGON_PALETTE[i % POLYGON_PALETTE.length];
    const pts = t.members.map(m => { const e = cyTmfg.getElementById(m); if (!e.length) return null; const p = e.renderedPosition(); return [p.x, p.y]; }).filter(p => p);
    if (pts.length < 3) return;
    const cx = pts.reduce((s,p)=>s+p[0],0)/pts.length, cy_ = pts.reduce((s,p)=>s+p[1],0)/pts.length;
    pts.sort((a,b) => Math.atan2(a[1]-cy_,a[0]-cx) - Math.atan2(b[1]-cy_,b[0]-cx));
    const ptsAttr = pts.map(p => `${p[0]},${p[1]}`).join(' ');
    const isSel = selectedThemeIdx === i;
    const poly = document.createElementNS('http://www.w3.org/2000/svg','polygon');
    poly.setAttribute('points', ptsAttr);
    poly.setAttribute('fill', colour); poly.setAttribute('fill-opacity', isSel ? 0.32 : 0.13);
    poly.setAttribute('stroke', colour); poly.setAttribute('stroke-width', isSel ? 2.5 : 1.2);
    poly.setAttribute('stroke-dasharray', isSel ? '' : '5 4'); poly.setAttribute('stroke-opacity', 0.85);
    poly.style.pointerEvents = 'auto'; poly.style.cursor = 'pointer';
    poly.addEventListener('click', () => selectTheme(i));
    svg.appendChild(poly);
    const lab = document.createElementNS('http://www.w3.org/2000/svg','text');
    lab.setAttribute('x', cx); lab.setAttribute('y', cy_); lab.setAttribute('text-anchor','middle');
    lab.setAttribute('dominant-baseline','middle'); lab.setAttribute('fill', colour);
    lab.setAttribute('font-size', isSel ? 13 : 10); lab.setAttribute('font-weight', isSel ? 700 : 500);
    lab.style.pointerEvents = 'none'; lab.textContent = `T${i+1} · w${t.weight.toFixed(1)}`;
    svg.appendChild(lab);
  });
}

window.addEventListener('resize', () => { drawPolygons(); });

// ===================== DATA tab =====================
let dataView = 'entities';
let dataSort = { col: 'evidenceCount', dir: 'desc' };
const VIEWS = {
  entities: {
    cols: [
      { key:'id', label:'Actor', sortable:true, render: r => r.id },
      { key:'type', label:'Type', sortable:true, render: r => r.type === 'event' ? 'Event' : 'Actor' },
      { key:'runs', label:'Threads', sortable:false, render: r => r.runs.map(rr => `<span class="inline-block w-2 h-2 rounded-full mr-1" title="${escapeAttr(rr)}" style="background:${threadColour[rr]||'#64748b'}"></span>`).join('') + ` <span class="text-slate-500 text-xs">${r.runs.length}/${PAYLOAD.runs.length}</span>` },
      { key:'evidenceCount', label:'Articles', sortable:true, render: r => `<span class="mono">${r.evidenceCount}</span>` },
      { key:'isBridge', label:'Bridge', sortable:true, render: r => r.isBridge ? '<span class="text-emerald-400">●</span>' : '' },
      { key:'score', label:'Score', sortable:true, render: r => `<span class="mono text-slate-400">${r.score.toFixed(2)}</span>` },
    ],
    rows: () => PAYLOAD.nodes,
  },
  events: {
    cols: [
      { key:'id', label:'Event', sortable:true, render: r => r.id },
      { key:'date', label:'Date', sortable:true, render: r => r.data?.date || '' },
      { key:'event_type', label:'Type', sortable:false, render: r => r.data?.event_type || '' },
      { key:'location', label:'Location', sortable:false, render: r => r.data?.location || '' },
      { key:'runs', label:'Threads', sortable:false, render: r => r.runs.map(rr => `<span class="inline-block w-2 h-2 rounded-full mr-1" style="background:${threadColour[rr]||'#64748b'}"></span>`).join('') },
    ],
    rows: () => PAYLOAD.nodes.filter(n => n.type === 'event'),
  },
  relationships: {
    cols: [
      { key:'source', label:'From', sortable:true, render: r => r.source },
      { key:'arrow', label:'',     sortable:false, render: () => '<span class="text-slate-500 mono">→</span>' },
      { key:'target', label:'To',  sortable:true, render: r => r.target },
      { key:'rtype', label:'Relation', sortable:true, render: r => r.rtype || r.type },
      { key:'context', label:'Context', sortable:false, render: r => `<span class="text-slate-400">${escapeHtml((r.context||'').slice(0,140))}${(r.context||'').length>140?'…':''}</span>` },
      { key:'url', label:'Source', sortable:false, render: r => r.url ? `<a class="text-emerald-400 hover:underline mono text-xs" target="_blank" href="${r.url}">${publisherOf(r.url)}</a>` : '' },
    ],
    rows: () => PAYLOAD.edges,
  },
};

document.querySelectorAll('[data-view]').forEach(b => {
  b.onclick = () => {
    document.querySelectorAll('[data-view]').forEach(x => { x.classList.replace('chip-on','chip-off'); });
    b.classList.replace('chip-off','chip-on');
    dataView = b.dataset.view;
    dataSort = { col: VIEWS[dataView].cols[0].key, dir: 'desc' };
    renderDataTable();
  };
});
document.getElementById('dataSearch').oninput = () => renderDataTable();

function renderDataTable() {
  const view = VIEWS[dataView];
  const head = document.getElementById('dataHead');
  head.innerHTML = view.cols.map(c =>
    `<th class="text-left py-2 px-3 text-xs uppercase tracking-wider text-slate-500 border-b border-slate-800 ${c.sortable ? 'sortable' : ''} ${dataSort.col === c.key ? dataSort.dir : ''}" data-col="${c.key}">${c.label}</th>`
  ).join('');
  head.querySelectorAll('[data-col]').forEach(th => {
    th.onclick = () => {
      if (!view.cols.find(c => c.key === th.dataset.col)?.sortable) return;
      if (dataSort.col === th.dataset.col) dataSort.dir = dataSort.dir === 'asc' ? 'desc' : 'asc';
      else { dataSort.col = th.dataset.col; dataSort.dir = 'desc'; }
      renderDataTable();
    };
  });
  const q = document.getElementById('dataSearch').value.toLowerCase();
  let rows = view.rows();
  if (q) {
    rows = rows.filter(r => JSON.stringify(r).toLowerCase().includes(q));
  }
  rows.sort((a, b) => {
    const va = a[dataSort.col] ?? a.data?.[dataSort.col] ?? '';
    const vb = b[dataSort.col] ?? b.data?.[dataSort.col] ?? '';
    const cmp = (typeof va === 'number' && typeof vb === 'number') ? va - vb : String(va).localeCompare(String(vb));
    return dataSort.dir === 'asc' ? cmp : -cmp;
  });
  const body = document.getElementById('dataBody');
  body.innerHTML = rows.slice(0, 500).map(r => `<tr class="table-row border-b border-slate-800/40">${view.cols.map(c => `<td class="py-1.5 px-3 align-top">${c.render(r) || ''}</td>`).join('')}</tr>`).join('');
  document.getElementById('dataStatus').textContent = `${rows.length} of ${view.rows().length} rows${rows.length > 500 ? ' (showing first 500)' : ''}`;
}
renderDataTable();

// ===================== REPORT tab =====================
let reportRendered = false;
function renderReport() {
  reportRendered = true;
  marked.setOptions({ headerIds: true, mangle: false });
  const html = marked.parse(PAYLOAD.report);
  document.getElementById('reportBody').innerHTML = html;
  // Build TOC from h2s
  const headings = document.querySelectorAll('#reportBody h2');
  const toc = document.getElementById('reportToc');
  toc.innerHTML = Array.from(headings).map((h, i) => {
    if (!h.id) h.id = 'h-' + i;
    return `<li><a href="#${h.id}" class="block text-xs text-slate-400 hover:text-emerald-400 py-1 border-l-2 border-transparent hover:border-emerald-400 pl-3">${escapeHtml(h.textContent)}</a></li>`;
  }).join('');
  // Smooth scroll inside reportBody
  toc.querySelectorAll('a').forEach(a => {
    a.onclick = ev => {
      ev.preventDefault();
      const id = a.getAttribute('href').slice(1);
      const el = document.getElementById(id);
      if (el) el.scrollIntoView({ behavior:'smooth', block:'start' });
    };
  });
}

// ===================== SOURCES tab =====================
function renderSources() {
  document.getElementById('srcPubCount').textContent  = PAYLOAD.sourcesMeta.publisherCount;
  document.getElementById('srcCiteCount').textContent = PAYLOAD.sourcesMeta.totalCitations;
  document.getElementById('srcTop3').textContent      = (PAYLOAD.sourcesMeta.top3Share * 100).toFixed(0) + '%';
  document.getElementById('srcDiversity').textContent =
    PAYLOAD.sourcesMeta.top3Share < 0.25 ? 'Diverse sourcing (top 3 < 25%)'
    : PAYLOAD.sourcesMeta.top3Share < 0.5 ? 'Moderate concentration'
    : 'High concentration (top 3 carry >50%)';
  const body = document.getElementById('srcBody');
  body.innerHTML = PAYLOAD.sources.map(s => {
    const urlList = s.items.slice(0, 5).map(u =>
      `<div class="mt-0.5"><a class="text-emerald-400 hover:underline text-xs mono" target="_blank" href="${u.url}">${publisherOf(u.url)}</a> <span class="text-slate-500 text-xs">${u.backs.slice(0,2).map(escapeHtml).join(' · ')}${u.backs.length>2 ? ' · …':''}</span></div>`
    ).join('');
    return `<tr class="table-row border-b border-slate-800/40">
      <td class="py-2 align-top"><span class="text-slate-200 font-semibold">${escapeHtml(s.publisher)}</span></td>
      <td class="py-2 align-top text-right mono text-slate-300">${s.count}</td>
      <td class="py-2 align-top pl-6">${urlList || '<span class="text-slate-500 text-xs italic">no urls</span>'}</td>
    </tr>`;
  }).join('');
}
renderSources();

// ===================== helpers =====================
function pairKey(a, b) { return a < b ? a + '||' + b : b + '||' + a; }
function publisherOf(url) { try { return new URL(url).hostname.replace(/^www\./,''); } catch { return url; } }
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function escapeAttr(s) { return escapeHtml(s).replace(/'/g,'&apos;'); }
</script>
</body>
</html>"""


def build_prototype(json_path: Path) -> Path:
    d = json.loads(json_path.read_text())
    report_md = _get_report_md(json_path, d)
    payload = _payload(d, report_md)
    html = HTML_TEMPLATE
    html = html.replace("__TITLE__", payload["title"])
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    out = json_path.with_suffix(".full_ui.html")
    out.write_text(html)
    print(f"Wrote: {out}")
    print(f"  size: {out.stat().st_size:,} bytes  "
          f"(nodes={len(payload['nodes'])}, edges={len(payload['edges'])}, "
          f"themes={len(payload['themes'])}, bridges={len(payload['bridges'])}, "
          f"publishers={payload['sourcesMeta']['publisherCount']})")
    return out


def main():
    if len(sys.argv) < 2:
        print("Usage: build_full_ui_prototype.py <artifact.json> [<artifact2.json> ...]", file=sys.stderr)
        sys.exit(1)
    for p in sys.argv[1:]:
        build_prototype(Path(p))


if __name__ == "__main__":
    main()
