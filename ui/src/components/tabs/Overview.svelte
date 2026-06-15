<script lang="ts">
  import type { InvestigationFull } from "../../lib/types";
  import { api } from "../../lib/api";
  import { bridgeConfidence, formatRunLabel } from "../../lib/helpers";
  import { confidencePillClass, threadColourMap, POLYGON_PALETTE } from "../../lib/colors";
  import { navigate, investigationUrl } from "../../lib/router.svelte";
  import type { GraphPayload, TmfgPayload } from "../../lib/types";

  let { inv }: { inv: InvestigationFull } = $props();
  let graph = $state<GraphPayload | null>(null);
  let tmfg = $state<TmfgPayload | null>(null);

  $effect(() => {
    api.getGraph(inv.id).then((g) => (graph = g));
    api.getTmfg(inv.id).then((t) => (tmfg = t));
  });

  const sum = $derived(inv.summary);
  const totalThreads = $derived((inv.threads ?? []).length);
  const colours = $derived(threadColourMap((inv.threads ?? []).map((t) => t.name)));

  // A single-query investigation has no cross-thread structure (no bridges, no
  // cross-themes), so the bridge/cross-theme panels would be empty. Show the
  // subject's own network instead: top actors by score + top themes by weight.
  const singleThread = $derived(totalThreads <= 1);
  const topActors = $derived.by(() => {
    if (!graph) return [];
    // filter() returns a fresh array, so sorting it doesn't mutate $state.
    return graph.nodes
      .filter((n) => n.type === "entity")
      .sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
      .slice(0, 8);
  });

  // Per-thread node counts derived from graph payload
  const perThread = $derived.by(() => {
    if (!graph) return {} as Record<string, number>;
    const out: Record<string, number> = {};
    for (const t of (inv.threads ?? [])) out[t.name] = 0;
    for (const n of graph.nodes) for (const r of n.runs) if (r in out) out[r]++;
    return out;
  });
</script>

