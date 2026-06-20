"""Self-contained Graph-tab prototype generator.

Takes a cross-event investigation JSON artifact and emits a single
self-contained HTML file: Cytoscape canvas, filter chips, entity-
detail side panel, layout selector. Tailwind dark theme.

No build step. No server needed. Open the file in a browser.

Usage:
    PYTHONPATH=.:src \\
      /home/dsivov/.conda/envs/tangos/bin/python \\
      research/build_graph_prototype.py <artifact.json>
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path


def _corroboration(n: dict) -> tuple[str, int]:
    """Fact-checking strength for an entity: (tier, distinct_source_count).

    Prefers the backend-computed fields (consolidator: winning-side distinct
    sources). Falls back to counting distinct evidence sources for artifacts
    produced before corroboration was added. 1->weak, 2->moderate, 3+->strong.
    """
    tier = n.get("corroboration")
    n_src = n.get("corroboration_sources")
    if isinstance(n_src, int) and isinstance(tier, str):
        return tier, n_src
    srcs = set()
    for ev in (n.get("evidence") or []):
        key = (ev.get("doc_id") or (ev.get("metadata") or {}).get("source") or "").strip().lower()
        if key:
            srcs.add(key)
    n_src = len(srcs)
    tier = "strong" if n_src >= 3 else "moderate" if n_src == 2 else "weak"
    return tier, n_src


def _payload(d: dict) -> dict:
    """Distil the JSON artifact into the compact shape the browser needs."""
    final = d["final_merged_graph"]
    nodes = final["nodes"]
    edges = final["edges"]
    bridge_set = {b["identifier"] for b in final.get("bridging_entities", []) or []}
    bridge_meta = {b["identifier"]: b for b in final.get("bridging_entities", []) or []}
    run_ids = [ev.get("name") if isinstance(ev, dict) else str(ev)
               for ev in d.get("events", [])]

    # Dedupe collisions where the same identifier appears as BOTH an entity
    # and an event (NER occasionally double-classifies a headline). Cytoscape
    # requires unique node ids; we keep the richer record (events carry
    # date/description/participants) and drop the bare entity version.
    by_id: dict[str, list[dict]] = defaultdict(list)
    for n in nodes:
        by_id[n["identifier"]].append(n)
    canonical_nodes = []
    for ident, group in by_id.items():
        if len(group) == 1:
            canonical_nodes.append(group[0])
        else:
            events = [n for n in group if n.get("type") == "event"]
            canonical_nodes.append(events[0] if events else group[0])

    out_nodes = []
    nodes_by_id = {}
    for n in canonical_nodes:
        ident = n["identifier"]
        nodes_by_id[ident] = n
        runs = n.get("runs") or []
        # Pick a sentence-level role from the node's outgoing edges later;
        # here we just carry the identity + metadata.
        labels = n.get("labels") or []
        # Strip [label, count] artefacts in some labels fields
        clean_labels = []
        for lab in labels:
            if isinstance(lab, list) and lab:
                lab = lab[0]
            lab = str(lab).strip()
            if lab and lab.upper() != ident.upper() and lab not in clean_labels:
                clean_labels.append(lab)
        _corr_tier, _corr_n = _corroboration(n)
        out_nodes.append({
            "id": ident,
            "label": ident if len(ident) <= 38 else ident[:35] + "…",
            "type": n.get("type") or "entity",
            "runs": runs,
            "isBridge": ident in bridge_set,
            "labels": clean_labels[:6],
            "evidenceCount": int(n.get("evidence_count") or 0),
            "corroboration": _corr_tier,
            "corroborationSources": _corr_n,
            "posterior": float(n.get("posterior_prob") or 0.0),
            "score": float(n.get("score") or 0.0),
            "data": _node_data_subset(n),
            "evidence": _evidence_subset(n),
        })

    # Edges: keep the semantic relationship types AND the `evidence`
    # (triangulation backbone) edges. The evidence edges wire each node to a
    # relevance hub; they read as noise on their own but they are exactly
    # what keeps the merged graph connected. Dropping them fragmented the
    # rendered graph into dozens of components even though the underlying
    # graph is connected. We keep them tagged `structural: true` so the
    # frontend can render them faintly and offer a hide toggle.
    KEEP_TYPES = ("affiliation", "event_participation", "event_followed_by",
                  "event_coincident", "claimed_caused_by", "evidence")
    STRUCTURAL_TYPES = ("evidence",)
    out_edges = []
    seen_pairs = set()
    for e in edges:
        s = e.get("src_identifier"); t = e.get("dst_identifier")
        if not (s and t) or s == t:
            continue
        etype = e.get("type") or "affiliation"
        if etype not in KEEP_TYPES:
            continue
        rel = e.get("relations")
        if isinstance(rel, str):
            try: rel = json.loads(rel)
            except Exception: rel = {}
        if not isinstance(rel, dict):
            rel = {}
        attrs = e.get("attributes") or {}
        url = ""
        src_field = e.get("source")
        if isinstance(src_field, str) and src_field.startswith("http"):
            url = src_field
        elif isinstance(attrs.get("source_url"), str) and attrs["source_url"].startswith("http"):
            url = attrs["source_url"]
        pair_key = (s, t, etype)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        out_edges.append({
            "id": f"{len(out_edges)}",
            "source": s,
            "target": t,
            "type": etype,
            "structural": etype in STRUCTURAL_TYPES,
            "rtype": rel.get("type") or "",
            "context": (rel.get("context") or "").strip()[:400],
            "url": url,
            "publisher": e.get("source") if isinstance(e.get("source"), str) and not (e.get("source") or "").startswith("http") else "",
        })

    return {
        "title": _title_from_runs(run_ids),
        "runs": run_ids,
        "domain": (d.get("params") or {}).get("domain") or "general",
        "period": (d.get("params") or {}).get("period") or "30d",
        "bridges": [
            {
                "id": b["identifier"],
                "runs": b.get("runs") or [],
                "posterior": float(b.get("posterior_prob") or 0.0),
                "score": float(b.get("score") or 0.0),
            }
            for b in (final.get("bridging_entities") or [])
        ],
        "nodes": out_nodes,
        "edges": out_edges,
    }


def _evidence_subset(node: dict) -> list[dict]:
    """Compact form of each evidence record for the UI's Evidence view.
    Carries the source-grounded reasoning, the actual quotes, the
    source URL/publisher, and the strength/confidence/polarity."""
    out = []
    for ev in (node.get("evidence") or []):
        if not isinstance(ev, dict):
            continue
        meta = ev.get("metadata") or {}
        src = meta.get("source") or ev.get("doc_id") or ""
        quotes = ev.get("evidence") or []
        if isinstance(quotes, str):
            quotes = [quotes]
        out.append({
            "reasoning": str(ev.get("reasoning") or "").strip()[:600],
            "quotes": [str(q).strip() for q in quotes if str(q).strip()][:4],
            "source": src if isinstance(src, str) else str(src),
            "strength": float(ev.get("strength") or 0.0),
            "confidence": float(ev.get("confidence") or 0.0),
            "supports": bool(ev.get("hypothesis")),
        })
    return out


def _node_data_subset(node: dict) -> dict:
    """Extract just the fields the UI displays for an entity, to keep
    the inline payload compact."""
    data = node.get("data") or {}
    keep = {}
    for k in ("position", "location", "address", "type",
              "date", "event_type", "description", "participants"):
        v = data.get(k)
        if v is None or v == "Not found" or v == "Unknown" or v == "":
            continue
        keep[k] = v
    return keep


def _title_from_runs(run_ids: list[str]) -> str:
    parts = [r.replace("_", " ").title() for r in run_ids]
    if len(parts) <= 1:
        return parts[0] if parts else "Investigation"
    return " · ".join(parts)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!doctype html>
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
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  html, body { background: #0b1220; color: #e2e8f0; font-family: 'IBM Plex Sans', system-ui, sans-serif; }
  .mono { font-family: 'IBM Plex Mono', ui-monospace, monospace; }
  #cy { background: #0b1220; }
  .chip { user-select: none; }
  .chip-on  { background: #1e3a8a; color: #dbeafe; border-color: #2563eb; }
  .chip-off { background: #1e293b; color: #64748b; border-color: #334155; }
  .scrollbar::-webkit-scrollbar { width: 8px; height: 8px; }
  .scrollbar::-webkit-scrollbar-track { background: #0f172a; }
  .scrollbar::-webkit-scrollbar-thumb { background: #334155; border-radius: 4px; }
  .scrollbar::-webkit-scrollbar-thumb:hover { background: #475569; }
</style>
</head>
<body class="h-screen flex flex-col overflow-hidden">

<!-- Top bar -->
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

<!-- Tab strip (visual only -- this prototype shows only the Graph tab) -->
<nav class="flex items-center gap-1 border-b border-slate-800 bg-slate-900 px-5">
  <button class="px-3 py-2 text-sm text-slate-500">Overview</button>
  <button class="px-3 py-2 text-sm text-emerald-300 border-b-2 border-emerald-400 font-medium">Graph</button>
  <button class="px-3 py-2 text-sm text-slate-500">TMFG themes</button>
  <button class="px-3 py-2 text-sm text-slate-500">Data</button>
  <button class="px-3 py-2 text-sm text-slate-500">Report</button>
  <button class="px-3 py-2 text-sm text-slate-500">Sources</button>
</nav>

<!-- Filter row -->
<div class="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/60 px-5 py-2 text-xs">
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">Threads</span>
    <div id="threadChips" class="flex gap-1"></div>
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
  <span class="text-slate-700">·</span>
  <button id="fitBtn" class="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300">Fit</button>
  <div class="ml-auto text-slate-400 mono" id="filterStatus">—</div>
</div>

<!-- Workspace: canvas (left) + side panel (right) -->
<main class="flex-1 flex min-h-0">
  <div id="cy" class="flex-1"></div>
  <aside id="side" class="w-[420px] flex-shrink-0 border-l border-slate-800 bg-slate-900 overflow-y-auto scrollbar p-5 text-sm">
    <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Selection</div>
    <div id="selectionEmpty" class="text-slate-500 italic">
      Click an entity in the graph to see its attested role, sources, and relationships.
    </div>
    <div id="selectionPanel" class="hidden"></div>
  </aside>
</main>

<footer class="border-t border-slate-800 bg-slate-900 px-5 py-2 text-xs text-slate-500 mono">
  <span id="footerStats">—</span>
</footer>

<!-- Data -->
<script>
const PAYLOAD = __PAYLOAD__;
</script>

<!-- Render -->
<script>
const THREAD_PALETTE = ['#3b82f6','#ef4444','#f59e0b','#a855f7','#10b981','#06b6d4','#ec4899'];
const ETYPE_LABEL = {
  affiliation: 'affiliation', event_participation: 'participates in',
  event_followed_by: 'followed by', event_coincident: 'coincident with',
  claimed_caused_by: 'caused (per source)'
};
const ETYPE_COLOR = {
  affiliation: '#475569', event_participation: '#b45309',
  event_followed_by: '#7e22ce', event_coincident: '#7e22ce',
  claimed_caused_by: '#dc2626'
};

const threadColour = {};
PAYLOAD.runs.forEach((r, i) => threadColour[r] = THREAD_PALETTE[i % THREAD_PALETTE.length]);

function nodeColour(n) {
  if (n.isBridge) return '#10b981';
  if (n.runs.length === 1) return threadColour[n.runs[0]] || '#64748b';
  return '#64748b';
}

document.getElementById('invTitle').textContent = PAYLOAD.title;
document.getElementById('invMeta').textContent =
  `${PAYLOAD.runs.length} threads · ${PAYLOAD.domain.replace(/_/g,' ')} · ${PAYLOAD.period}`;

// Thread chips
const tcEl = document.getElementById('threadChips');
const threadsOn = new Set(PAYLOAD.runs);
PAYLOAD.runs.forEach(r => {
  const b = document.createElement('button');
  b.dataset.thread = r;
  b.className = 'chip chip-on rounded-md border px-2 py-1';
  b.innerHTML = `<span class="inline-block w-2 h-2 rounded-full mr-1 align-middle" style="background:${threadColour[r]}"></span>${r}`;
  b.onclick = () => {
    if (threadsOn.has(r)) { threadsOn.delete(r); b.classList.remove('chip-on'); b.classList.add('chip-off'); }
    else { threadsOn.add(r); b.classList.add('chip-on'); b.classList.remove('chip-off'); }
    applyFilters();
  };
  tcEl.appendChild(b);
});

const typesOn = new Set(['entity','event']);
document.querySelectorAll('[data-type]').forEach(b => {
  b.onclick = () => {
    const t = b.dataset.type;
    if (typesOn.has(t)) { typesOn.delete(t); b.classList.remove('chip-on'); b.classList.add('chip-off'); }
    else { typesOn.add(t); b.classList.add('chip-on'); b.classList.remove('chip-off'); }
    applyFilters();
  };
});

document.getElementById('minEv').oninput = e => {
  document.getElementById('minEvVal').textContent = e.target.value;
  applyFilters();
};
document.getElementById('layout').onchange = e => runLayout(e.target.value);
document.getElementById('fitBtn').onclick = () => cy.fit(undefined, 40);

const cy = cytoscape({
  container: document.getElementById('cy'),
  wheelSensitivity: 0.2,
  elements: [
    ...PAYLOAD.nodes.map(n => ({
      data: { ...n, faceColour: nodeColour(n) },
      classes: (n.type === 'event' ? 'is-event' : 'is-actor') + (n.isBridge ? ' is-bridge' : '')
    })),
    ...PAYLOAD.edges.map(e => ({
      data: { ...e, edgeColour: ETYPE_COLOR[e.type] || '#475569' }
    })),
  ],
  style: [
    { selector: 'node', style: {
        'background-color': 'data(faceColour)',
        'label': 'data(label)',
        'color': '#e2e8f0',
        'text-margin-y': -10,
        'font-size': 10,
        'font-family': 'IBM Plex Sans, system-ui, sans-serif',
        'text-outline-color': '#0b1220', 'text-outline-width': 2,
        'border-width': 1.5, 'border-color': '#0f172a',
        'width': 26, 'height': 26,
    }},
    { selector: 'node.is-event', style: { 'shape': 'diamond', 'width': 22, 'height': 22 }},
    { selector: 'node.is-bridge', style: {
        'border-color': '#10b981', 'border-width': 3.5,
        'width': 38, 'height': 38, 'font-size': 12, 'font-weight': 700,
    }},
    { selector: 'node:selected', style: {
        'border-color': '#fde68a', 'border-width': 4, 'overlay-opacity': 0,
    }},
    { selector: 'node.dim', style: { 'opacity': 0.18 }},
    { selector: 'edge', style: {
        'curve-style': 'bezier',
        'line-color': 'data(edgeColour)',
        'target-arrow-color': 'data(edgeColour)',
        'target-arrow-shape': 'triangle',
        'width': 1.2,
        'arrow-scale': 0.9,
        'opacity': 0.75,
    }},
    { selector: 'edge[type = "event_followed_by"], edge[type = "event_coincident"]', style: { 'line-style': 'dashed' }},
    { selector: 'edge[type = "claimed_caused_by"]', style: { 'width': 2.5 }},
    { selector: 'edge.dim', style: { 'opacity': 0.1 }},
    { selector: 'edge.hi', style: { 'opacity': 1.0, 'width': 2.2 }},
  ],
});

cy.on('tap', 'node', evt => showEntity(evt.target.id()));
cy.on('tap', (evt) => {
  if (evt.target === cy) clearHighlight();
});

function showEntity(id) {
  const n = PAYLOAD.nodes.find(n => n.id === id);
  if (!n) return;
  highlightNeighbourhood(id);

  const incoming = PAYLOAD.edges.filter(e => e.target === id);
  const outgoing = PAYLOAD.edges.filter(e => e.source === id);

  const sideEmpty = document.getElementById('selectionEmpty');
  const side = document.getElementById('selectionPanel');
  sideEmpty.classList.add('hidden');
  side.classList.remove('hidden');

  const bridgePill = n.isBridge
    ? `<span class="inline-block bg-emerald-900/40 text-emerald-300 text-xs rounded px-2 py-0.5 ml-2 align-middle">Bridge · ${n.runs.length} threads</span>`
    : '';
  const typePill = `<span class="inline-block bg-slate-700/60 text-slate-300 text-xs rounded px-2 py-0.5 ml-2 align-middle">${n.type === 'event' ? 'Event' : 'Actor'}</span>`;
  const threadDots = n.runs.map(r => `<span class="inline-block w-2 h-2 rounded-full mr-1" title="${r}" style="background:${threadColour[r] || '#64748b'}"></span>`).join('');

  const labelsHtml = n.labels.length
    ? `<div class="mt-2 text-xs text-slate-400"><span class="text-slate-500">Also known as:</span> ${n.labels.map(l => `<span class="mono text-slate-300">${escapeHtml(l)}</span>`).join(' · ')}</div>`
    : '';

  const dataRows = [];
  if (n.data.position)   dataRows.push(['Role', n.data.position]);
  if (n.data.location)   dataRows.push(['Location', n.data.location]);
  if (n.data.event_type) dataRows.push(['Event type', n.data.event_type]);
  if (n.data.date)       dataRows.push(['Date', n.data.date]);
  if (n.data.description) dataRows.push(['Description', n.data.description.slice(0,260) + (n.data.description.length > 260 ? '…' : '')]);
  const dataHtml = dataRows.length
    ? `<div class="mt-3 grid grid-cols-[110px_1fr] gap-y-1 text-xs">${
        dataRows.map(([k,v]) => `<div class="text-slate-500">${escapeHtml(k)}</div><div class="text-slate-200">${escapeHtml(String(v))}</div>`).join('')
      }</div>`
    : '';

  function renderEdgeRow(e, direction) {
    const other = direction === 'out' ? e.target : e.source;
    const arrow = direction === 'out' ? '→' : '←';
    const ctx = e.context ? `<div class="text-xs text-slate-400 mt-0.5 leading-snug">"${escapeHtml(e.context)}"</div>` : '';
    const src = e.url
      ? `<a class="text-emerald-400 hover:underline text-xs mono" target="_blank" href="${e.url}">${publisherOf(e.url)}</a>`
      : (e.publisher ? `<span class="text-slate-500 text-xs">${escapeHtml(e.publisher)}</span>` : '');
    const rt = e.rtype ? `<span class="text-xs text-slate-500 italic">${escapeHtml(e.rtype)}</span>` : '';
    return `<li class="border-l-2 pl-3 py-1" style="border-color:${ETYPE_COLOR[e.type] || '#475569'}">
      <div class="text-slate-200"><span class="text-slate-500 mono">${arrow}</span> <a href="#" class="hover:underline" data-goto="${escapeAttr(other)}">${escapeHtml(other)}</a> ${rt}</div>
      ${ctx}
      ${src ? `<div class="mt-0.5">${src}</div>` : ''}
    </li>`;
  }

  const relSection = (outgoing.length + incoming.length)
    ? `
    <div class="mt-4">
      <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Attested relationships (${outgoing.length + incoming.length})</div>
      <ul class="space-y-1">
        ${outgoing.map(e => renderEdgeRow(e, 'out')).join('')}
        ${incoming.map(e => renderEdgeRow(e, 'in')).join('')}
      </ul>
    </div>`
    : `<div class="mt-4 text-slate-500 text-sm italic">No attested relationships in this corpus.</div>`;

  side.innerHTML = `
    <div class="flex items-center justify-between gap-2">
      <div class="text-lg font-semibold text-slate-100 leading-tight">${escapeHtml(n.id)}</div>
      <button id="closeSel" class="text-slate-500 hover:text-slate-200">×</button>
    </div>
    <div class="mt-1 flex items-center gap-1">${threadDots}${typePill}${bridgePill}</div>
    <div class="mt-2 text-xs text-slate-400">
      <span class="mono">${n.evidenceCount}</span> attesting article(s) ·
      structural score <span class="mono">${(n.score).toFixed(2)}</span>
    </div>
    ${labelsHtml}
    ${dataHtml}
    ${relSection}
  `;

  document.getElementById('closeSel').onclick = () => {
    side.classList.add('hidden');
    document.getElementById('selectionEmpty').classList.remove('hidden');
    clearHighlight();
  };
  side.querySelectorAll('[data-goto]').forEach(a => {
    a.onclick = ev => {
      ev.preventDefault();
      const target = a.dataset.goto;
      const ele = cy.getElementById(target);
      if (ele.length) { cy.center(ele); ele.select(); showEntity(target); }
    };
  });
}

function highlightNeighbourhood(id) {
  cy.elements().addClass('dim');
  cy.elements().removeClass('hi');
  const target = cy.getElementById(id);
  const neighbourhood = target.closedNeighborhood();
  neighbourhood.removeClass('dim');
  neighbourhood.edges().addClass('hi');
}

function clearHighlight() {
  cy.elements().removeClass('dim').removeClass('hi');
  cy.elements().unselect();
  const side = document.getElementById('selectionPanel');
  side.classList.add('hidden');
  document.getElementById('selectionEmpty').classList.remove('hidden');
}

function applyFilters() {
  const minEv = +document.getElementById('minEv').value;
  let visible = 0;
  cy.nodes().forEach(n => {
    const d = n.data();
    const showByThread = d.runs.some(r => threadsOn.has(r));
    const showByType   = typesOn.has(d.type);
    const showByEv     = d.evidenceCount >= minEv;
    const show = showByThread && showByType && showByEv;
    n.style('display', show ? 'element' : 'none');
    if (show) visible++;
  });
  cy.edges().forEach(e => {
    const sVis = e.source().style('display') !== 'none';
    const tVis = e.target().style('display') !== 'none';
    e.style('display', (sVis && tVis) ? 'element' : 'none');
  });
  document.getElementById('filterStatus').textContent =
    `${visible} of ${PAYLOAD.nodes.length} nodes visible`;
  document.getElementById('footerStats').textContent =
    `nodes ${visible}/${PAYLOAD.nodes.length} · edges ${PAYLOAD.edges.length} · bridges ${PAYLOAD.bridges.length}`;
}

function runLayout(name) {
  const cfg = name === 'fcose'
    ? { name: 'fcose', animate: true, randomize: false, nodeRepulsion: 7000, idealEdgeLength: 90, gravity: 0.25 }
    : { name, animate: true, padding: 40 };
  cy.layout(cfg).run();
}

function publisherOf(url) {
  try {
    const h = new URL(url).hostname.replace(/^www\\./,'');
    return h;
  } catch { return url; }
}
function escapeHtml(s) { return String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }
function escapeAttr(s) { return escapeHtml(s).replace(/'/g,'&apos;'); }

applyFilters();
runLayout('fcose');
</script>
</body>
</html>"""


def build_prototype(json_path: Path) -> Path:
    d = json.loads(json_path.read_text())
    payload = _payload(d)
    html = HTML_TEMPLATE
    html = html.replace("__TITLE__", payload["title"])
    html = html.replace("__PAYLOAD__", json.dumps(payload, ensure_ascii=False))
    out = json_path.with_suffix(".graph_prototype.html")
    out.write_text(html)
    print(f"Wrote: {out}")
    print(f"  size: {out.stat().st_size:,} bytes  "
          f"(nodes={len(payload['nodes'])}, edges={len(payload['edges'])}, "
          f"bridges={len(payload['bridges'])})")
    return out


def main():
    if len(sys.argv) < 2:
        print("Usage: build_graph_prototype.py <artifact.json> [<artifact2.json> ...]", file=sys.stderr)
        sys.exit(1)
    for p in sys.argv[1:]:
        build_prototype(Path(p))


if __name__ == "__main__":
    main()
