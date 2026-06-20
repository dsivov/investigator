<script lang="ts">
  import { api } from "../../lib/api";
  import type { GraphPayload, GraphNode } from "../../lib/types";
  import { threadColourMap } from "../../lib/colors";
  import { publisherOf } from "../../lib/helpers";

  let { id }: { id: string } = $props();
  let graph = $state<GraphPayload | null>(null);
  let view = $state<"entities" | "events" | "relationships" | "evidence">("entities");
  let q = $state("");
  // Default the Actors view to `score` (relevance × prob = distance-to-subject
  // weighted), not raw evidence count: on a broad single-subject query, raw
  // prob/evidence floats topically-related-but-off-subject entities to the top
  // (e.g. unrelated corruption cases), while `score` keeps the subject's own
  // network on top.
  let sortKey = $state("score");
  let sortDir = $state<"asc" | "desc">("desc");
  const colours = $derived(graph ? threadColourMap(graph.runs) : {});

  $effect(() => {
    api.getGraph(id).then((g) => (graph = g));
  });

  // Flatten every entity's evidence records into rows for the Evidence view.
  const evidenceRows = $derived.by(() => {
    if (!graph) return [] as any[];
    const out: any[] = [];
    for (const n of graph.nodes) {
      for (const ev of n.evidence ?? []) {
        out.push({ entity: n.id, ...ev });
      }
    }
    return out;
  });

  const rows = $derived.by(() => {
    if (!graph) return [] as any[];
    let arr: any[];
    if (view === "entities") arr = graph.nodes.filter((n) => n.type === "entity");
    else if (view === "events") arr = graph.nodes.filter((n) => n.type === "event");
    else if (view === "evidence") arr = evidenceRows;
    else arr = graph.edges;
    if (q) {
      const lq = q.toLowerCase();
      arr = arr.filter((r) => JSON.stringify(r).toLowerCase().includes(lq));
    }
    // Sort a COPY: `arr` may be a $state array (graph.edges) or a $derived
    // (evidenceRows). Sorting in place mutates that value from inside this
    // $derived.by, which Svelte 5 forbids (state_unsafe_mutation) and which
    // broke the relationships/evidence views.
    const sorted = [...arr].sort((a, b) => {
      const va = a[sortKey] ?? a.data?.[sortKey] ?? "";
      const vb = b[sortKey] ?? b.data?.[sortKey] ?? "";
      const cmp =
        typeof va === "number" && typeof vb === "number"
          ? va - vb
          : String(va).localeCompare(String(vb));
      return sortDir === "asc" ? cmp : -cmp;
    });
    return sorted.slice(0, 500);
  });

  function sortBy(k: string) {
    if (sortKey === k) sortDir = sortDir === "asc" ? "desc" : "asc";
    else {
      sortKey = k;
      sortDir = "desc";
    }
  }

  // Claim-level corroboration badge (fact-checking): how many INDEPENDENT
  // sources confirm the best-corroborated single claim. 1 = weak, 2 = moderate,
  // 3+ = strong. Syndicated/near-identical copies count once.
  const CORRO_STYLE: Record<string, string> = {
    strong: "bg-emerald-900/40 text-emerald-300 border-emerald-700/50",
    moderate: "bg-amber-900/40 text-amber-300 border-amber-700/50",
    weak: "bg-slate-800 text-slate-400 border-slate-700",
  };
  function corroTitle(r: any): string {
    const n = r.corroborationSources ?? 0;
    if (!n) return "No corroborated claim";
    const head = `${n} independent source${n === 1 ? "" : "s"} confirm the best claim`
      + (r.corroboratedClaims ? ` · ${r.corroboratedClaims} corroborated claim(s)` : "");
    return r.corroboratedClaim ? `${head}:\n${r.corroboratedClaim}` : head;
  }
</script>

