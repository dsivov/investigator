<script lang="ts">
  import { api } from "../../lib/api";
  import type { SourcesPayload } from "../../lib/types";
  import { publisherOf } from "../../lib/helpers";

  let { id }: { id: string } = $props();
  let data = $state<SourcesPayload | null>(null);

  $effect(() => {
    api.getSources(id).then((d) => (data = d));
  });

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
