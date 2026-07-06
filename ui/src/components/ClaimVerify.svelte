<script lang="ts">
  import { api } from "../lib/api";
  import type { ClaimVerdict } from "../lib/api";
  import VerdictPanel from "./VerdictPanel.svelte";
  import AdvancedSettings from "./AdvancedSettings.svelte";
  import { navigate, investigationUrl } from "../lib/router.svelte";

  let claim = $state("");
  let entitiesRaw = $state("");
  let result = $state<ClaimVerdict | null>(null);
  let loading = $state(false);
  let err = $state("");
  let launching = $state(false);

  // Depth knobs for the full-investigation launch. Same panel and defaults as
  // the New Investigation wizard — but note a claim fans out into ~6
  // support/refute threads, so each knob is multiplied by the thread count.
  let showAdvanced = $state(false);
  // Adversarial fan-out: N support + N refute threads. 0 = one neutral thread
  // on the assertion (still claim mode — the verdict tab auto-runs).
  let adversarialPairs = $state(3);
  let period = $state("1y");
  let stage1Articles = $state(50);
  let stage2ArticlesPerEntity = $state(20);
  let topNEntities = $state(8);
  let enhancedRetrieval = $state(false);
  let retrievalDepth = $state(2);
  let retrievalExpansions = $state(4);

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

  // Launch a full, graph-depth investigation of this claim (adversarially seeded).
  async function launchInvestigation() {
    if (!claim.trim() || launching) return;
    launching = true;
    err = "";
    try {
      const { id } = await api.createInvestigation({
        claim: claim.trim(),
        period,
        adversarialPairs,
        advanced: {
          stage1Articles, stage2ArticlesPerEntity, topNEntities,
          enhancedRetrieval, retrievalDepth, retrievalExpansions,
        },
      });
      navigate(investigationUrl(id, "overview"));
    } catch (e: any) {
      err = e?.message || "Could not start investigation";
    } finally {
      launching = false;
    }
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
      <div class="mt-6">
        <VerdictPanel {result} {claim} />
        <div class="mt-5 pt-4 border-t border-slate-800">
          <button
            onclick={launchInvestigation}
            disabled={launching}
            class="px-4 py-2 rounded-lg text-sm font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 disabled:opacity-40"
          >
            {launching ? "Starting…" : "Run a full investigation on this claim →"}
          </button>
          <span class="text-xs text-slate-600 ml-2">builds a graph + deep verdict (~20 min at defaults)</span>
          <button
            class="text-xs text-slate-400 hover:text-slate-200 ml-3"
            onclick={() => (showAdvanced = !showAdvanced)}
          >
            {showAdvanced ? "▾" : "▸"} Advanced
          </button>
          {#if showAdvanced}
            <p class="text-[11px] text-slate-500 mt-3">
              The claim fans out into support/refute threads, so every knob below is
              multiplied by the thread count — reduce the fan-out or the knobs for a
              faster run (each thread ≈ 30–40 min at default depth).
            </p>
            <div class="mt-3 rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm">
              <label class="flex flex-col gap-1 max-w-xs">
                <span class="text-xs text-slate-500">Adversarial fan-out</span>
                <select
                  class="bg-slate-800 border border-slate-700 rounded px-2 py-1"
                  bind:value={adversarialPairs}
                >
                  <option value={3}>3 + 3 — thorough (6 threads, default)</option>
                  <option value={1}>1 + 1 — fast (2 threads)</option>
                  <option value={0}>0 — single neutral thread</option>
                </select>
              </label>
              <p class="text-[11px] text-slate-500 mt-1.5">
                N support + N refute search threads. Zero runs one thread on the
                assertion itself — quickest, but the graph loses the explicit
                for/against corpus split. The claim-verdict tab works in all cases.
              </p>
            </div>
            <AdvancedSettings
              bind:period
              bind:stage1Articles
              bind:stage2ArticlesPerEntity
              bind:topNEntities
              bind:enhancedRetrieval
              bind:retrievalDepth
              bind:retrievalExpansions
            />
          {/if}
        </div>
      </div>
    {/if}
  </div>
</div>
