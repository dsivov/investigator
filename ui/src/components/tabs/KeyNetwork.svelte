<script lang="ts">
  import { onMount, tick } from "svelte";
  import { api } from "../../lib/api";
  import { loadCytoscape } from "../../lib/cytoscape";
  import { ETYPE_COLOR } from "../../lib/colors";
  import type { ConnectorResult } from "../../lib/types";

  let { id }: { id: string } = $props();

  let result = $state<ConnectorResult | null>(null);
  let loadErr = $state("");
  let cy: any = null;
  let cyEl: HTMLDivElement;
  let detail = $state<string | null>(null);

  // Analysis (LLM summary of this representative subgraph).
  let analyzing = $state(false);
  let reportHtml = $state("");
  let analyzeErr = $state("");
  let showReport = $state(false);

  onMount(() => {
    let destroyed = false;
    (async () => {
      try {
        const r = await api.getKeyNetwork(id);
        if (destroyed) return;
        result = r;
        await tick();
        await new Promise((res) => requestAnimationFrame(() => res(null)));
        if (!destroyed && r.nodes.length) await buildCy();
      } catch (e: any) {
        loadErr = e?.message || "Failed to build the key network";
      }
    })();
    return () => {
      destroyed = true;
      if (cy) { cy.destroy(); cy = null; }
    };
  });

  async function buildCy() {
    if (!cyEl || !result) return;
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
            (n.role === "selected" ? " is-selected" : " is-connector") +
            (n.isBroker ? " is-broker" : ""),
        })),
        ...result.edges.map((e) => ({
          data: { ...e, edgeColour: ETYPE_COLOR[e.type] || "#475569" },
        })),
      ],
      style: [
        { selector: "node", style: {
          "background-color": "#64748b", label: "data(label)", color: "#e2e8f0",
          "text-margin-y": -10, "font-size": 10, "font-family": "IBM Plex Sans, system-ui, sans-serif",
          "text-outline-color": "#0b1220", "text-outline-width": 2,
          "border-width": 1.5, "border-color": "#0f172a", width: 26, height: 26 } },
        { selector: "node.is-event", style: { shape: "diamond" } },
        { selector: "node.is-selected", style: {
          "background-color": "#10b981", "border-color": "#34d399", "border-width": 3,
          width: 36, height: 36, "font-size": 11, "font-weight": 700 } },
        { selector: "node.is-connector", style: { "background-color": "#475569", opacity: 0.9 } },
        { selector: "node.is-broker", style: {
          "border-color": "#f59e0b", "border-width": 4, width: 38, height: 38 } },
        { selector: "edge", style: {
          "curve-style": "bezier", "line-color": "data(edgeColour)",
          "target-arrow-color": "data(edgeColour)", "target-arrow-shape": "triangle",
          width: 1.2, "arrow-scale": 0.9, opacity: 0.75 } },
      ],
    });
    cy.on("tap", "node", (evt: any) => {
      const n = result!.nodes.find((x) => x.id === evt.target.id());
      if (n) detail = `${n.id} · ${n.role}${n.isBroker ? " · broker" : ""}`
        + (n.role === "connector" ? ` · betweenness ${n.betweenness.toFixed(3)}` : "");
    });
    cy.on("tap", "edge", (evt: any) => {
      const d = evt.target.data();
      detail = `${d.source} → ${d.target} · ${d.rtype || d.type}${d.context ? " — " + d.context : ""}`;
    });
    cy.resize();
    const layout = cy.layout({ name: "fcose", animate: true, randomize: false,
      nodeRepulsion: 8000, idealEdgeLength: 110, gravity: 0.2, fit: true, padding: 50 });
    layout.one("layoutstop", () => cy && cy.fit(undefined, 50));
    layout.run();
  }

  async function analyse() {
    showReport = true;
    if (reportHtml || analyzing || !result) return;
    analyzing = true;
    analyzeErr = "";
    try {
      const [res, { marked }] = await Promise.all([
        api.analyzeConnections(id, result.selected, "hidden"),
        import("marked"),
      ]);
      if (res.report) {
        marked.setOptions({ headerIds: false, mangle: false } as any);
        reportHtml = marked.parse(res.report) as string;
      } else {
        analyzeErr = res.message || "No analysis produced.";
      }
    } catch (e: any) {
      analyzeErr = e?.message || "Analysis failed";
    } finally {
      analyzing = false;
    }
  }
