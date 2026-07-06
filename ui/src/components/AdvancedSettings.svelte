<script lang="ts">
  // Shared advanced-settings panel: pipeline depth knobs + the enhanced-retrieval
  // opt-in. Used by the New Investigation wizard and the claim launcher so both
  // paths expose the same controls (and the same defaults as ui/server.py).
  let {
    period = $bindable("1y"),
    stage1Articles = $bindable(50),
    stage2ArticlesPerEntity = $bindable(20),
    topNEntities = $bindable(8),
    enhancedRetrieval = $bindable(false),
    retrievalDepth = $bindable(2),
    retrievalExpansions = $bindable(4),
  }: {
    period?: string;
    stage1Articles?: number;
    stage2ArticlesPerEntity?: number;
    topNEntities?: number;
    enhancedRetrieval?: boolean;
    retrievalDepth?: number;
    retrievalExpansions?: number;
  } = $props();
</script>

<div class="mt-3 grid grid-cols-2 gap-4 rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm">
  <label class="flex flex-col gap-1">
    <span class="text-xs text-slate-500">Period</span>
    <select class="bg-slate-800 border border-slate-700 rounded px-2 py-1" bind:value={period}>
      <option value="7d">7 days</option>
      <option value="30d">30 days</option>
      <option value="3m">3 months</option>
      <option value="6m">6 months</option>
      <option value="1y">1 year</option>
    </select>
  </label>
  <label class="flex flex-col gap-1">
    <span class="text-xs text-slate-500">Stage-1 articles</span>
    <input type="number" min="10" max="100" class="bg-slate-800 border border-slate-700 rounded px-2 py-1" bind:value={stage1Articles} />
  </label>
  <label class="flex flex-col gap-1">
    <span class="text-xs text-slate-500">Stage-2 articles / entity</span>
    <input type="number" min="5" max="50" class="bg-slate-800 border border-slate-700 rounded px-2 py-1" bind:value={stage2ArticlesPerEntity} />
  </label>
  <label class="flex flex-col gap-1">
    <span class="text-xs text-slate-500">Top-N entities</span>
    <input type="number" min="2" max="20" class="bg-slate-800 border border-slate-700 rounded px-2 py-1" bind:value={topNEntities} />
  </label>
</div>

<!-- Enhanced retrieval -->
<div class="mt-3 rounded-lg border border-slate-800 bg-slate-900 p-4">
  <label class="flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
    <input type="checkbox" class="accent-emerald-500" bind:checked={enhancedRetrieval} />
    ✦ Enhanced retrieval
  </label>
  <p class="text-[11px] text-slate-500 mt-1">
    Expands the query into several angles, retrieves titles broadly,
    reranks for relevance, and (depth&gt;1) deepens on the most relevant
    entities — widening recall before fetching article bodies.
  </p>
  {#if enhancedRetrieval}
    <div class="grid grid-cols-2 gap-4 mt-3 text-sm">
      <label class="flex flex-col gap-1">
        <span class="text-xs text-slate-500">Depth (entity-driven turns)</span>
        <input type="number" min="1" max="4" class="bg-slate-800 border border-slate-700 rounded px-2 py-1" bind:value={retrievalDepth} />
      </label>
      <label class="flex flex-col gap-1">
        <span class="text-xs text-slate-500">Query expansions</span>
        <input type="number" min="1" max="8" class="bg-slate-800 border border-slate-700 rounded px-2 py-1" bind:value={retrievalExpansions} />
      </label>
    </div>
  {/if}
</div>
