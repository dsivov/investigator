<script lang="ts">
  import { onDestroy } from "svelte";
  import { api } from "../../lib/api";
  import type { SourcesPayload, EnrichmentPayload } from "../../lib/types";
  import { publisherOf } from "../../lib/helpers";

  let { id }: { id: string } = $props();
  let data = $state<SourcesPayload | null>(null);

  // External-records enrichment (SEC EDGAR + OpenRegistry).
  let enr = $state<EnrichmentPayload | null>(null);
  let enriching = $state(false);
  let enrErr = $state("");
  let enrMsg = $state("");
  let poll: ReturnType<typeof setInterval> | null = null;

  $effect(() => {
    api.getSources(id).then((d) => (data = d));
    api.getEnrichment(id).then((e) => (enr = e)).catch(() => {});
  });
  onDestroy(() => poll && clearInterval(poll));

  async function refreshEnrichment() {
    try {
      enr = await api.getEnrichment(id);
      if (enr && !enr.running && poll) {
        clearInterval(poll);
        poll = null;
        enriching = false;
        enrMsg = enr.recordCount ? `${enr.recordCount} entity record(s) found.` : "No registry matches found.";
      }
    } catch {}
  }
  async function runEnrich() {
    enriching = true;
    enrErr = "";
    enrMsg = "";
    try {
      const s = await api.enrichInvestigation(id, 12);
      enrMsg = s.message || "Enrichment started…";
      if (!poll) poll = setInterval(refreshEnrichment, 2500);
    } catch (e: any) {
      enriching = false;
      enrErr = e?.message || "Enrichment failed";
    }
  }

  let concentration = $derived(data?.topConcentration ?? 0);
  let diversityLabel = $derived(
    concentration < 0.25
      ? "Diverse sourcing (top 3 < 25%)"
      : concentration < 0.5
      ? "Moderate concentration"
      : "High concentration (top 3 carry >50%)"
  );
</script>

{#if !data}
  <div class="p-6 text-slate-500 italic text-sm">Loading…</div>
{:else}
  <div class="border-b border-slate-800 bg-slate-900 p-5 flex items-center gap-6 text-sm flex-shrink-0">
    <div>
      <div class="text-2xl font-bold text-slate-100 mono">{data.publisherCount}</div>
      <div class="text-xs text-slate-500">Publishers</div>
    </div>
    <div>
      <div class="text-2xl font-bold text-slate-100 mono">
        {data.publishers.reduce((s, p) => s + p.count, 0)}
      </div>
      <div class="text-xs text-slate-500">Citations</div>
    </div>
    <div>
      <div class="text-2xl font-bold text-slate-100 mono">
        {(data.topConcentration * 100).toFixed(0)}%
      </div>
      <div class="text-xs text-slate-500">Top-3 share</div>
    </div>
    <div class="ml-auto text-xs text-slate-500">{diversityLabel}</div>
  </div>
  <div class="flex-1 overflow-y-auto scrollbar p-5">
    <!-- External records (post-run enrichment: SEC EDGAR + OpenRegistry) -->
    <section class="mb-6 rounded-lg border border-slate-800 bg-slate-900/50">
      <div class="flex items-center gap-3 px-4 py-3 border-b border-slate-800">
        <div class="min-w-0">
          <div class="text-slate-200 font-medium text-sm">External records</div>
          <div class="text-xs text-slate-500">Company registries — SEC EDGAR + OpenRegistry (beneficial owners, officers, filings)</div>
        </div>
        <div class="ml-auto flex items-center gap-3 text-xs">
          {#if enr?.recordCount}
            <span class="text-emerald-300">{enr.recordCount} entity record(s)</span>
          {/if}
          <button
            class="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-1.5 text-emerald-200 hover:bg-emerald-900/70 disabled:opacity-50"
            disabled={enriching || enr?.running}
            onclick={runEnrich}
          >{enriching || enr?.running ? "Enriching…" : (enr?.hasEnriched ? "Re-enrich" : "Enrich company entities")}</button>
        </div>
      </div>
      <div class="px-4 py-3 text-sm">
        {#if enrErr}<div class="text-red-400 text-xs">{enrErr}</div>{/if}
        {#if enrMsg}<div class="text-slate-400 text-xs mb-2">{enrMsg}</div>{/if}
        {#if enr?.items && enr.items.length}
          <div class="space-y-3">
            {#each enr.items as it}
              <div class="border-b border-slate-800/40 pb-2">
                <div class="text-slate-200 font-semibold">{it.id}</div>
                {#if it.enrichment.edgar}
                  {@const ed = it.enrichment.edgar}
                  <div class="text-xs text-slate-400 mt-0.5">
                    <span class="text-slate-500">SEC filer:</span>
                    <a class="text-emerald-400 hover:underline" target="_blank" href={ed._provenance?.url}>{ed.matched_name}</a>
                    ({ed.ticker}, CIK {ed.cik})
                    {#if ed.recent_filings?.length}· filings: {ed.recent_filings.slice(0,3).map((f) => `${f.form} (${f.date})`).join(", ")}{/if}
                  </div>
                {/if}
                {#if it.enrichment.openregistry}
                  {@const o = it.enrichment.openregistry}
                  <div class="text-xs text-slate-400 mt-0.5">
                    <span class="text-slate-500">Registry ({o.jurisdiction}):</span>
                    <a class="text-emerald-400 hover:underline" target="_blank" href={o._provenance?.url}>{o.matched_name}</a>
                    [{o.company_id}] · status {o.status}
                  </div>
                {/if}
              </div>
            {/each}
          </div>
        {:else if !enriching && !enr?.running}
          <div class="text-xs text-slate-500">
            No external records yet. Click <span class="text-slate-300">Enrich</span> to look up the top company entities in SEC EDGAR and OpenRegistry.
            {#if enr && !enr.hasEnriched}(OpenRegistry needs to be connected in Settings.){/if}
          </div>
        {/if}
      </div>
    </section>

    <table class="min-w-full text-sm">
      <thead class="text-slate-500 text-xs uppercase tracking-wider">
        <tr>
          <th class="text-left py-1">Publisher</th>
          <th class="text-right py-1">Citations</th>
          <th class="text-left py-1 pl-6">Backs</th>
        </tr>
      </thead>
      <tbody>
        {#each data.publishers as p}
          <tr class="hover:bg-slate-800/40 border-b border-slate-800/40">
            <td class="py-2 align-top">
              <span class="text-slate-200 font-semibold">{p.publisher}</span>
            </td>
            <td class="py-2 align-top text-right mono text-slate-300">{p.count}</td>
            <td class="py-2 align-top pl-6">
              {#each p.urls.slice(0, 5) as u}
                <div class="mt-0.5">
                  <a
                    class="text-emerald-400 hover:underline text-xs mono"
                    target="_blank"
                    href={u.url}>{publisherOf(u.url)}</a
                  >
                  <span class="text-slate-500 text-xs">backs {u.backsEntity}</span>
                </div>
              {/each}
              {#if p.urls.length > 5}
                <div class="text-xs text-slate-500 mt-0.5">…and {p.urls.length - 5} more</div>
              {/if}
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
