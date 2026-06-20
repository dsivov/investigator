<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api } from "../lib/api";
  import { loadCytoscape } from "../lib/cytoscape";
  import { ETYPE_COLOR } from "../lib/colors";
  import type { ConnectorResult } from "../lib/types";

  let {
    result,
    id,
    mode = "shortest_path",
    onClose,
  }: {
    result: ConnectorResult;
    id: string;
    mode?: "shortest_path" | "induced";
    onClose: () => void;
  } = $props();

  let cy: any = null;
  let cyEl: HTMLDivElement;
  let detail = $state<string | null>(null);

  // Analysis (LLM summary of the connected subgraph).
  let analyzing = $state(false);
  let reportHtml = $state<string>("");
  let analyzeErr = $state("");
  let analyzeMsg = $state("");
  let showReport = $state(false);

  async function analyse() {
    showReport = true;
    if (reportHtml || analyzing) return;
    analyzing = true;
    analyzeErr = "";
    analyzeMsg = "";
    try {
      const [res, { marked }] = await Promise.all([
        api.analyzeConnections(id, result.selected, mode),
        import("marked"),
      ]);
      if (res.message) analyzeMsg = res.message;
      if (res.report) {
        marked.setOptions({ headerIds: false, mangle: false } as any);
        reportHtml = marked.parse(res.report) as string;
      }
    } catch (e: any) {
      analyzeErr = e?.message || "Analysis failed";
    } finally {
      analyzing = false;
    }
  }

  onMount(() => {
    let destroyed = false;
    (async () => {
      // The modal is a freshly-opened fixed overlay -- wait for it to be laid
      // out (a paint) before init, else Cytoscape sees a 0x0 container and
      // fcose positions everything into nothing (blank canvas).
      await tick();
      await new Promise((r) => requestAnimationFrame(() => r(null)));
      if (!destroyed) await buildCy();
    })();
    return () => {
      destroyed = true;
      if (cy) { cy.destroy(); cy = null; }
    };
  });

  async function buildCy() {
    if (!cyEl) return;
    const cytoscape = await loadCytoscape();
    if (cy) cy.destroy();
    cy = cytoscape({
      container: cyEl,
      wheelSensitivity: 0.2,
      elements: [
        ...result.nodes.map((n) => ({
          data: { ...n },
          classes:
            (n.type === "event" ? "is-event" : "is-actor") +
            (n.role === "selected" ? " is-selected" : " is-connector"),
        })),
        ...result.edges.map((e) => ({
          data: { ...e, edgeColour: ETYPE_COLOR[e.type] || "#475569" },
        })),
      ],
      style: [
        {
          selector: "node",
          style: {
            "background-color": "#64748b",
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
        { selector: "node.is-event", style: { shape: "diamond" } },
        {
          selector: "node.is-selected",
          style: {
            "background-color": "#10b981",
            "border-color": "#34d399",
            "border-width": 3.5,
            width: 40,
            height: 40,
            "font-size": 12,
            "font-weight": 700,
          },
        },
        {
          selector: "node.is-connector",
          style: { "background-color": "#475569", opacity: 0.9 },
        },
        {
          selector: "edge",
          style: {
            "curve-style": "bezier",
            "line-color": "data(edgeColour)",
            "target-arrow-color": "data(edgeColour)",
            "target-arrow-shape": "triangle",
            label: "data(rtype)",
            "font-size": 7,
            color: "#94a3b8",
            "text-rotation": "autorotate",
            "text-outline-color": "#0b1220",
            "text-outline-width": 2,
            width: 1.4,
            "arrow-scale": 0.9,
            opacity: 0.8,
          },
        },
      ],
    });
    cy.on("tap", "node", (evt: any) => {
      const n = result.nodes.find((x) => x.id === evt.target.id());
      if (n) detail = `${n.id} · ${n.role}${n.type === "event" ? " · event" : ""}`;
    });
    cy.on("tap", "edge", (evt: any) => {
      const d = evt.target.data();
      detail = `${d.source} → ${d.target} · ${d.rtype || d.type}${d.context ? " — " + d.context : ""}`;
    });
    cy.resize();
    const layout = cy.layout({
      name: "fcose", animate: true, randomize: false,
      nodeRepulsion: 8000, idealEdgeLength: 110, gravity: 0.2,
      fit: true, padding: 50,
    });
    layout.one("layoutstop", () => cy && cy.fit(undefined, 50));
    layout.run();
  }
</script>

<div class="fixed inset-0 z-50 flex flex-col bg-slate-950/95">
  <div class="flex items-center gap-3 border-b border-slate-800 px-5 py-3 text-sm">
    <span class="font-semibold text-slate-200">Connections</span>
    <span class="text-slate-400 mono text-xs">
      {result.stats.selectedCount} selected · {result.stats.connectorCount} connector(s) ·
      {result.stats.edgeCount} edge(s)
    </span>
    {#if result.stats.unreachablePairs > 0}
      <span class="text-amber-400 text-xs">
        {result.stats.unreachablePairs} pair(s) not connected
      </span>
    {/if}
    {#if result.missing.length}
      <span class="text-red-400 text-xs">{result.missing.length} id(s) not found</span>
    {/if}
    <div class="ml-auto flex items-center gap-3 text-xs">
      <span class="flex items-center gap-1 text-slate-400">
        <span class="inline-block w-3 h-3 rounded-full" style="background:#10b981"></span> selected
      </span>
      <span class="flex items-center gap-1 text-slate-400">
        <span class="inline-block w-3 h-3 rounded-full" style="background:#475569"></span> connector
      </span>
      <button
        class="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-1 text-emerald-200 hover:bg-emerald-900/70 disabled:opacity-50"
        disabled={result.stats.edgeCount === 0 || analyzing}
        onclick={analyse}
        title="Summarise how the connected entities interrelate (LLM)"
        >{analyzing ? "Analysing…" : "Analyse"}</button
      >
      <button
        class="rounded border border-slate-700 px-3 py-1 text-slate-200 hover:bg-slate-800"
        onclick={onClose}>Close</button
      >
    </div>
  </div>

  <div class="flex-1 flex min-h-0 relative">
    <div bind:this={cyEl} class="flex-1"></div>
    {#if result.nodes.length === 0}
      <div class="absolute inset-0 grid place-items-center text-slate-500">
        No relationships found between the selected entities.
      </div>
    {/if}
    {#if detail}
      <div class="absolute bottom-3 left-3 rounded border border-slate-700 bg-slate-900/90 px-3 py-2 text-xs text-slate-300 max-w-[60%]">
        {detail}
      </div>
    {/if}

    {#if showReport}
      <aside class="absolute right-0 top-0 bottom-0 w-[460px] max-w-[90%] border-l border-slate-800 bg-slate-900 flex flex-col shadow-2xl">
        <div class="flex items-center justify-between border-b border-slate-800 px-4 py-2 text-sm">
          <span class="font-semibold text-slate-200">Connection analysis</span>
          <button class="text-slate-400 hover:text-slate-200 text-xs" onclick={() => (showReport = false)}>hide</button>
        </div>
        <div class="flex-1 overflow-y-auto scrollbar p-4 text-sm text-slate-300">
          {#if analyzing}
            <div class="text-slate-500">Summarising the connected network…</div>
          {:else if analyzeErr}
            <div class="text-red-400">{analyzeErr}</div>
          {:else if analyzeMsg}
            <div class="text-amber-400">{analyzeMsg}</div>
          {:else}
            <div class="report-md">{@html reportHtml}</div>
          {/if}
        </div>
      </aside>
    {/if}
  </div>
</div>

<style>
  .report-md :global(h2) {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #94a3b8;
    margin: 1rem 0 0.4rem;
  }
  .report-md :global(h2:first-child) { margin-top: 0; }
  .report-md :global(p) { margin: 0.4rem 0; line-height: 1.5; }
  .report-md :global(ul) { list-style: disc; padding-left: 1.1rem; margin: 0.4rem 0; }
  .report-md :global(li) { margin: 0.25rem 0; line-height: 1.45; }
  .report-md :global(strong) { color: #e2e8f0; }
  .report-md :global(code) {
    background: #0f172a; padding: 0 0.25rem; border-radius: 3px; font-size: 0.85em;
  }
</style>