</script>

<div class="flex flex-col h-full min-h-0">
  <div class="flex items-center gap-3 border-b border-slate-800 bg-slate-900/60 px-5 py-2 text-xs flex-shrink-0">
    <span class="text-slate-300 font-medium">Key network</span>
    {#if result}
      <span class="text-slate-500 mono">
        {result.seed?.seedCount ?? result.stats.selectedCount} key nodes
        ({result.seed?.themeMembers ?? 0} theme · {result.seed?.bridges ?? 0} bridge) ·
        {result.stats.connectorCount} connector(s)
        {#if result.stats.brokerCount} · {result.stats.brokerCount} broker(s){/if} ·
        {result.stats.edgeCount} edge(s)
      </span>
    {/if}
    <div class="ml-auto flex items-center gap-3 text-xs">
      <span class="flex items-center gap-1 text-slate-400"><span class="inline-block w-3 h-3 rounded-full" style="background:#10b981"></span> key</span>
      <span class="flex items-center gap-1 text-slate-400"><span class="inline-block w-3 h-3 rounded-full" style="background:#475569"></span> connector</span>
      {#if result?.stats.brokerCount}
        <span class="flex items-center gap-1 text-slate-400"><span class="inline-block w-3 h-3 rounded-full border-2" style="border-color:#f59e0b;background:#475569"></span> broker</span>
      {/if}
      <button
        class="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-1 text-emerald-200 hover:bg-emerald-900/70 disabled:opacity-50"
        disabled={!result || result.stats.edgeCount === 0 || analyzing}
        onclick={analyse}>{analyzing ? "Analysing…" : "Analyse"}</button>
    </div>
  </div>

  <div class="flex-1 flex min-h-0 relative">
    <div bind:this={cyEl} class="flex-1"></div>
    {#if loadErr}
      <div class="absolute inset-0 grid place-items-center text-red-400 text-sm">{loadErr}</div>
    {:else if result && result.nodes.length === 0}
      <div class="absolute inset-0 grid place-items-center text-slate-500 text-sm">
        Not enough themes/bridges to build a key network (run needs the TMFG stage).
      </div>
    {:else if !result}
      <div class="absolute inset-0 grid place-items-center text-slate-500 text-sm">Building key network…</div>
    {/if}
    {#if detail}
      <div class="absolute bottom-3 left-3 rounded border border-slate-700 bg-slate-900/90 px-3 py-2 text-xs text-slate-300 max-w-[60%]">{detail}</div>
    {/if}
    {#if showReport}
      <aside class="absolute right-0 top-0 bottom-0 w-[460px] max-w-[90%] border-l border-slate-800 bg-slate-900 flex flex-col shadow-2xl">
        <div class="flex items-center justify-between border-b border-slate-800 px-4 py-2 text-sm">
          <span class="font-semibold text-slate-200">Investigation summary</span>
          <button class="text-slate-400 hover:text-slate-200 text-xs" onclick={() => (showReport = false)}>hide</button>
        </div>
        <div class="flex-1 overflow-y-auto scrollbar p-4 text-sm text-slate-300">
          {#if analyzing}<div class="text-slate-500">Summarising the key network…</div>
          {:else if analyzeErr}<div class="text-amber-400">{analyzeErr}</div>
          {:else}<div class="report-md">{@html reportHtml}</div>{/if}
        </div>
      </aside>
    {/if}
  </div>
</div>

<style>
  .report-md :global(h2) { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; color: #94a3b8; margin: 1rem 0 0.4rem; }
  .report-md :global(h2:first-child) { margin-top: 0; }
  .report-md :global(p) { margin: 0.4rem 0; line-height: 1.5; }
  .report-md :global(ul) { list-style: disc; padding-left: 1.1rem; margin: 0.4rem 0; }
  .report-md :global(li) { margin: 0.25rem 0; line-height: 1.45; }
  .report-md :global(strong) { color: #e2e8f0; }
</style>
