<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../../lib/api";
  import { loadCytoscape } from "../../lib/cytoscape";
  import type { TmfgPayload, ThemePayload } from "../../lib/types";
  import { POLYGON_PALETTE, threadColourMap } from "../../lib/colors";
  import { publisherOf } from "../../lib/helpers";

  let { id, runs }: { id: string; runs: string[] } = $props();

  let payload = $state<TmfgPayload | null>(null);
  let selected = $state<number | null>(null);
  // Detail inspection: a clicked member node or attested edge.
  let detailNode = $state<any | null>(null);
  let detailEdge = $state<any | null>(null);
  let topN = $state(8);
  // A single-query run has no cross-thread themes, so "cross-only" would hide
  // every theme. Default is set in onMount from the thread count.
  let crossOnly = $state(false);
  let edgeKinds = $state<Set<string>>(new Set(["attested", "fillin"]));
  let statusText = $state("");

  let cy: any = null;
  let cyEl: HTMLDivElement;
  let svgEl: SVGSVGElement;

  const colours = $derived(threadColourMap(runs));

  onMount(() => {
    let destroyed = false;
    crossOnly = runs.length > 1;
    api.getTmfg(id).then((p) => {
      if (destroyed) return;
      payload = p;
      buildCy();
    });
    return () => {
      destroyed = true;
      if (cy) {
        cy.destroy();
        cy = null;
      }
    };
  });

  // Themes after the crossOnly + topN filters -- the single source of truth
  // for both the canvas and the right-hand list.
  function shownThemes(): ThemePayload[] {
    if (!payload) return [];
    const arr = crossOnly
      ? payload.themes.filter((t) => (t.runs || []).length >= 2)
      : payload.themes;
    return topN > 0 ? arr.slice(0, topN) : arr;
  }

  function pairKey(a: string, b: string) {
    return a < b ? `${a}||${b}` : `${b}||${a}`;
  }

  async function buildCy() {
    if (!payload || !cyEl) return;
    const cytoscape = await loadCytoscape();

    const themes = shownThemes();
    const memberSet = new Set<string>();
    themes.forEach((t) => t.members.forEach((m) => memberSet.add(m)));
    const nodes = payload.nodes.filter((n) => memberSet.has(n.id));

    // Attested edges between members (from the payload) + fill-in edges for
    // tetrahedron-internal pairs that aren't already attested.
    const attestedPairs = new Set<string>();
    const attested = payload.edges.filter(
      (e) => memberSet.has(e.source) && memberSet.has(e.target)
    );
    attested.forEach((e) => attestedPairs.add(pairKey(e.source, e.target)));
    const fillin: Array<{ source: string; target: string }> = [];
    themes.forEach((t) => {
      const m = t.members;
      for (let i = 0; i < m.length; i++) {
        for (let j = i + 1; j < m.length; j++) {
          const k = pairKey(m[i], m[j]);
          if (!attestedPairs.has(k) && memberSet.has(m[i]) && memberSet.has(m[j])) {
            attestedPairs.add(k);
            fillin.push({ source: m[i], target: m[j] });
          }
        }
      }
    });

    if (cy) cy.destroy();
    cy = cytoscape({
      container: cyEl,
      wheelSensitivity: 0.2,
      elements: [
        ...nodes.map((n) => ({
          data: {
            ...n,
            faceColour: n.isBridge ? "#10b981" : colours[n.runs[0]] || "#64748b",
          },
          classes:
            (n.type === "event" ? "is-event" : "is-actor") +
            (n.isBridge ? " is-bridge" : ""),
        })),
        ...attested.map((e, i) => ({
          data: { id: `a${i}`, source: e.source, target: e.target, kind: "attested", edgeColour: "#52525b" },
        })),
        ...fillin.map((e, i) => ({
          data: { id: `f${i}`, source: e.source, target: e.target, kind: "fillin", edgeColour: "#fb923c" },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(faceColour)",
            label: "data(label)",
            color: "#e2e8f0",
            "font-size": 10,
            "text-margin-y": -10,
            "font-family": "IBM Plex Sans, system-ui, sans-serif",
            "text-outline-color": "#0b1220",
            "text-outline-width": 2,
            "border-width": 1.5,
            "border-color": "#0f172a",
            width: 26,
            height: 26,
          },
        },
        { selector: "node.is-event", style: { shape: "diamond", width: 22, height: 22 } },
        {
          selector: "node.is-bridge",
          style: {
            "border-color": "#10b981",
            "border-width": 3.5,
            width: 40,
            height: 40,
            "font-size": 12,
            "font-weight": 700,
          },
        },
        { selector: "node.dim", style: { opacity: 0.12 } },
        {
          selector: "edge",
          style: {
            "curve-style": "bezier",
            "line-color": "data(edgeColour)",
            "target-arrow-color": "data(edgeColour)",
            width: 1.2,
            opacity: 0.75,
          },
        },
        { selector: 'edge[kind = "fillin"]', style: { "line-style": "dashed", "target-arrow-shape": "none" } },
        { selector: 'edge[kind = "attested"]', style: { "target-arrow-shape": "triangle", "arrow-scale": 0.9 } },
      ],
    });

    cy.on("pan zoom resize", drawPolygons);
    cy.on("layoutstop", drawPolygons);
    cy.on("tap", "node", (evt: any) => {
      const nid = evt.target.id();
      detailEdge = null;
      detailNode = payload?.nodes.find((n) => n.id === nid) ?? null;
    });
    cy.on("tap", "edge", (evt: any) => {
      const d = evt.target.data();
      detailNode = null;
      // Only attested edges carry relation context; fill-in edges are hypotheses.
      detailEdge = {
        source: d.source,
        target: d.target,
        kind: d.kind,
        type: d.type,
        rtype: d.rtype,
        context: d.context,
        url: d.url,
      };
    });
    cy.on("tap", (evt: any) => {
      if (evt.target === cy) {
        selectTheme(null);
        detailNode = null;
        detailEdge = null;
      }
    });

    applyEdgeFilter();
    // Defer layout to the next frame. The canvas lives in an
    // `absolute inset-0` container, which can measure 0x0 at the instant
    // cytoscape() runs inside this async callback; fcose would then place
    // every node at the origin and the graph reads as blank. Resizing on
    // the next animation frame guarantees the container has real
    // dimensions before the layout computes positions.
    requestAnimationFrame(() => {
      if (!cy) return;
      cy.resize();
      cy.layout({
        name: "fcose",
        animate: true,
        randomize: false,
        nodeRepulsion: 6500,
        idealEdgeLength: 90,
        gravity: 0.3,
      }).run();
    });

    statusText = `${themes.length} themes · ${nodes.length} actors · ${attested.length + fillin.length} edges (${fillin.length} fill-in)`;
  }

  function applyEdgeFilter() {
    if (!cy) return;
    cy.edges().forEach((e: any) => {
      e.style("display", edgeKinds.has(e.data("kind")) ? "element" : "none");
    });
  }

  function toggleEdgeKind(k: string) {
    if (edgeKinds.has(k)) edgeKinds.delete(k);
    else edgeKinds.add(k);
    edgeKinds = new Set(edgeKinds);
    applyEdgeFilter();
  }

  function selectTheme(idx: number | null) {
    selected = idx;
    if (!cy) return;
    if (idx === null) {
      cy.nodes().removeClass("dim");
    } else {
      const t = shownThemes()[idx];
      if (t) {
        cy.nodes().addClass("dim");
        t.members.forEach((m) => cy.getElementById(m).removeClass("dim"));
      }
    }
    drawPolygons();
  }

  function drawPolygons() {
    if (!cy || !svgEl) return;
    const rect = cyEl.getBoundingClientRect();
    svgEl.setAttribute("width", String(rect.width));
    svgEl.setAttribute("height", String(rect.height));
    svgEl.setAttribute("viewBox", `0 0 ${rect.width} ${rect.height}`);
    while (svgEl.firstChild) svgEl.removeChild(svgEl.firstChild);

    const themes = shownThemes();
    themes.forEach((t, i) => {
      const colour = POLYGON_PALETTE[i % POLYGON_PALETTE.length];
      const pts = t.members
        .map((m) => {
          const e = cy.getElementById(m);
          if (!e.length) return null;
          const p = e.renderedPosition();
          return [p.x, p.y] as [number, number];
        })
        .filter((p): p is [number, number] => p !== null);
      if (pts.length < 3) return;

      const cx = pts.reduce((s, p) => s + p[0], 0) / pts.length;
      const cyy = pts.reduce((s, p) => s + p[1], 0) / pts.length;
      pts.sort(
        (a, b) =>
          Math.atan2(a[1] - cyy, a[0] - cx) - Math.atan2(b[1] - cyy, b[0] - cx)
      );
      const isSel = selected === i;

      const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      poly.setAttribute("points", pts.map((p) => `${p[0]},${p[1]}`).join(" "));
      poly.setAttribute("fill", colour);
      poly.setAttribute("fill-opacity", String(isSel ? 0.32 : 0.13));
      poly.setAttribute("stroke", colour);
      poly.setAttribute("stroke-width", String(isSel ? 2.5 : 1.2));
      poly.setAttribute("stroke-dasharray", isSel ? "" : "5 4");
      poly.setAttribute("stroke-opacity", "0.85");
      poly.style.pointerEvents = "auto";
      poly.style.cursor = "pointer";
      poly.addEventListener("click", () => selectTheme(i));
      svgEl.appendChild(poly);

      const lab = document.createElementNS("http://www.w3.org/2000/svg", "text");
      lab.setAttribute("x", String(cx));
      lab.setAttribute("y", String(cyy));
      lab.setAttribute("text-anchor", "middle");
      lab.setAttribute("dominant-baseline", "middle");
      lab.setAttribute("fill", colour);
      lab.setAttribute("font-size", String(isSel ? 13 : 10));
      lab.setAttribute("font-weight", String(isSel ? 700 : 500));
      lab.style.pointerEvents = "none";
      lab.textContent = `T${i + 1} · w${t.weight.toFixed(1)}`;
      svgEl.appendChild(lab);
    });
  }

  // Rebuild the scene whenever topN / crossOnly change.
  function onFilterChange() {
    selected = null;
    buildCy();
  }
</script>

<div
  class="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/60 px-5 py-2 text-xs flex-shrink-0"
>
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">Show top</span>
    <select
      class="bg-slate-800 border border-slate-700 rounded px-2 py-1"
      bind:value={topN}
      onchange={onFilterChange}
    >
      <option value={5}>5 themes</option>
      <option value={8}>8 themes</option>
      <option value={12}>12 themes</option>
      <option value={0}>All</option>
    </select>
  </div>
  <span class="text-slate-700">·</span>
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">Edges</span>
    <button
      class="chip {edgeKinds.has('attested') ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      onclick={() => toggleEdgeKind("attested")}>Attested</button
    >
    <button
      class="chip {edgeKinds.has('fillin') ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      onclick={() => toggleEdgeKind("fillin")}>TMFG fill-in</button
    >
  </div>
  <span class="text-slate-700">·</span>
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">Cross-thread only</span>
    <button
      class="chip {crossOnly ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      onclick={() => {
        crossOnly = !crossOnly;
        onFilterChange();
      }}>{crossOnly ? "Yes" : "No"}</button
    >
  </div>
  <button
    class="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300"
    onclick={() => cy?.fit(undefined, 50)}>Fit</button
  >
  <button
    class="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300"
    onclick={() =>
      cy
        ?.layout({ name: "fcose", animate: true, randomize: false, nodeRepulsion: 6500, idealEdgeLength: 90, gravity: 0.3 })
        .run()}>Re-layout</button
  >
  <div class="ml-auto text-slate-400 mono">{statusText}</div>
