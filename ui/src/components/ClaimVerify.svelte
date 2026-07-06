<script lang="ts">
  import { api } from "../lib/api";
  import type { ClaimVerdict } from "../lib/api";
  import VerdictPanel from "./VerdictPanel.svelte";
  import { navigate, investigationUrl } from "../lib/router.svelte";

  let claim = $state("");
  let entitiesRaw = $state("");
  let result = $state<ClaimVerdict | null>(null);
  let loading = $state(false);
  let err = $state("");
  let launching = $state(false);

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
      const { id } = await api.createInvestigation({ claim: claim.trim(), period: "1y" });
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
          <span class="text-xs text-slate-600 ml-2">builds a graph + deep verdict (~20 min)</span>
        </div>
      </div>
    {/if}
  </div>
</div>
