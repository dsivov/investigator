<script lang="ts">
  import { api } from "../lib/api";
  import type { KbStats, KbResult } from "../lib/types";

  let stats = $state<KbStats | null>(null);
  let query = $state("");
  let mode = $state<"local" | "global" | "hybrid" | "mix">("hybrid");
  let synthesize = $state(true);
  let result = $state<KbResult | null>(null);
  let answerHtml = $state("");
  let loading = $state(false);
  let err = $state("");

  api.kbStats().then((s) => (stats = s)).catch(() => {});

  async function run() {
    if (!query.trim() || loading) return;
    loading = true;
    err = "";
    result = null;
    answerHtml = "";
    try {
      const [res, { marked }] = await Promise.all([
        api.kbQuery(query.trim(), mode, synthesize),
        import("marked"),
      ]);
      result = res;
      if (res.answer) {
        marked.setOptions({ headerIds: false, mangle: false } as any);
        answerHtml = marked.parse(res.answer) as string;
      }
    } catch (e: any) {
      err = e?.message || "Query failed";
    } finally {
      loading = false;
    }
  }
</script>

<div class="flex-1 overflow-y-auto scrollbar">
  <div class="max-w-4xl mx-auto p-8">
    <div class="flex items-baseline gap-3 mb-1">
      <h1 class="text-lg font-semibold text-slate-200">Knowledge base</h1>
      {#if stats?.available}
        <span class="text-xs text-slate-500 mono">
          {stats.entities} entities · {stats.edges} relationships across all investigations
        </span>
      {/if}
    </div>
    <p class="text-sm text-slate-500 mb-5">
      Ask about anything seen across every investigation. Answers and entities are drawn from the
      cumulative cross-investigation graph.
    </p>

    {#if stats && !stats.available}
      <div class="rounded-lg border border-amber-700/40 bg-amber-900/15 px-4 py-3 text-sm text-amber-200">
        No cumulative knowledge base yet. Run investigations with the analytic engine enabled
        (<span class="mono">--analytic_engine_enabled</span>) and they'll accumulate here.
      </div>
    {:else}
      <div class="flex flex-wrap items-center gap-2 mb-4">
        <input
          class="flex-1 min-w-[18rem] bg-slate-800 border border-slate-700 rounded px-3 py-2 text-slate-200 placeholder-slate-500"
          placeholder="e.g. How is Netanyahu connected to media owners?"
          bind:value={query}
          onkeydown={(e) => e.key === "Enter" && run()}
        />
        <select class="bg-slate-800 border border-slate-700 rounded px-2 py-2 text-slate-300 text-sm"
          bind:value={mode}
          title="local = entity-anchored · global = theme-anchored · hybrid = both (recommended)">
          <option value="hybrid">hybrid</option>
          <option value="local">local</option>
          <option value="global">global</option>
          <option value="mix">mix</option>
        </select>
        <label class="flex items-center gap-1 text-xs text-slate-400">
          <input type="checkbox" bind:checked={synthesize} /> synthesize answer
        </label>
        <button
          class="rounded border border-emerald-700 bg-emerald-900/40 px-4 py-2 text-sm text-emerald-200 hover:bg-emerald-900/70 disabled:opacity-50"
          disabled={loading || !query.trim()}
          onclick={run}>{loading ? "Searching…" : "Ask"}</button>
      </div>

      {#if err}<div class="text-red-400 text-sm mb-4">{err}</div>{/if}

      {#if result}
        {#if answerHtml}
          <section class="mb-6 rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <div class="text-xs uppercase tracking-wider text-slate-500 mb-2">Answer</div>
            <div class="kb-answer text-sm text-slate-300">{@html answerHtml}</div>
          </section>
        {/if}

        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <section class="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <div class="text-xs uppercase tracking-wider text-slate-500 mb-2">
              Entities ({result.entities.length})
            </div>
            <ul class="space-y-1.5 text-sm">
              {#each result.entities.slice(0, 40) as e}
                <li>
                  <span class="text-slate-200 font-medium">{e.name}</span>
                  {#if e.type}<span class="text-slate-600 text-xs mono ml-1">{e.type}</span>{/if}
                </li>
              {/each}
            </ul>
          </section>
          <section class="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <div class="text-xs uppercase tracking-wider text-slate-500 mb-2">
              Relationships ({result.relationships.length})
            </div>
            <ul class="space-y-1.5 text-sm">
              {#each result.relationships.slice(0, 40) as r}
                <li class="text-slate-300">
                  <span class="text-slate-200">{r.src}</span>
                  <span class="text-slate-600">→</span>
                  <span class="text-slate-200">{r.dst}</span>
                </li>
              {/each}
            </ul>
          </section>
        </div>
      {/if}
    {/if}
  </div>
</div>

<style>
  .kb-answer :global(p) { margin: 0.4rem 0; line-height: 1.5; }
  .kb-answer :global(ul) { list-style: disc; padding-left: 1.1rem; margin: 0.4rem 0; }
  .kb-answer :global(li) { margin: 0.2rem 0; }
  .kb-answer :global(strong) { color: #e2e8f0; }
</style>