<div class="flex flex-wrap items-center gap-3 border-b border-slate-800 bg-slate-900/60 px-5 py-2 text-xs flex-shrink-0">
  <div class="flex items-center gap-1">
    <span class="text-slate-500 mr-1">View</span>
    <button
      class="chip {view === 'entities' ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      onclick={() => (view = "entities")}>Actors</button
    >
    <button
      class="chip {view === 'events' ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      onclick={() => (view = "events")}>Events</button
    >
    <button
      class="chip {view === 'relationships' ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      onclick={() => (view = "relationships")}>Relationships</button
    >
    <button
      class="chip {view === 'evidence' ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-1"
      onclick={() => (view = "evidence")}>Evidence</button
    >
  </div>
  <span class="text-slate-700">·</span>
  <input
    class="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-200 placeholder-slate-500 w-64"
    placeholder="Search…"
    bind:value={q}
  />
  <div class="ml-auto text-slate-400 mono">
    {rows.length}{#if rows.length === 500}+{/if} rows
  </div>
</div>

<div class="flex-1 overflow-auto scrollbar p-4">
  <table class="min-w-full text-sm border-separate border-spacing-0">
    <thead class="sticky top-0 bg-slate-900 z-10">
      {#if view === "entities"}
        <tr>
          <th class="th sortable" onclick={() => sortBy("id")}>Actor</th>
          <th class="th sortable" onclick={() => sortBy("type")}>Type</th>
          <th class="th">Threads</th>
          <th class="th sortable" onclick={() => sortBy("evidenceCount")}>Articles</th>
          <th class="th sortable" onclick={() => sortBy("corroborationSources")}>Corroboration</th>
          <th class="th sortable" onclick={() => sortBy("isBridge")}>Bridge</th>
          <th class="th sortable" onclick={() => sortBy("score")}>Score</th>
        </tr>
      {:else if view === "events"}
        <tr>
          <th class="th sortable" onclick={() => sortBy("id")}>Event</th>
          <th class="th sortable" onclick={() => sortBy("date")}>Date</th>
          <th class="th">Type</th>
          <th class="th">Location</th>
          <th class="th">Threads</th>
        </tr>
      {:else if view === "evidence"}
        <tr>
          <th class="th sortable" onclick={() => sortBy("entity")}>Entity</th>
          <th class="th sortable" onclick={() => sortBy("supports")}>Polarity</th>
          <th class="th sortable" onclick={() => sortBy("corroborationSources")}>Corroboration</th>
          <th class="th">Reasoning &amp; quotes</th>
          <th class="th sortable" onclick={() => sortBy("strength")}>Str</th>
          <th class="th sortable" onclick={() => sortBy("confidence")}>Conf</th>
          <th class="th">Source</th>
        </tr>
      {:else}
        <tr>
          <th class="th sortable" onclick={() => sortBy("source")}>From</th>
          <th class="th">→</th>
          <th class="th sortable" onclick={() => sortBy("target")}>To</th>
          <th class="th sortable" onclick={() => sortBy("rtype")}>Relation</th>
          <th class="th">Context</th>
          <th class="th">Source</th>
        </tr>
      {/if}
    </thead>
    <tbody>
      {#if view === "entities"}
        {#each rows as r}
          <tr class="hover:bg-slate-800/40 border-b border-slate-800/40">
            <td class="td text-slate-200">{r.id}</td>
            <td class="td">{r.type === "event" ? "Event" : "Actor"}</td>
            <td class="td">
              {#each r.runs as rr}
                <span
                  class="inline-block w-2 h-2 rounded-full mr-1"
                  style="background: {colours[rr] || '#64748b'}"
                ></span>
              {/each}
            </td>
            <td class="td mono text-slate-300">{r.evidenceCount}</td>
            <td class="td">
              <span
                class="inline-block rounded border px-1.5 py-0.5 text-xs capitalize {CORRO_STYLE[r.corroboration] ?? CORRO_STYLE.weak}"
                title={corroTitle(r)}
              >{r.corroboration ?? "weak"}{#if r.corroborationSources}<span class="mono ml-1 opacity-70">{r.corroborationSources}</span>{/if}</span>
            </td>
            <td class="td">{#if r.isBridge}<span class="text-emerald-400">●</span>{/if}</td>
            <td class="td mono text-slate-400">{r.score.toFixed(2)}</td>
          </tr>
        {/each}
      {:else if view === "events"}
        {#each rows as r}
          <tr class="hover:bg-slate-800/40 border-b border-slate-800/40">
            <td class="td text-slate-200">{r.id}</td>
            <td class="td">{r.data?.date || ""}</td>
            <td class="td">{r.data?.event_type || ""}</td>
            <td class="td">{r.data?.location || ""}</td>
            <td class="td">
              {#each r.runs as rr}
                <span
                  class="inline-block w-2 h-2 rounded-full mr-1"
                  style="background: {colours[rr] || '#64748b'}"
                ></span>
              {/each}
            </td>
          </tr>
        {/each}
      {:else if view === "evidence"}
        {#each rows as r}
          <tr class="hover:bg-slate-800/40 border-b border-slate-800/40">
            <td class="td text-slate-200 align-top">{r.entity}</td>
            <td class="td align-top">
              {#if r.supports}
                <span class="text-emerald-400 text-xs">supports</span>
              {:else}
                <span class="text-red-400 text-xs">contradicts</span>
              {/if}
            </td>
            <td class="td align-top">
              <span
                class="inline-block rounded border px-1.5 py-0.5 text-xs capitalize {CORRO_STYLE[r.corroboration] ?? CORRO_STYLE.weak}"
                title="{r.corroborationSources ?? 1} independent source(s) confirm this claim"
              >{r.corroboration ?? "weak"}{#if r.corroborationSources}<span class="mono ml-1 opacity-70">{r.corroborationSources}</span>{/if}</span>
            </td>
            <td class="td text-slate-300 text-xs align-top max-w-xl">
              <div>{r.reasoning}</div>
              {#if r.quotes?.length}
                <ul class="mt-1 space-y-0.5">
                  {#each r.quotes as qt}
                    <li class="text-slate-500 border-l-2 border-slate-700 pl-2">"{qt}"</li>
                  {/each}
                </ul>
              {/if}
            </td>
            <td class="td mono text-slate-400 align-top">{r.strength?.toFixed?.(2) ?? r.strength}</td>
            <td class="td mono text-slate-400 align-top">{r.confidence?.toFixed?.(2) ?? r.confidence}</td>
            <td class="td align-top">
              {#if r.source?.startsWith?.("http")}
                <a class="text-emerald-400 hover:underline mono text-xs" href={r.source} target="_blank"
                  >{publisherOf(r.source)}</a
                >
              {:else if r.source}
                <span class="text-slate-500 text-xs">{r.source}</span>
              {/if}
            </td>
          </tr>
        {/each}
      {:else}
        {#each rows as r}
          <tr class="hover:bg-slate-800/40 border-b border-slate-800/40">
            <td class="td text-slate-200">{r.source}</td>
            <td class="td text-slate-500 mono">→</td>
            <td class="td text-slate-200">{r.target}</td>
            <td class="td text-slate-300">{r.rtype || r.type}</td>
            <td class="td text-slate-400 text-xs">{(r.context || "").slice(0, 140)}</td>
            <td class="td">
              {#if r.url}
                <a class="text-emerald-400 hover:underline mono text-xs" href={r.url} target="_blank"
                  >{publisherOf(r.url)}</a
                >
              {/if}
            </td>
          </tr>
        {/each}
      {/if}
    </tbody>
  </table>
</div>

<style>
  .th {
    text-align: left;
    padding: 0.5rem 0.75rem;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #64748b;
    border-bottom: 1px solid #1e293b;
  }
  .sortable {
    cursor: pointer;
  }
  .td {
    padding: 0.375rem 0.75rem;
    vertical-align: top;
  }
</style>