<div class="flex-1 overflow-y-auto scrollbar p-6">
  {#if sum?.asymmetric_corpus}
    <div class="mb-5 rounded-lg border border-amber-700/60 bg-amber-900/15 p-4 flex items-start gap-3 text-sm">
      <span class="text-amber-400 text-xl leading-none">⚠</span>
      <div>
        <div class="font-semibold text-amber-200">Asymmetric corpus detected</div>
        <div class="mt-1 text-amber-100/80">
          Thread(s)
          {#each sum.sparse_threads ?? [] as s, i}
            <code class="mono text-amber-200">{s}</code>{#if i < (sum.sparse_threads ?? []).length - 1},
            {/if}
          {/each}
          returned five or fewer extracted entities. Hypotheses depending on the sparse thread cannot
          be tested from this corpus alone.
        </div>
      </div>
    </div>
  {/if}

  <div class="grid grid-cols-12 gap-5">
    <!-- Bridges (multi-thread) / Top actors (single-thread) -->
    <div class="col-span-7 rounded-xl border border-slate-800 bg-slate-900 p-5">
      <div class="flex items-center justify-between mb-3">
        <div class="text-slate-200 font-semibold">{singleThread ? "Top actors" : "Cross-thread bridges"}</div>
        <div class="text-xs text-slate-500">
          {#if singleThread}
            ranked by relevance (score = relevance × confidence)
          {:else}
            {sum.bridges ?? "—"} bridge(s) · {sum.cross_event_themes ?? "—"} cross-thread themes
          {/if}
        </div>
      </div>
      {#if !graph}
        <div class="text-slate-500 italic text-sm">Loading…</div>
      {:else if singleThread}
        {#if topActors.length === 0}
          <div class="text-slate-500 italic text-sm">No actors extracted.</div>
        {:else}
          <div class="space-y-2 text-sm">
            {#each topActors as a}
              <div class="flex items-center justify-between border-b border-slate-800/60 py-1 last:border-0">
                <span class="text-slate-100 font-semibold">{a.id}</span>
                <span class="text-xs text-slate-500 mono">score {(a.score ?? 0).toFixed(2)}</span>
              </div>
            {/each}
          </div>
        {/if}
      {:else if graph.bridges.length === 0}
        <div class="text-slate-500 italic text-sm">No actor was attested across multiple threads.</div>
      {:else}
        <div class="space-y-2 text-sm">
          {#each graph.bridges.slice(0, 8) as b}
            {@const conf = bridgeConfidence(b, totalThreads)}
            {@const scope = b.runs.length >= totalThreads ? "all threads" : `${b.runs.length} of ${totalThreads}`}
            <div class="flex items-center justify-between border-b border-slate-800/60 py-1 last:border-0">
              <div class="flex items-center gap-2">
                <span class="text-slate-100 font-semibold">{b.id}</span>
                <span class="flex gap-0.5">
                  {#each b.runs as r}
                    <span
                      class="inline-block w-2 h-2 rounded-full"
                      title={r}
                      style="background: {colours[r] || '#64748b'}"
                    ></span>
                  {/each}
                </span>
                <span class="text-xs text-slate-500">{scope}</span>
              </div>
              <span class="text-xs px-2 py-0.5 rounded-md {confidencePillClass(conf)}">{conf}</span>
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <!-- Coverage -->
    <div class="col-span-5 rounded-xl border border-slate-800 bg-slate-900 p-5">
      <div class="text-slate-200 font-semibold mb-3">Coverage</div>
      <div class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
        <div class="text-slate-400">Articles fetched</div>
        <div class="mono text-slate-200 text-right">{sum?.fetched ?? "—"}</div>
        <div class="text-slate-400">  ↳ full body</div>
        <div class="mono text-slate-300 text-right">{sum?.extracted_full_body ?? "—"}</div>
        <div class="text-slate-400">  ↳ headline only</div>
        <div class="mono text-slate-300 text-right">{sum?.extracted_headline_only ?? "—"}</div>
        <div class="text-slate-400">Body-fetch failure</div>
        <div class="mono text-slate-200 text-right">
          {sum?.fetched
            ? ((sum.extracted_headline_only ?? 0) / sum.fetched * 100).toFixed(0) + "%"
            : "—"}
        </div>
      </div>
      <div class="mt-4 pt-3 border-t border-slate-800">
        <div class="text-xs text-slate-500 mb-2">Per thread (nodes extracted)</div>
        {#if graph}
          {@const max = Math.max(1, ...Object.values(perThread))}
          <div class="space-y-1.5 text-sm">
            {#each Object.entries(perThread) as [name, count]}
              <div class="flex items-center gap-2">
                <span class="text-xs w-44 truncate" title={name} style="color: {colours[name] || '#64748b'}">
                  {formatRunLabel(name)}
                </span>
                <div class="flex-1 bg-slate-800 rounded h-2">
                  <div
                    class="h-2 rounded"
                    style="background: {colours[name] || '#64748b'}; width: {(count / max) * 100}%"
                  ></div>
                </div>
                <span class="mono text-xs text-slate-400 w-8 text-right">{count}</span>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    </div>

    <!-- Themes shortlist -->
    <div class="col-span-12 rounded-xl border border-slate-800 bg-slate-900 p-5">
      <div class="flex items-center justify-between mb-3">
        <div class="text-slate-200 font-semibold">{singleThread ? "Top themes" : "Top cross-thread themes"}</div>
        <button
          class="text-xs text-emerald-400 hover:underline"
          onclick={() => navigate(investigationUrl(inv.id, "tmfg"))}
        >
          all →
        </button>
      </div>
      {#if !tmfg}
        <div class="text-slate-500 italic text-sm">Loading…</div>
      {:else}
        {@const cross = singleThread
          ? [...tmfg.themes].sort((a, b) => b.weight - a.weight).slice(0, 6)
          : tmfg.themes.filter((t) => t.isCross).slice(0, 6)}
        {#if cross.length === 0}
          <div class="text-slate-500 italic text-sm">{singleThread ? "No themes." : "No cross-thread themes."}</div>
        {:else}
          <ol class="space-y-1.5 text-sm">
            {#each cross as t, i}
              <li class="flex items-baseline gap-2">
                <span
                  class="inline-block w-2.5 h-2.5 rounded-sm flex-shrink-0"
                  style="background: {POLYGON_PALETTE[i % POLYGON_PALETTE.length]}; opacity: .5; border: 1px solid {POLYGON_PALETTE[i % POLYGON_PALETTE.length]}"
                ></span>
                <span class="text-slate-300 truncate flex-1">{t.members.join(" · ")}</span>
                <span class="text-xs text-slate-500 mono">w {t.weight.toFixed(1)}</span>
              </li>
            {/each}
          </ol>
        {/if}
      {/if}
    </div>
  </div>
</div>
