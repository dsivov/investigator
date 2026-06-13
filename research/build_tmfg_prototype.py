"""Self-contained TMFG-themes tab prototype generator.

Takes a cross-event investigation JSON artifact and emits a single
self-contained HTML page that renders the TMFG-filtered subgraph with
4-clique theme polygons overlaid -- the interactive equivalent of the
matplotlib `fig_tmfg_themes` figure used in the blog.

Stack: Cytoscape (graph) + SVG overlay (polygons) + Tailwind (UI).
No build step, no server -- open in any modern browser.
"""
from __future__ import annotations

import json
import json as _json
import sys
from collections import defaultdict
from pathlib import Path

# Reuse the node-detail helpers so the TMFG payload carries the same
# evidence + data subset the Graph tab uses.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_graph_prototype import _evidence_subset, _node_data_subset  # noqa: E402


def _edge_rtype_context_url(e: dict) -> tuple[str, str, str]:
    """Pull (relation_type, context_sentence, source_url) off an edge."""
    rel = e.get("relations")
    if isinstance(rel, str):
        try:
            rel = _json.loads(rel)
        except Exception:
            rel = {}
    if not isinstance(rel, dict):
        rel = {}
    url = ""
    src = e.get("source")
    if isinstance(src, str) and src.startswith("http"):
        url = src
    elif isinstance((e.get("attributes") or {}).get("source_url"), str) and (e.get("attributes") or {})["source_url"].startswith("http"):
        url = (e.get("attributes") or {})["source_url"]
    return (rel.get("type") or "", (rel.get("context") or "").strip()[:400], url)


