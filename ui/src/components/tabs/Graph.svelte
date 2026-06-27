<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../../lib/api";
  import { loadCytoscape } from "../../lib/cytoscape";
  import type { GraphPayload, GraphNode, GraphEdge } from "../../lib/types";
  import { threadColourMap, ETYPE_COLOR } from "../../lib/colors";
  import { publisherOf, escapeHtml } from "../../lib/helpers";

  let { id, runs }: { id: string; runs: string[] } = $props();

  let graph = $state<GraphPayload | null>(null);
  let cy: any = null;
  let cyEl: HTMLDivElement;
  let selected = $state<GraphNode | null>(null);
  let threadsOn = $state<Set<string>>(new Set());
  let typesOn = $state<Set<string>>(new Set(["entity", "event"]));
  let minEv = $state(0);
  let layoutName = $state("fcose");
  let filterStatus = $state("");
  // Temporal "as of" reconstruction: the payload carries firstSeen/activeWindow
  // per node/edge, so we filter client-side (no relayout) and watch the graph
  // grow over time. allDates is the sorted day-axis; asOfIdx === allDates.length
  // means "all" (no temporal filter).
  let allDates = $state<string[]>([]);
  let asOfIdx = $state(0);
  const asOf = $derived(asOfIdx >= allDates.length ? "" : allDates[asOfIdx]);
  // Structural (triangulation-backbone) edges keep the merged graph connected.
  // On by default so the graph reads as one component; toggle off to declutter.
  let showStructural = $state(true);

  const colours = $derived(threadColourMap(runs));

  // Build once on mount. The Graph component is created fresh each time the
  // tab is opened (InvestigationView swaps tabs with {#if}), so id/runs are
  // stable for this instance -- no need for a reactive $effect here, which
  // previously re-ran in a loop (it both read and mutated `threadsOn`) and
  // rebuilt Cytoscape on every tick, causing the blinking.
  onMount(() => {
    let destroyed = false;
    threadsOn = new Set(runs);
    api.getGraph(id).then((g) => {
      if (destroyed) return;
      graph = g;
      threadsOn = new Set(g.runs);
      const ds = new Set<string>();
      for (const n of g.nodes) if (n.firstSeen) ds.add(n.firstSeen);
      for (const e of g.edges) {
        if (e.firstSeen) ds.add(e.firstSeen);
        if (e.activeWindow && e.activeWindow[0]) ds.add(e.activeWindow[0]);
      }
      allDates = [...ds].sort();
      asOfIdx = allDates.length; // start at "all"
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

  async function buildCy() {
    if (!graph || !cyEl) return;
    const cytoscape = await loadCytoscape();

    if (cy) cy.destroy();

    cy = cytoscape({
      container: cyEl,
      wheelSensitivity: 0.2,
      elements: [
        ...graph.nodes.map((n) => ({
          data: {
            ...n,
            faceColour: n.isBridge ? "#10b981" : (colours[n.runs[0]] || "#64748b"),
          },
          classes:
            (n.type === "event" ? "is-event" : "is-actor") +
            (n.isBridge ? " is-bridge" : ""),
        })),
        ...graph.edges.map((e) => ({
          data: { ...e, edgeColour: ETYPE_COLOR[e.type] || "#475569" },
          classes: e.structural ? "structural" : "",
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "data(faceColour)",
            label: "data(label)",
            color: "#e2e8f0",
            "text-margin-y": -10,
            "font-size": 10,
            "font-family": "IBM Plex Sans, system-ui, sans-serif",
            "text-outline-color": "#0b1220",
            "text-outline-width": 2,
            "border-width": 1.5,
            "border-color": "#0f172a",
            width: 26,
            height: 26,
          },
        },
        {
          selector: "node.is-event",
          style: { shape: "diamond", width: 22, height: 22 },
        },
        {
          selector: "node.is-bridge",
          style: {
            "border-color": "#10b981",
            "border-width": 3.5,
            width: 38,
            height: 38,
            "font-size": 12,
            "font-weight": 700,
          },
        },
        {
          selector: "node.dim",
          style: { opacity: 0.18 },
        },
        {
          selector: "edge",
          style: {
            "curve-style": "bezier",
            "line-color": "data(edgeColour)",
            "target-arrow-color": "data(edgeColour)",
            "target-arrow-shape": "triangle",
            width: 1.2,
            "arrow-scale": 0.9,
            opacity: 0.75,
          },
        },
        {
          selector: 'edge[type = "event_followed_by"], edge[type = "event_coincident"]',
          style: { "line-style": "dashed" },
        },
        {
          selector: 'edge[type = "claimed_caused_by"]',
          style: { width: 2.5 },
        },
        {
          // Structural (triangulation-backbone) edges: faint, thin, no arrow.
          // They keep the graph connected without competing visually with the
          // attested relationships.
          selector: "edge.structural",
          style: {
            "line-color": "#334155",
            "target-arrow-shape": "none",
            width: 0.8,
            opacity: 0.4,
            "line-style": "dotted",
          },
        },
      ],
    });
    cy.on("tap", "node", (evt: any) => {
      const id = evt.target.id();
      const n = graph?.nodes.find((x) => x.id === id);
      if (!n) return;
      selected = n;
      cy.elements().addClass("dim");
      evt.target.closedNeighborhood().removeClass("dim");
    });
    cy.on("tap", (evt: any) => {
      if (evt.target === cy) {
        selected = null;
        cy.elements().removeClass("dim");
      }
    });
    applyFilters();
    runLayout("fcose");
  }

  // Effective "asserted" date of an edge: observed pub date, else inferred
  // active-window start. "" = undated (always present under an as-of filter).
  function edgeStart(d: any): string {
    return d.firstSeen || (d.activeWindow && d.activeWindow[0]) || "";
  }

  function applyFilters() {
    if (!cy) return;
    // Base node pass (threads / types / min-articles) + as-of for events.
    const baseShow = new Map<string, boolean>();
    cy.nodes().forEach((n: any) => {
      const d = n.data();
      let ok =
        d.runs.some((r: string) => threadsOn.has(r)) &&
        typesOn.has(d.type) &&
        d.evidenceCount >= minEv;
      if (ok && asOf && d.type === "event" && d.firstSeen && d.firstSeen > asOf) ok = false;
      baseShow.set(d.id, ok);
    });
    // Edge pass: endpoints visible, structural toggle, edge asserted by as-of.
    const edgeVis = new Map<string, boolean>();
    cy.edges().forEach((e: any) => {
      const d = e.data();
      let ok = !!baseShow.get(d.source) && !!baseShow.get(d.target);
      if (ok && e.hasClass("structural") && !showStructural) ok = false;
      if (ok && asOf) {
        const st = edgeStart(d);
        if (st && st > asOf) ok = false;
      }
      edgeVis.set(e.id(), ok);
    });
    // Under an as-of filter, prune entities whose only surviving links are
    // structural hub edges -- so relationships appear over time rather than
    // every actor staying wired to the relevance hub.
    const realDeg = new Set<string>();
    cy.edges().forEach((e: any) => {
      if (edgeVis.get(e.id()) && !e.hasClass("structural")) {
        const d = e.data();
        realDeg.add(d.source);
        realDeg.add(d.target);
      }
    });
    let visible = 0;
    cy.nodes().forEach((n: any) => {
      const d = n.data();
      let show = !!baseShow.get(d.id);
      if (show && asOf && d.type !== "event" && !realDeg.has(d.id)) show = false;
      n.style("display", show ? "element" : "none");
      if (show) visible++;
    });
    cy.edges().forEach((e: any) => {
      const vis =
        edgeVis.get(e.id()) &&
        e.source().style("display") !== "none" &&
        e.target().style("display") !== "none";
      e.style("display", vis ? "element" : "none");
    });
    filterStatus =
      `${visible} of ${graph?.nodes.length} nodes visible` +
      (asOf ? ` · as of ${asOf}` : "");
  }

  function runLayout(name: string) {
    if (!cy) return;
    const cfg =
      name === "fcose"
        ? {
            name: "fcose",
            animate: true,
            randomize: false,
            nodeRepulsion: 7000,
            idealEdgeLength: 90,
            gravity: 0.25,
          }
        : { name, animate: true, padding: 40 };
    cy.layout(cfg).run();
  }

  function toggleThread(r: string) {
    if (threadsOn.has(r)) threadsOn.delete(r);
    else threadsOn.add(r);
    threadsOn = new Set(threadsOn);
    applyFilters();
  }

  function toggleType(t: string) {
    if (typesOn.has(t)) typesOn.delete(t);
    else typesOn.add(t);
    typesOn = new Set(typesOn);
    applyFilters();
  }

  function relationships(): { incoming: GraphEdge[]; outgoing: GraphEdge[] } {
    if (!selected || !graph) return { incoming: [], outgoing: [] };
    // Exclude structural backbone edges -- they carry no attested context.
    return {
      incoming: graph.edges.filter((e) => e.target === selected!.id && !e.structural),
      outgoing: graph.edges.filter((e) => e.source === selected!.id && !e.structural),
    };
  }
</script>

<div class="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/60 px-5 py-2 text-xs flex-shrink-0">
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">Threads</span>
    {#each runs as r}
      <button
        class="chip {threadsOn.has(r) ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
        onclick={() => toggleThread(r)}
      >
        <span class="inline-block w-2 h-2 rounded-full mr-1 align-middle" style="background: {colours[r]}"></span>
        {r}
      </button>
    {/each}
  </div>
  <span class="text-slate-700">·</span>
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">Types</span>
    <button
      class="chip {typesOn.has('entity') ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      onclick={() => toggleType("entity")}
    >Actor</button>
    <button
      class="chip {typesOn.has('event') ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      onclick={() => toggleType("event")}
    >Event</button>
  </div>
  <span class="text-slate-700">·</span>
  <button
    class="chip {showStructural ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
    title="Faint backbone edges that keep the graph connected"
    onclick={() => {
      showStructural = !showStructural;
      applyFilters();
    }}
  >Backbone</button>
  <span class="text-slate-700">·</span>
  <div class="flex items-center gap-2">
    <span class="text-slate-500">Min articles</span>
    <input
      type="range"
      min="0"
      max="20"
      bind:value={minEv}
      oninput={applyFilters}
      class="accent-emerald-500"
    />
    <span class="mono w-6 text-slate-300">{minEv}</span>
  </div>
  {#if allDates.length > 1}
    <span class="text-slate-700">·</span>
    <div class="flex items-center gap-2">
      <span class="text-slate-500" title="Reconstruct the graph as it was known by this date">As of</span>
      <input
        type="range"
        min="0"
        max={allDates.length}
        bind:value={asOfIdx}
        oninput={applyFilters}
        class="accent-sky-500"
      />
      <span class="mono text-slate-300 w-20 text-right">{asOf || "all"}</span>
    </div>
  {/if}
  <span class="text-slate-700">·</span>
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">Layout</span>
    <select
      class="bg-slate-800 border border-slate-700 rounded px-2 py-1"
      bind:value={layoutName}
      onchange={() => runLayout(layoutName)}
    >
      <option value="fcose">Force (fcose)</option>
      <option value="cose">Spring (cose)</option>
      <option value="concentric">Concentric</option>
      <option value="breadthfirst">Hierarchy</option>
      <option value="circle">Circle</option>
    </select>
  </div>
  <button
    class="px-2 py-1 rounded border border-slate-700 hover:bg-slate-800 text-slate-300"
    onclick={() => cy?.fit(undefined, 40)}
  >Fit</button>
  <div class="ml-auto text-slate-400 mono">{filterStatus}</div>
</div>

<div class="flex-1 flex min-h-0">
  <div bind:this={cyEl} class="flex-1"></div>
  <aside class="w-[420px] flex-shrink-0 border-l border-slate-800 bg-slate-900 overflow-y-auto scrollbar p-5 text-sm">
    <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Selection</div>
    {#if !selected}
      <div class="text-slate-500 italic">
        Click an entity in the graph to see its attested role, sources, and relationships.
      </div>
    {:else}
      {@const rel = relationships()}
      <div class="flex items-center justify-between gap-2">
        <div class="text-lg font-semibold text-slate-100 leading-tight">{selected.id}</div>
        <button
          class="text-slate-500 hover:text-slate-200"
          onclick={() => {
            selected = null;
            cy?.elements().removeClass("dim");
          }}
        >×</button>
      </div>
      <div class="mt-1 flex items-center gap-1">
        {#each selected.runs as r}
          <span
            class="inline-block w-2 h-2 rounded-full mr-1"
            title={r}
            style="background: {colours[r] || '#64748b'}"
          ></span>
        {/each}
        <span class="inline-block bg-slate-700/60 text-slate-300 text-xs rounded px-2 py-0.5 ml-2">
          {selected.type === "event" ? "Event" : "Actor"}
        </span>
        {#if selected.isBridge}
          <span class="inline-block bg-emerald-900/40 text-emerald-300 text-xs rounded px-2 py-0.5 ml-2">
            Bridge · {selected.runs.length} threads
          </span>
        {/if}
      </div>
      <div class="mt-2 text-xs text-slate-400">
        <span class="mono">{selected.evidenceCount}</span> attesting article(s) · structural score
        <span class="mono">{selected.score.toFixed(2)}</span>
      </div>
      {#if selected.labels.length}
        <div class="mt-2 text-xs text-slate-400">
          <span class="text-slate-500">Also known as:</span>
          {#each selected.labels as l, i}
            <span class="mono text-slate-300">{l}</span>{#if i < selected.labels.length - 1} · {/if}
          {/each}
        </div>
      {/if}

      {#if rel.incoming.length + rel.outgoing.length > 0}
        <div class="mt-4">
          <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">
            Attested relationships ({rel.incoming.length + rel.outgoing.length})
          </div>
          <ul class="space-y-1">
            {#each rel.outgoing as e}
              <li
                class="border-l-2 pl-3 py-1"
                style="border-color: {ETYPE_COLOR[e.type] || '#475569'}"
              >
                <div class="text-slate-200">
                  <span class="text-slate-500 mono">→</span> {e.target}
                  {#if e.rtype}<span class="text-xs text-slate-500 italic">{e.rtype}</span>{/if}
                </div>
                {#if e.context}
                  <div class="text-xs text-slate-400 mt-0.5 leading-snug">"{e.context}"</div>
                {/if}
                {#if e.url}
                  <div class="mt-0.5">
                    <a class="text-emerald-400 hover:underline text-xs mono" target="_blank" href={e.url}
                      >{publisherOf(e.url)}</a
                    >
                  </div>
                {/if}
              </li>
            {/each}
            {#each rel.incoming as e}
              <li
                class="border-l-2 pl-3 py-1"
                style="border-color: {ETYPE_COLOR[e.type] || '#475569'}"
              >
                <div class="text-slate-200">
                  <span class="text-slate-500 mono">←</span> {e.source}
                  {#if e.rtype}<span class="text-xs text-slate-500 italic">{e.rtype}</span>{/if}
                </div>
                {#if e.context}
                  <div class="text-xs text-slate-400 mt-0.5 leading-snug">"{e.context}"</div>
                {/if}
                {#if e.url}
                  <div class="mt-0.5">
                    <a class="text-emerald-400 hover:underline text-xs mono" target="_blank" href={e.url}
                      >{publisherOf(e.url)}</a
                    >
                  </div>
                {/if}
              </li>
            {/each}
          </ul>
        </div>
      {:else}
        <div class="mt-4 text-slate-500 text-sm italic">No attested relationships in this corpus.</div>
      {/if}
    {/if}
  </aside>
</div>
