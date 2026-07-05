<script lang="ts">
  import { api } from "../lib/api";
  import type { ClaimVerdict } from "../lib/api";

  let claim = $state("");
  let entitiesRaw = $state("");
  let result = $state<ClaimVerdict | null>(null);
  let loading = $state(false);
  let err = $state("");

  async function run() {
    if (!claim.trim() || loading) return;
    loading = true;
    err = "";
    result = null;
    try {
      const entities = entitiesRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      result = await api.claimVerify(
        claim.trim(),
        entities.length ? entities : undefined,
      );
    } catch (e: any) {
      err = e?.message || "Verification failed";
    } finally {
      loading = false;
    }
  }

  // Verdict colour band from the source-tempered signal in [-1, 1].
  function verdictClass(t: number): string {
    if (t >= 0.55) return "bg-emerald-900/40 text-emerald-200 border-emerald-600/50";
    if (t >= 0.25) return "bg-emerald-900/25 text-emerald-300 border-emerald-700/40";
    if (t > -0.25) return "bg-amber-900/30 text-amber-200 border-amber-700/40";
    if (t > -0.55) return "bg-red-900/25 text-red-300 border-red-700/40";
    return "bg-red-900/40 text-red-200 border-red-600/50";
  }
  function confClass(c: number): string {
    if (c >= 0.66) return "text-emerald-400";
    if (c >= 0.4) return "text-amber-400";
    return "text-slate-500";
  }
</script>

<div class="flex-1 overflow-y-auto scrollbar">
  <div class="max-w-4xl mx-auto p-8">
    <h1 class="text-lg font-semibold text-slate-200 mb-1">Verify a claim</h1>
    <p class="text-sm text-slate-500 mb-5">
      Enter a claim. The system plans both
      <span class="text-emerald-400">supporting</span> and
      <span class="text-red-400">refuting</span> searches, reads the evidence, and returns
      a confidence verdict — with the evidence for and against. Takes ~30&nbsp;seconds.
    </p>

    <textarea
      bind:value={claim}
      rows="2"
      placeholder="e.g. Company X bribed officials in Country Y to win contract Z"
      class="w-full bg-slate-900 border border-slate-700 rounded-lg p-3 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-500"
    ></textarea>
    <input
      bind:value={entitiesRaw}
      placeholder="Optional: related entities, comma-separated (seeds the search)"
      class="w-full mt-2 bg-slate-900 border border-slate-700 rounded-lg p-2.5 text-sm text-slate-300 placeholder-slate-600 focus:outline-none focus:border-slate-500"
    />
    <button
      onclick={run}
      disabled={loading || !claim.trim()}
      class="mt-3 px-4 py-2 rounded-lg text-sm font-medium bg-sky-700 hover:bg-sky-600 text-white disabled:opacity-40 disabled:cursor-not-allowed"
    >
      {loading ? "Searching for and against…" : "Verify claim"}
    </button>

    {#if err}
      <div class="text-red-400 text-sm mt-4">{err}</div>
    {/if}
    {#if loading}
      <div class="text-slate-500 text-sm mt-4">
        Planning adversarial searches, retrieving, and classifying evidence…
      </div>
    {/if}

    {#if result}
      <!-- Verdict -->
      <div class="mt-6 rounded-xl border p-5 {verdictClass(result.tempered_net)}">
        <div class="text-xs uppercase tracking-wider opacity-70 mb-1">Verdict</div>
        <div class="text-2xl font-bold">{result.verdict}</div>
        <div class="text-sm opacity-80 mt-0.5">the claim is supported</div>
        {#if result.assertion && result.assertion.trim().toLowerCase() !== claim.trim().toLowerCase()}
          <div class="text-xs opacity-70 mt-1 italic">Interpreted as: {result.assertion}</div>
        {/if}
        <div class="text-xs mono opacity-70 mt-2">
          {result.counts.supports} supporting · {result.counts.refutes} refuting ·
          {result.counts.neutral} neutral ·
          {result.counts.support_sources + result.counts.refute_sources} independent source(s) ·
          net {result.net}
        </div>
        {#if result.counts.support_sources + result.counts.refute_sources < 2}
          <div class="text-xs mt-2 opacity-80">
            ⚠ Thin evidence — verdict tempered toward the middle. Treat as a lead, not a conclusion.
          </div>
        {/if}
      </div>

      <!-- Adversarial transparency: the queries actually run -->
      <div class="mt-4 grid grid-cols-2 gap-3 text-xs">
        <div>
          <div class="text-emerald-400 mb-1 font-medium">Searched for support</div>
          {#each result.queries.support as q}
            <div class="mono text-slate-500">· {q}</div>
          {/each}
        </div>
        <div>
          <div class="text-red-400 mb-1 font-medium">Searched for refutation</div>
          {#each result.queries.refute as q}
            <div class="mono text-slate-500">· {q}</div>
          {/each}
        </div>
      </div>

      <!-- Evidence, for vs against -->
      <div class="mt-4 grid grid-cols-2 gap-4">
        {#each [{ label: "Supports", items: result.support, color: "text-emerald-400" }, { label: "Refutes", items: result.refute, color: "text-red-400" }] as col}
          <div>
            <div class="{col.color} text-sm font-medium mb-2">
              {col.label} ({col.items.length})
            </div>
            {#each col.items as e}
              <div class="mb-2 bg-slate-900 border border-slate-800 rounded-lg p-3">
                <div class="flex justify-between items-baseline text-xs">
                  <span class="text-slate-400">{e.source}</span>
                  <span class="mono {confClass(e.confidence)}">{e.confidence}</span>
                </div>
                <div class="text-sm text-slate-300 mt-1">{e.title}</div>
                {#if e.quote}
                  <div class="text-xs text-slate-500 italic mt-1">“{e.quote}”</div>
                {/if}
                {#if e.url}
                  <a href={e.url} target="_blank" rel="noopener"
                    class="text-xs text-sky-500 hover:underline">source ↗</a>
                {/if}
              </div>
            {:else}
              <div class="text-xs text-slate-600">none found</div>
            {/each}
          </div>
        {/each}
      </div>
    {/if}
  </div>
</div>