def _payload(d: dict) -> dict:
    final = d["final_merged_graph"]
    nodes = final["nodes"]
    edges = final["edges"]
    themes = final.get("themes", []) or []
    bridge_set = {b["identifier"] for b in final.get("bridging_entities", []) or []}
    run_ids = [ev.get("name") if isinstance(ev, dict) else str(ev)
               for ev in d.get("events", [])]

    # Dedupe: same identifier can appear as both entity and event from NER --
    # prefer the event (richer data fields).
    by_id: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        by_id[n["identifier"]].append(n)
    canonical = {}
    for ident, group in by_id.items():
        ev_first = [n for n in group if n.get("type") == "event"]
        canonical[ident] = ev_first[0] if ev_first else group[0]

    # Pick themes: prefer cross-event (spans 2+ threads), then by weight.
    cross_themes = sorted(
        themes,
        key=lambda t: (-len(t.get("runs_spanned") or []),
                       -(t.get("weight") or 0.0)),
    )

    # We render the union-graph of selected theme members. Cap to top N
    # cross-event themes by default, fall back to within-thread themes only
    # if none span multiple threads.
    THEME_CAP = 12
    chosen_themes = []
    seen_member_sigs = set()
    for t in cross_themes:
        members = tuple(sorted((t.get("members") or [])[:4]))
        if len(members) < 3:
            continue
        if members in seen_member_sigs:
            continue
        seen_member_sigs.add(members)
        chosen_themes.append(t)
        if len(chosen_themes) >= THEME_CAP:
            break

    member_set = set()
    for t in chosen_themes:
        member_set.update(t.get("members") or [])

    # Map identifier -> source URLs (from any edge that touches it)
    attestations_by_pair: dict[frozenset, list[str]] = defaultdict(list)
    attested_pairs: set[frozenset] = set()
    for e in edges:
        s = e.get("src_identifier"); t = e.get("dst_identifier")
        if not (s and t) or s == t:
            continue
        if s not in member_set or t not in member_set:
            continue
        pair = frozenset((s, t))
        attested_pairs.add(pair)
        url = ""
        src_field = e.get("source")
        if isinstance(src_field, str) and src_field.startswith("http"):
            url = src_field
        elif isinstance((e.get("attributes") or {}).get("source_url"), str) and (e.get("attributes") or {})["source_url"].startswith("http"):
            url = (e.get("attributes") or {})["source_url"]
        if url:
            attestations_by_pair[pair].append(url)

    # Build node payload
    out_nodes = []
    for ident in member_set:
        n = canonical.get(ident)
        if not n:
            # Theme references a node that isn't in the merged graph -- skip.
            continue
        runs = n.get("runs") or []
        raw_labels = n.get("labels") or []
        clean_labels = []
        for lab in raw_labels:
            if isinstance(lab, list) and lab:
                lab = lab[0]
            lab = str(lab).strip()
            if lab and lab.upper() != ident.upper() and lab not in clean_labels:
                clean_labels.append(lab)
        out_nodes.append({
            "id": ident,
            "label": ident if len(ident) <= 36 else ident[:33] + "…",
            "type": n.get("type") or "entity",
            "runs": runs,
            "isBridge": ident in bridge_set,
            "evidenceCount": int(n.get("evidence_count") or 0),
            "score": float(n.get("score") or 0.0),
            "labels": clean_labels[:6],
            "data": _node_data_subset(n),
            "evidence": _evidence_subset(n),
        })

    # Build the edge payload: attested (from the JSON) + fill-in (tetrahedron-
    # internal pairs not attested). 4-cliques generate C(4,2)=6 internal pairs.
    out_edges = []
    pair_seen = set()
    # 1. Attested edges (those we already saw between member pairs)
    for e in edges:
        s = e.get("src_identifier"); t = e.get("dst_identifier")
        if not (s and t) or s == t:
            continue
        if s not in member_set or t not in member_set:
            continue
        if e.get("type") not in ("affiliation", "event_participation",
                                 "event_followed_by", "event_coincident",
                                 "claimed_caused_by"):
            continue
        pair = (s, t) if s < t else (t, s)
        if pair in pair_seen:
            continue
        pair_seen.add(pair)
        rtype, context, url = _edge_rtype_context_url(e)
        out_edges.append({
            "source": s, "target": t,
            "kind": "attested",
            "type": e.get("type"),
            "rtype": rtype,
            "context": context,
            "url": url,
        })
    # 2. Fill-in edges: tetrahedron-internal pairs not yet in pair_seen
    for t in chosen_themes:
        members = list(t.get("members") or [])[:4]
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                if a not in member_set or b not in member_set:
                    continue
                pair = (a, b) if a < b else (b, a)
                if pair in pair_seen:
                    continue
                pair_seen.add(pair)
                out_edges.append({
                    "source": a, "target": b,
                    "kind": "fillin",
                    "type": "tmfg_hypothesis",
                })

    # Themes for the side panel: collect attesting URLs per theme by walking
    # all pairs of its 4 members.
    out_themes = []
    for i, t in enumerate(chosen_themes):
        members = list(t.get("members") or [])[:4]
        urls: list[str] = []
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                key = frozenset((members[a], members[b]))
                urls.extend(attestations_by_pair.get(key, []))
        # dedupe preserving order
        seen = set(); deduped_urls = []
        for u in urls:
            if u and u not in seen:
                deduped_urls.append(u); seen.add(u)
        out_themes.append({
            "idx": i,
            "members": members,
            "weight": float(t.get("weight") or 0.0),
            "runs": t.get("runs_spanned") or [],
            "isCross": bool(t.get("is_cross_investigation")),
            "urls": deduped_urls[:12],
        })

    return {
        "title": _title_from_runs(run_ids),
        "runs": run_ids,
        "domain": (d.get("params") or {}).get("domain") or "general",
        "period": (d.get("params") or {}).get("period") or "30d",
        "nodes": out_nodes,
        "edges": out_edges,
        "themes": out_themes,
        "bridges": sorted({n["id"] for n in out_nodes if n["isBridge"]}),
    }