</div>

<div class="flex-1 flex min-h-0">
  <div class="relative flex-1 min-w-0 min-h-0">
    <div bind:this={cyEl} class="w-full h-full"></div>
    <svg
      bind:this={svgEl}
      class="absolute inset-0"
      style="pointer-events: none"
      xmlns="http://www.w3.org/2000/svg"
    ></svg>
  </div>

  <aside
    class="w-[440px] flex-shrink-0 border-l border-slate-800 bg-slate-900 overflow-y-auto scrollbar p-5 text-sm"
  >
    {#if detailNode}
      <!-- Node detail: evidence + relations, in place -->
      <div class="flex items-center justify-between mb-2">
        <div class="text-lg font-semibold text-slate-100 leading-tight">{detailNode.id}</div>
        <button class="text-slate-500 hover:text-slate-200" onclick={() => (detailNode = null)}>×</button>
      </div>
      <div class="flex items-center gap-1 mb-2">
        {#each detailNode.runs as r}
          <span class="inline-block w-2 h-2 rounded-full" title={r} style="background: {colours[r] || '#64748b'}"></span>
        {/each}
        <span class="inline-block bg-slate-700/60 text-slate-300 text-xs rounded px-2 py-0.5 ml-1">
          {detailNode.type === "event" ? "Event" : "Actor"}
        </span>
        {#if detailNode.isBridge}
          <span class="inline-block bg-emerald-900/40 text-emerald-300 text-xs rounded px-2 py-0.5 ml-1">Bridge</span>
        {/if}
      </div>
      <div class="text-xs text-slate-400 mb-2">
        <span class="mono">{detailNode.evidenceCount}</span> attesting article(s) · score
        <span class="mono">{(detailNode.score ?? 0).toFixed(2)}</span>
      </div>
      {#if detailNode.labels?.length}
        <div class="text-xs text-slate-400 mb-2">
          <span class="text-slate-500">aka:</span>
          {#each detailNode.labels as l}<span class="mono text-slate-300">{l}</span> {/each}
        </div>
      {/if}
      {#if detailNode.data?.position || detailNode.data?.location || detailNode.data?.date}
        <div class="text-xs text-slate-300 mb-2 space-y-0.5">
          {#if detailNode.data.position}<div><span class="text-slate-500">Role:</span> {detailNode.data.position}</div>{/if}
          {#if detailNode.data.location}<div><span class="text-slate-500">Location:</span> {detailNode.data.location}</div>{/if}
          {#if detailNode.data.date}<div><span class="text-slate-500">Date:</span> {detailNode.data.date}</div>{/if}
        </div>
      {/if}
      {#if detailNode.evidence?.length}
        <div class="text-slate-500 text-xs uppercase tracking-wider mt-3 mb-1">
          Evidence ({detailNode.evidence.length})
        </div>
        <ul class="space-y-2">
          {#each detailNode.evidence as ev}
            <li class="border-l-2 pl-2 {ev.supports ? 'border-emerald-700' : 'border-red-700'}">
              <div class="text-[10px] uppercase tracking-wider {ev.supports ? 'text-emerald-400' : 'text-red-400'}">
                {ev.supports ? "supports" : "contradicts"} · str {ev.strength?.toFixed?.(2)} · conf {ev.confidence?.toFixed?.(2)}
              </div>
              <div class="text-xs text-slate-300 mt-0.5">{ev.reasoning}</div>
              {#each ev.quotes ?? [] as qt}
                <div class="text-[11px] text-slate-500 mt-0.5">"{qt}"</div>
              {/each}
              {#if ev.source?.startsWith?.("http")}
                <a href={ev.source} target="_blank" class="text-[11px] text-emerald-400 hover:underline mono"
                  >{publisherOf(ev.source)}</a
                >
              {/if}
            </li>
          {/each}
        </ul>
      {:else}
        <div class="text-slate-500 italic text-xs mt-2">No evidence records on this node.</div>
      {/if}
      <hr class="border-slate-800 my-4" />
    {:else if detailEdge}
      <!-- Edge detail: the relationship between two theme members -->
      <div class="flex items-center justify-between mb-2">
        <div class="text-slate-300 text-sm font-medium">Relationship</div>
        <button class="text-slate-500 hover:text-slate-200" onclick={() => (detailEdge = null)}>×</button>
      </div>
      <div class="text-slate-200 mb-1">
        {detailEdge.source} <span class="text-slate-500 mono">→</span> {detailEdge.target}
      </div>
      <div class="text-xs mb-2">
        {#if detailEdge.kind === "fillin"}
          <span class="text-amber-400">TMFG fill-in — structural hypothesis, not attested in any article.</span>
        {:else}
          <span class="text-slate-400 italic">{detailEdge.rtype || detailEdge.type}</span>
        {/if}
      </div>
      {#if detailEdge.context}
        <div class="text-xs text-slate-300 border-l-2 border-slate-700 pl-2">"{detailEdge.context}"</div>
      {/if}
      {#if detailEdge.url}
        <div class="mt-1">
          <a href={detailEdge.url} target="_blank" class="text-[11px] text-emerald-400 hover:underline mono"
            >{publisherOf(detailEdge.url)}</a
          >
        </div>
      {/if}
      <hr class="border-slate-800 my-4" />
    {/if}

    <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Themes</div>
    {#if !payload}
      <div class="text-slate-500 italic text-xs">Loading…</div>
    {:else}
      {@const themes = shownThemes()}
      <div class="space-y-2">
        {#each themes as t, i}
          {@const colour = POLYGON_PALETTE[i % POLYGON_PALETTE.length]}
          <button
            class="w-full text-left border rounded-md p-2 hover:border-slate-600
                   {selected === i ? 'border-emerald-600 bg-emerald-900/10' : 'border-slate-800'}"
            onclick={() => selectTheme(selected === i ? null : i)}
          >
            <div class="flex items-center justify-between gap-2">
              <div class="flex items-center gap-2">
                <span
                  class="inline-block w-3 h-3 rounded"
                  style="background: {colour}; opacity:.4; border: 1px solid {colour}"
                ></span>
                <span class="text-slate-300 text-xs font-semibold">Theme {i + 1}</span>
                <span class="text-slate-500 text-[10px] mono">w {t.weight.toFixed(1)}</span>
              </div>
              <div class="flex gap-0.5">
                {#each t.runs as r}
                  <span class="inline-block w-2 h-2 rounded-full" style="background: {colours[r] || '#64748b'}"></span>
                {/each}
              </div>
            </div>
            <div class="mt-1 text-[11px] text-slate-400 truncate">{t.members.join(" · ")}</div>
            {#if selected === i}
              <div class="mt-2 pt-2 border-t border-slate-800">
                <div class="text-[10px] text-slate-500 uppercase tracking-wider">
                  {t.isCross ? `Spans ${t.runs.length} thread(s)` : "Within one thread"}
                </div>
                {#if t.urls.length}
                  <div class="mt-1 space-y-0.5">
                    {#each t.urls as u}
                      <a href={u} target="_blank" class="block text-[11px] text-emerald-400 hover:underline mono truncate">
                        {publisherOf(u)}
                      </a>
                    {/each}
                  </div>
                {:else}
                  <div class="mt-1 text-[11px] text-slate-500 italic">No attesting article URLs attached.</div>
                {/if}
              </div>
            {/if}
          </button>
        {/each}
      </div>
    {/if}
  </aside>
</div>
