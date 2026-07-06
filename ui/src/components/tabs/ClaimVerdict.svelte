<script lang="ts">
  import { api } from "../../lib/api";
  import type { ClaimVerdict } from "../../lib/api";
  import VerdictPanel from "../VerdictPanel.svelte";

  let { id }: { id: string } = $props();

  let claim = $state("");
  let result = $state<ClaimVerdict | null>(null);
  let loading = $state(false);
  let err = $state("");
  let needsClaim = $state(false);

  async function run(c?: string) {
    loading = true;
    err = "";
    try {
      const res = await api.claimVerdict(id, c);
      result = res;
      if (res.claim && !claim) claim = res.claim;
      needsClaim = false;
    } catch (e: any) {
      const m = (e?.message || "").toLowerCase();
      if (m.includes("no claim") || m.includes("claim")) needsClaim = true;
      else err = e?.message || "Verdict failed";
    } finally {
      loading = false;
    }
  }

  // Auto-run using the investigation's stored claim (claim-mode investigations).
  run();
</script>

<div class="flex-1 overflow-y-auto scrollbar">
  <div class="max-w-4xl mx-auto p-6">
    <h2 class="text-slate-200 font-semibold mb-1">Claim verdict</h2>
    <p class="text-sm text-slate-500 mb-4">
      Stance of this investigation's evidence toward the claim, as an ICD-203 confidence
      verdict — computed over the graph's collected evidence (not a fresh search).
    </p>

    {#if needsClaim || (!result && !loading)}
      <div class="mb-4">
        <textarea
          bind:value={claim}
          rows="2"
          placeholder="Enter a claim to assess against this investigation's evidence"
          class="w-full bg-slate-900 border border-slate-700 rounded-lg p-3 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-slate-500"
        ></textarea>
        <button
          onclick={() => run(claim.trim())}
          disabled={loading || !claim.trim()}
          class="mt-2 px-4 py-2 rounded-lg text-sm font-medium bg-sky-700 hover:bg-sky-600 text-white disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {loading ? "Assessing…" : "Compute verdict"}
        </button>
      </div>
    {/if}

    {#if err}
      <div class="text-red-400 text-sm mb-3">{err}</div>
    {/if}
    {#if loading}
      <div class="text-slate-500 text-sm mb-3">Classifying the investigation's evidence…</div>
    {/if}
    {#if result}
      <VerdictPanel {result} {claim} />
    {/if}
  </div>
</div>