def _title_from_runs(run_ids: list[str]) -> str:
    parts = [r.replace("_", " ").title() for r in run_ids]
    if len(parts) <= 1:
        return parts[0] if parts else "Investigation"
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!doctype html>
<html lang="en" class="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__ — TMFG themes — OSINTGraph</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://unpkg.com/cytoscape@3.30.2/dist/cytoscape.min.js"></script>
<script src="https://unpkg.com/layout-base@2.0.1/layout-base.js"></script>
<script src="https://unpkg.com/cose-base@2.2.0/cose-base.js"></script>
<script src="https://unpkg.com/cytoscape-fcose@2.2.0/cytoscape-fcose.js"></script>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  html, body { background: #0b1220; color: #e2e8f0; font-family: 'IBM Plex Sans', system-ui, sans-serif; }
  .mono { font-family: 'IBM Plex Mono', ui-monospace, monospace; }
  #cy { background: #0b1220; }
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
</style>
</head>
<body class="h-screen flex flex-col overflow-hidden">

<header class="flex items-center justify-between border-b border-slate-800 bg-slate-900 px-5 py-3">
  <div class="flex items-center gap-3">
    <div class="text-emerald-400 font-bold tracking-tight text-lg">OSINTGraph</div>
    <div class="text-slate-500">›</div>
    <div class="text-sm text-slate-300 truncate max-w-2xl" id="invTitle">—</div>
  </div>
  <div class="flex items-center gap-4 text-xs text-slate-400">
    <span id="invMeta">—</span>
  </div>
</header>

<nav class="flex items-center gap-1 border-b border-slate-800 bg-slate-900 px-5">
  <button class="px-3 py-2 text-sm text-slate-500">Overview</button>
  <button class="px-3 py-2 text-sm text-slate-500">Graph</button>
  <button class="px-3 py-2 text-sm text-emerald-300 border-b-2 border-emerald-400 font-medium">TMFG themes</button>
  <button class="px-3 py-2 text-sm text-slate-500">Data</button>
  <button class="px-3 py-2 text-sm text-slate-500">Report</button>
  <button class="px-3 py-2 text-sm text-slate-500">Sources</button>
</nav>

<div class="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/60 px-5 py-2 text-xs">
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">Show top</span>
    <select id="topN" class="bg-slate-800 border border-slate-700 rounded px-2 py-1">
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
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">Cross-thread only</span>
    <button id="crossOnly" class="chip chip-on rounded-md border px-2 py-1">Yes</button>
  </div>
  <span class="text-slate-700">·</span>
  <button id="fitBtn" class="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300">Fit</button>
  <button id="reLayoutBtn" class="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300">Re-layout</button>
  <div class="ml-auto text-slate-400 mono" id="statusBar">—</div>
</div>

<main class="flex-1 flex min-h-0">
  <div class="stage flex-1">
    <div id="cy" class="absolute inset-0"></div>
    <svg id="polygons" xmlns="http://www.w3.org/2000/svg"></svg>
  </div>
  <aside class="w-[440px] flex-shrink-0 border-l border-slate-800 bg-slate-900 overflow-y-auto scrollbar p-5 text-sm">
    <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Themes</div>
    <div id="themeList" class="space-y-2"></div>
    <hr class="border-slate-800 my-4"/>
    <div id="themeDetail" class="text-slate-400 italic text-xs">
      Click a theme to see its members and attesting source articles. Click a polygon in the canvas for the same view.
    </div>
  </aside>
</main>

<footer class="border-t border-slate-800 bg-slate-900 px-5 py-2 text-xs text-slate-500 mono">
  <span id="footerStats">—</span>
</footer>

<script>
const PAYLOAD = __PAYLOAD__;
</script>

<script>
const THREAD_PALETTE = ['#3b82f6','#ef4444','#f59e0b','#a855f7','#10b981','#06b6d4','#ec4899'];
const POLYGON_PALETTE = ['#10b981','#ef4444','#3b82f6','#f59e0b','#a855f7','#06b6d4','#ec4899','#f43f5e','#84cc16','#0ea5e9','#fb7185','#22c55e'];

const threadColour = {};
PAYLOAD.runs.forEach((r, i) => threadColour[r] = THREAD_PALETTE[i % THREAD_PALETTE.length]);

document.getElementById('invTitle').textContent = PAYLOAD.title;
document.getElementById('invMeta').textContent =
  `${PAYLOAD.runs.length} threads · ${PAYLOAD.domain.replace(/_/g,' ')} · ${PAYLOAD.period}`;

const edgeKinds = new Set(['attested','fillin']);
document.querySelectorAll('[data-edgekind]').forEach(btn => {
  btn.onclick = () => {
    const k = btn.dataset.edgekind;
    if (edgeKinds.has(k)) { edgeKinds.delete(k); btn.classList.remove('chip-on'); btn.classList.add('chip-off'); }
    else { edgeKinds.add(k); btn.classList.add('chip-on'); btn.classList.remove('chip-off'); }
    applyEdgeFilter();
  };
});

let crossOnly = true;
document.getElementById('crossOnly').onclick = (e) => {
  crossOnly = !crossOnly;
  e.target.classList.toggle('chip-on', crossOnly);
  e.target.classList.toggle('chip-off', !crossOnly);
  e.target.textContent = crossOnly ? 'Yes' : 'No';
  rebuildScene();
};
document.getElementById('topN').onchange = rebuildScene;
document.getElementById('fitBtn').onclick = () => cy.fit(undefined, 50);
document.getElementById('reLayoutBtn').onclick = () => runLayout();

let cy = null;
let selectedThemeIdx = null;

function rebuildScene() {
  const topN = +document.getElementById('topN').value;
  const themesAll = crossOnly
    ? PAYLOAD.themes.filter(t => (t.runs || []).length >= 2)
    : PAYLOAD.themes;
  const themesShown = topN > 0 ? themesAll.slice(0, topN) : themesAll;

  const memberSet = new Set();
  themesShown.forEach(t => t.members.forEach(m => memberSet.add(m)));

  // Filter nodes to themes' member union
  const nodes = PAYLOAD.nodes.filter(n => memberSet.has(n.id));
  // Filter edges to those touching members
  const edges = PAYLOAD.edges.filter(e => memberSet.has(e.source) && memberSet.has(e.target));

  if (cy) { cy.destroy(); }
  cy = cytoscape({
    container: document.getElementById('cy'),
    wheelSensitivity: 0.2,
    elements: [
      ...nodes.map(n => ({
        data: { ...n, faceColour: n.isBridge ? '#10b981' : (threadColour[n.runs[0]] || '#64748b') },
        classes: (n.type === 'event' ? 'is-event' : 'is-actor') + (n.isBridge ? ' is-bridge' : '')
      })),
      ...edges.map((e, i) => ({
        data: { ...e, id: 'e' + i, edgeColour: e.kind === 'fillin' ? '#fb923c' : '#52525b' }
      })),
    ],
    style: [
      { selector: 'node', style: {
          'background-color': 'data(faceColour)',
          'label': 'data(label)', 'color': '#e2e8f0',
          'font-size': 10, 'text-margin-y': -10,
          'font-family': 'IBM Plex Sans, system-ui, sans-serif',
          'text-outline-color': '#0b1220', 'text-outline-width': 2,
          'border-width': 1.5, 'border-color': '#0f172a',
          'width': 26, 'height': 26,
      }},
      { selector: 'node.is-event', style: { 'shape': 'diamond', 'width': 22, 'height': 22 }},
      { selector: 'node.is-bridge', style: {
          'border-color': '#10b981', 'border-width': 3.5,
          'width': 40, 'height': 40, 'font-size': 12, 'font-weight': 700,
      }},
      { selector: 'edge', style: {
          'curve-style': 'bezier',
          'line-color': 'data(edgeColour)',
          'target-arrow-color': 'data(edgeColour)',
          'width': 1.2,
          'opacity': 0.7,
      }},
      { selector: 'edge[kind = "fillin"]', style: { 'line-style': 'dashed', 'target-arrow-shape': 'none' }},
      { selector: 'edge[kind = "attested"]', style: { 'target-arrow-shape': 'triangle', 'arrow-scale': 0.9 }},
      { selector: '.dim', style: { 'opacity': 0.12 }},
    ],
  });

  cy.on('pan zoom resize', drawPolygons);
  cy.on('layoutstop', drawPolygons);
  cy.on('tap', 'node', evt => highlightNodeThemes(evt.target.id()));
  cy.on('tap', evt => { if (evt.target === cy) selectTheme(null); });

  applyEdgeFilter();
  runLayout();
  renderThemeList(themesShown);
  selectTheme(null);
  document.getElementById('statusBar').textContent =
    `${themesShown.length} themes · ${nodes.length} actors · ${edges.length} edges`;
  document.getElementById('footerStats').textContent =
    `themes shown ${themesShown.length} of ${PAYLOAD.themes.length} · members ${nodes.length} · bridges ${PAYLOAD.bridges.length}`;
}

function runLayout() {
  if (!cy) return;
  cy.layout({
    name: 'fcose', animate: true, randomize: false,
    nodeRepulsion: 6500, idealEdgeLength: 90, gravity: 0.3
  }).run();
}

function applyEdgeFilter() {
  if (!cy) return;
  cy.edges().forEach(e => {
    const kind = e.data('kind');
    e.style('display', edgeKinds.has(kind) ? 'element' : 'none');
  });
}

const currentThemes = () => {
  const topN = +document.getElementById('topN').value;
  const themesAll = crossOnly
    ? PAYLOAD.themes.filter(t => (t.runs || []).length >= 2)
    : PAYLOAD.themes;
  return topN > 0 ? themesAll.slice(0, topN) : themesAll;
};

function renderThemeList(themesShown) {
  const wrap = document.getElementById('themeList');
  if (!themesShown.length) {
    wrap.innerHTML = '<div class="text-slate-500 italic text-xs">No themes match the current filters.</div>';
    return;
  }
  wrap.innerHTML = themesShown.map((t, i) => {
    const colour = POLYGON_PALETTE[i % POLYGON_PALETTE.length];
    const runs = t.runs.map(r =>
      `<span class="inline-block w-2 h-2 rounded-full" style="background:${threadColour[r] || '#64748b'}"></span>`
    ).join(' ');
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
  wrap.querySelectorAll('.theme-row').forEach(r => {
    r.onclick = () => selectTheme(+r.dataset.theme);
  });
}

function selectTheme(idx) {
  selectedThemeIdx = idx;
  document.querySelectorAll('.theme-row').forEach(r => {
    r.classList.toggle('selected', +r.dataset.theme === idx);
  });
  drawPolygons();
  const det = document.getElementById('themeDetail');
  if (idx == null) {
    det.innerHTML = `<div class="text-slate-400 italic text-xs">Click a theme to see its members and attesting source articles. Click a polygon in the canvas for the same view.</div>`;
    if (cy) cy.nodes().removeClass('dim');
    return;
  }
  const t = currentThemes()[idx];
  if (!t) return;
  if (cy) {
    cy.nodes().addClass('dim');
    t.members.forEach(m => cy.getElementById(m).removeClass('dim'));
  }
  const colour = POLYGON_PALETTE[idx % POLYGON_PALETTE.length];
  const urls = t.urls.map(u => `<li class="text-[11px] mono"><a class="text-emerald-400 hover:underline" href="${u}" target="_blank">${publisherOf(u)}</a></li>`).join('');
  det.innerHTML = `
    <div class="flex items-center gap-2">
      <span class="inline-block w-4 h-4 rounded" style="background:${colour}; opacity:.4; border:1px solid ${colour}"></span>
      <span class="text-slate-200 font-semibold">Theme ${idx+1}</span>
      <span class="text-slate-500 text-[11px] mono">weight ${t.weight.toFixed(1)}</span>
    </div>
    <div class="mt-2 text-slate-400 text-xs">
      ${t.isCross ? `Spans <b class="text-slate-200">${t.runs.length}</b> investigative thread(s)` : 'Within a single thread'}
    </div>
    <div class="mt-3 text-slate-500 text-[11px] uppercase tracking-wider">Members</div>
    <ul class="mt-1 space-y-1">
      ${t.members.map(m => `<li><a href="#" data-goto="${escapeAttr(m)}" class="text-slate-200 hover:underline">${escapeHtml(m)}</a></li>`).join('')}
    </ul>
    ${urls ? `<div class="mt-3 text-slate-500 text-[11px] uppercase tracking-wider">Attesting articles (${t.urls.length})</div><ul class="mt-1 space-y-0.5">${urls}</ul>` : '<div class="mt-3 text-slate-500 text-[11px] italic">No external article URLs were attached to the attesting edges.</div>'}
  `;
  det.querySelectorAll('[data-goto]').forEach(a => {
    a.onclick = ev => {
      ev.preventDefault();
      const ele = cy.getElementById(a.dataset.goto);
      if (ele.length) { cy.center(ele); }
    };
  });
}

function highlightNodeThemes(id) {
  // Find which themes contain this node; if exactly one, select it.
  const themes = currentThemes();
  const hits = themes
    .map((t, i) => ({ t, i }))
    .filter(p => p.t.members.includes(id));
  if (hits.length === 1) selectTheme(hits[0].i);
  // Otherwise let the user pick from the side list visually
}

function drawPolygons() {
  if (!cy) return;
  const svg = document.getElementById('polygons');
  // Match SVG viewport to the Cytoscape canvas
  const cyRect = document.getElementById('cy').getBoundingClientRect();
  svg.setAttribute('width', cyRect.width);
  svg.setAttribute('height', cyRect.height);
  svg.setAttribute('viewBox', `0 0 ${cyRect.width} ${cyRect.height}`);
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  const themesShown = currentThemes();
  themesShown.forEach((t, i) => {
    const colour = POLYGON_PALETTE[i % POLYGON_PALETTE.length];
    const pts = t.members.map(m => {
      const ele = cy.getElementById(m);
      if (!ele.length) return null;
      const p = ele.renderedPosition();
      return [p.x, p.y];
    }).filter(p => p);
    if (pts.length < 3) return;
    // Sort vertices clockwise around centroid for a clean polygon
    const cx = pts.reduce((s,p) => s+p[0],0)/pts.length;
    const cy_ = pts.reduce((s,p) => s+p[1],0)/pts.length;
    pts.sort((a,b) => Math.atan2(a[1]-cy_,a[0]-cx) - Math.atan2(b[1]-cy_,b[0]-cx));
    const ptsAttr = pts.map(p => `${p[0]},${p[1]}`).join(' ');
    const isSel = selectedThemeIdx === i;
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
    poly.setAttribute('points', ptsAttr);
    poly.setAttribute('fill', colour);
    poly.setAttribute('fill-opacity', isSel ? 0.32 : 0.13);
    poly.setAttribute('stroke', colour);
    poly.setAttribute('stroke-width', isSel ? 2.5 : 1.2);
    poly.setAttribute('stroke-dasharray', isSel ? '' : '5 4');
    poly.setAttribute('stroke-opacity', 0.85);
    poly.style.pointerEvents = 'auto';
    poly.style.cursor = 'pointer';
    poly.addEventListener('click', () => selectTheme(i));
    svg.appendChild(poly);
    // Label at centroid
    const lab = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    lab.setAttribute('x', cx); lab.setAttribute('y', cy_);
    lab.setAttribute('text-anchor', 'middle');
    lab.setAttribute('dominant-baseline', 'middle');
    lab.setAttribute('fill', colour);
    lab.setAttribute('font-size', isSel ? 13 : 10);
    lab.setAttribute('font-weight', isSel ? 700 : 500);
    lab.style.pointerEvents = 'none';
    lab.textContent = `T${i+1} · w${t.weight.toFixed(1)}`;
    svg.appendChild(lab);
  });
}

window.addEventListener('resize', drawPolygons);

function publisherOf(url) {
  try { return new URL(url).hostname.replace(/^www\\./,''); } catch { return url; }
}
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function escapeAttr(s) { return escapeHtml(s).replace(/'/g,'&apos;'); }

rebuildScene();
</script>
</body>
</html>"""


def build_prototype(json_path: Path) -> Path:
    d = json.loads(json_path.read_text())
    payload = _payload(d)
    html = HTML_TEMPLATE
    html = html.replace("__TITLE__", payload["title"])
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    out = json_path.with_suffix(".tmfg_prototype.html")
    out.write_text(html)
    print(f"Wrote: {out}")
    print(f"  size: {out.stat().st_size:,} bytes  "
          f"(themes={len(payload['themes'])}, members={len(payload['nodes'])}, "
          f"edges={len(payload['edges'])})")
    return out


def main():
    if len(sys.argv) < 2:
        print("Usage: build_tmfg_prototype.py <artifact.json> [<artifact2.json> ...]", file=sys.stderr)
        sys.exit(1)
    for p in sys.argv[1:]:
        build_prototype(Path(p))


if __name__ == "__main__":
    main()
