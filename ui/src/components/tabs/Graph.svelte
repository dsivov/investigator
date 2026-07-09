<script lang="ts">
  import { onMount } from "svelte";
  import { api } from "../../lib/api";
  import { loadCytoscape } from "../../lib/cytoscape";
  import type { GraphPayload, GraphNode, GraphEdge, GraphCommunity } from "../../lib/types";
  import { threadColourMap, ETYPE_COLOR, POLYGON_PALETTE } from "../../lib/colors";
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
  const conflictCount = $derived(
    (graph?.nodes.filter((n) => n.dateConflict).length || 0) +
      (graph?.edges.filter((e) => e.dateConflict).length || 0),
  );
  // Structural (triangulation-backbone) edges keep the merged graph connected.
  // On by default so the graph reads as one component; toggle off to declutter.
  let showStructural = $state(true);

  const colours = $derived(threadColourMap(runs));

  // Node colouring: by thread (default) or by Louvain storyline community.
  let colourMode = $state<"thread" | "community">("thread");
  function communityColour(c: number | undefined): string {
    return c === undefined || c < 0 ? "#475569" : POLYGON_PALETTE[c % POLYGON_PALETTE.length];
  }
  function nodeColour(n: GraphNode): string {
    if (colourMode === "community") return communityColour(n.community);
    return n.isBridge ? "#10b981" : colours[n.runs[0]] || "#64748b";
  }
  function toggleColourMode() {
    colourMode = colourMode === "thread" ? "community" : "thread";
    if (!cy || !graph) return;
    const byId = new Map(graph.nodes.map((n) => [n.id, n]));
    cy.nodes().forEach((el: any) => {
      const n = byId.get(el.id());
      if (n) el.data("faceColour", nodeColour(n));
    });
  }

  // Community (storyline) selection: focus one community, dim the rest, and
  // offer an LLM narration of it in the side panel.
  let selectedCommunity = $state<GraphCommunity | null>(null);
  let communityReportHtml = $state("");
  let communityErr = $state("");
  let analyzingCommunity = $state(false);

  function communityMembers(c: GraphCommunity): GraphNode[] {
    return (graph?.nodes ?? [])
      .filter((n) => n.community === c.id)
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }

  function selectCommunity(c: GraphCommunity) {
    if (!cy || !graph) return;
    selected = null;
    selectedCommunity = c;
    communityReportHtml = "";
    communityErr = "";
    if (colourMode !== "community") toggleColourMode();
    const memberSet = new Set(communityMembers(c).map((n) => n.id));
    cy.elements().addClass("dim");
    const members = cy.nodes().filter((el: any) => memberSet.has(el.id()));
    members.removeClass("dim");
    members.edgesWith(members).removeClass("dim");
    cy.fit(members, 60);
  }

  function clearCommunity() {
    selectedCommunity = null;
    communityReportHtml = "";
    communityErr = "";
    cy?.elements().removeClass("dim");
  }

  function focusNode(id: string) {
    if (!cy || !graph) return;
    const n = graph.nodes.find((x) => x.id === id);
    const el = cy.$id(id);
    if (!n || !el.length) return;
    selectedCommunity = null;
    selected = n;
    cy.elements().addClass("dim");
    el.closedNeighborhood().removeClass("dim");
    cy.animate({ center: { eles: el }, duration: 250 });
  }

  async function summarizeCommunity() {
    if (!selectedCommunity || analyzingCommunity) return;
    analyzingCommunity = true;
    communityErr = "";
    try {
      const [res, { marked }] = await Promise.all([
        api.analyzeCommunity(id, selectedCommunity.id),
        import("marked"),
      ]);
      marked.setOptions({ headerIds: false, mangle: false } as any);
      communityReportHtml = marked.parse(res.report) as string;
    } catch (e: any) {
      communityErr = e?.message || "Storyline analysis failed";
    } finally {
      analyzingCommunity = false;
    }
  }

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
            faceColour: nodeColour(n),
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
      selectedCommunity = null;
      selected = n;
      cy.elements().addClass("dim");
      evt.target.closedNeighborhood().removeClass("dim");
    });
    cy.on("tap", (evt: any) => {
      if (evt.target === cy) {
        selected = null;
        clearCommunity();
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
  {#if graph?.communities?.length}
    <button
      class="chip {colourMode === 'community' ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      title="Colour nodes by Louvain community — structurally cohesive storylines"
      onclick={toggleColourMode}
    >Storylines ({graph.communities.length})</button>
  {/if}
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
  {#if conflictCount > 0}
    <span class="rounded border border-amber-700/50 bg-amber-900/20 px-2 py-1 text-amber-200"
      title="Events/orderings whose dates disagree by more than the tolerance — open them for detail">
      ⚠ {conflictCount} date conflict{conflictCount === 1 ? "" : "s"}
    </span>
  {/if}
  <div class="ml-auto text-slate-400 mono">{filterStatus}</div>
</div>

<div class="flex-1 flex min-h-0">
  <div class="flex-1 relative min-w-0">
    <!-- Cytoscape force-sets inline position:relative on its container, so it
         must be sized directly (h-full), not stretched via absolute inset. -->
    <div bind:this={cyEl} class="w-full h-full"></div>
    {#if colourMode === "community" && graph?.communities?.length}
      <!-- Storyline legend: Louvain communities, largest first -->
      <div class="absolute left-3 bottom-3 max-w-sm max-h-64 overflow-y-auto scrollbar rounded-lg border border-slate-700 bg-slate-900/90 backdrop-blur p-3 text-xs space-y-1">
        <div class="text-slate-500 uppercase tracking-wider mb-1.5">Storylines (Louvain)</div>
        {#each graph.communities.slice(0, 12) as c}
          <button
            class="flex items-center gap-2 w-full text-left rounded px-1 py-0.5
                   {selectedCommunity?.id === c.id ? 'bg-slate-700/60' : 'hover:bg-slate-800/80'}"
            title={c.top.join(" · ")}
            onclick={() => (selectedCommunity?.id === c.id ? clearCommunity() : selectCommunity(c))}
          >
            <span class="inline-block w-2.5 h-2.5 rounded-full flex-shrink-0"
              style="background: {communityColour(c.id)}"></span>
            <span class="text-slate-300 truncate flex-1">{c.label}</span>
            <span class="mono text-slate-500">{c.size}</span>
          </button>
        {/each}
        {#if graph.communities.length > 12}
          <div class="text-slate-600">+ {graph.communities.length - 12} smaller</div>
        {/if}
      </div>
    {/if}
  </div>
  <aside class="w-[420px] flex-shrink-0 border-l border-slate-800 bg-slate-900 overflow-y-auto scrollbar p-5 text-sm">
    <div class="text-slate-500 text-xs uppercase tracking-wider mb-2">Selection</div>
    {#if selectedCommunity}
      {@const members = communityMembers(selectedCommunity)}
      <div class="flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0">
          <span class="inline-block w-3 h-3 rounded-full flex-shrink-0"
            style="background: {communityColour(selectedCommunity.id)}"></span>
          <div class="text-base font-semibold text-slate-100 leading-tight truncate">
            {selectedCommunity.label}
          </div>
        </div>
        <button class="text-slate-500 hover:text-slate-200" onclick={clearCommunity}>×</button>
      </div>
      <div class="mt-1 text-xs text-slate-400">
        Storyline · {selectedCommunity.size} members · mean relevance {selectedCommunity.meanScore}
        {#if selectedCommunity.bridges}· {selectedCommunity.bridges} bridge(s){/if}
      </div>

      <button
        class="mt-3 w-full px-3 py-2 rounded-lg text-xs font-medium bg-sky-800/60 hover:bg-sky-700/60 text-sky-100 border border-sky-700/50 disabled:opacity-40"
        disabled={analyzingCommunity}
        onclick={summarizeCommunity}
      >
        {analyzingCommunity ? "Narrating storyline…" : "✦ Summarize storyline"}
      </button>
      {#if communityErr}
        <div class="text-red-400 text-xs mt-2">{communityErr}</div>
      {/if}
      {#if communityReportHtml}
        <div class="report-md mt-3 text-[13px] leading-relaxed text-slate-300">
          {@html communityReportHtml}
        </div>
      {/if}

      <div class="text-slate-500 text-xs uppercase tracking-wider mt-4 mb-2">
        Members (by relevance)
      </div>
      <div class="space-y-1">
        {#each members.slice(0, 40) as m}
          <button
            class="w-full text-left flex items-baseline justify-between gap-2 rounded px-1.5 py-1 hover:bg-slate-800"
            onclick={() => focusNode(m.id)}
          >
            <span class="text-slate-300 truncate">
              {m.id}
              {#if m.type === "event"}<span class="text-slate-600 text-[10px] ml-1">event</span>{/if}
            </span>
            <span class="mono text-xs text-slate-500">{(m.score ?? 0).toFixed(2)}</span>
          </button>
        {/each}
        {#if members.length > 40}
          <div class="text-xs text-slate-600 px-1.5">+ {members.length - 40} more</div>
        {/if}
      </div>
    {:else if !selected}
      <div class="text-slate-500 italic">
        Click an entity in the graph to see its attested role, sources, and relationships.
        {#if graph?.communities?.length}
          <div class="mt-2">Or turn on <span class="text-slate-300">Storylines</span> and pick a
          community from the legend to analyze it as one story.</div>
        {/if}
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
      {#if selected.community !== undefined && selected.community >= 0 && graph?.communities?.[selected.community]}
        {@const sc = graph.communities[selected.community]}
        <button
          class="mt-2 flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200"
          title="Focus this node's storyline community"
          onclick={() => selectCommunity(sc)}
        >
          <span class="inline-block w-2 h-2 rounded-full" style="background: {communityColour(sc.id)}"></span>
          Storyline: <span class="truncate max-w-[280px]">{sc.label}</span> ›
        </button>
      {/if}
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
      {#if selected.dateConflict}
        <div class="mt-2 rounded border border-amber-700/50 bg-amber-900/20 px-2 py-1 text-xs text-amber-200">
          ⚠ Dates disputed: <span class="mono">{selected.dateConflict.min}</span> vs
          <span class="mono">{selected.dateConflict.max}</span>
          ({selected.dateConflict.daysApart}d apart) — sources disagree, or two events merged.
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
