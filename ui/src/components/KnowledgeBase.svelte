<script lang="ts">
  import { api } from "../lib/api";
  import type { KbStats, KbResult, KbConflicts } from "../lib/types";

  let stats = $state<KbStats | null>(null);
  let conflicts = $state<KbConflicts | null>(null);
  let showConflicts = $state(false);
  let query = $state("");
  let synthesize = $state(true);
  let asOf = $state("");
  let result = $state<KbResult | null>(null);
  let answerHtml = $state("");
  let loading = $state(false);
  let err = $state("");
  let expanded = $state<Set<string>>(new Set());

  function toggle(name: string) {
    const s = new Set(expanded);
    s.has(name) ? s.delete(name) : s.add(name);
    expanded = s;
  }
  function publisherOf(url: string): string {
    try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return url; }
  }
  // Confidence chip colour from prob (distance-to-subject belief).
  function probClass(p: number | null | undefined): string {
    if (p == null) return "bg-slate-800 text-slate-500 border-slate-700";
    if (p >= 0.66) return "bg-emerald-900/40 text-emerald-300 border-emerald-700/50";
    if (p >= 0.4) return "bg-amber-900/40 text-amber-300 border-amber-700/50";
    return "bg-slate-800 text-slate-400 border-slate-700";
  }

  api.kbStats().then((s) => (stats = s)).catch(() => {});
  api.kbConflicts().then((c) => (conflicts = c)).catch(() => {});

  const conflictCount = $derived(
    (conflicts?.events.length || 0) + (conflicts?.orderings.length || 0),
  );

  async function run() {
    if (!query.trim() || loading) return;
    loading = true;
    err = "";
    result = null;
    answerHtml = "";
    try {
      const [res, { marked }] = await Promise.all([
        api.kbQuery(query.trim(), synthesize, undefined, asOf || undefined),
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

    {#if conflictCount > 0}
      <div class="mb-5 rounded-lg border border-amber-700/40 bg-amber-900/15">
        <button class="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-amber-200 text-left"
          onclick={() => (showConflicts = !showConflicts)}>
          <span class="text-amber-400">{showConflicts ? "▾" : "▸"}</span>
          ⚠ Timeline conflicts ({conflictCount})
          <span class="text-amber-200/60 text-xs font-normal ml-1">dates that disagree across sources/runs — possible errors or merged entities</span>
        </button>
        {#if showConflicts && conflicts}
          <div class="px-4 pb-3 space-y-3 text-xs">
            {#if conflicts.events.length}
              <div>
                <div class="text-amber-200/70 uppercase tracking-wider mb-1">Disputed event dates ({conflicts.events.length})</div>
                <ul class="space-y-1.5">
                  {#each conflicts.events.slice(0, 20) as c}
                    <li class="text-slate-300">
                      <span class="text-slate-200">{c.id}</span>
                      <span class="mono text-amber-300/90 ml-1">{c.min} … {c.max}</span>
                      <span class="text-slate-500">({c.daysApart}d)</span>
                      {#if c.dates.length}<span class="text-slate-600 mono"> · {c.dates.join(", ")}</span>{/if}
                    </li>
                  {/each}
                </ul>
              </div>
            {/if}
            {#if conflicts.orderings.length}
              <div>
                <div class="text-amber-200/70 uppercase tracking-wider mb-1">Contradictory orderings ({conflicts.orderings.length})</div>
                <ul class="space-y-1.5">
                  {#each conflicts.orderings.slice(0, 20) as o}
                    <li class="text-slate-300">
                      <span class="text-slate-200">{o.src}</span>
                      <span class="text-slate-500 mono">→follows→</span>
                      <span class="text-slate-200">{o.dst}</span>
                      <span class="mono text-amber-300/90 ml-1">but {o.srcDate} &gt; {o.dstDate}</span>
                      <span class="text-slate-500">({o.daysApart}d)</span>
                    </li>
                  {/each}
                </ul>
              </div>
            {/if}
          </div>
        {/if}
      </div>
    {/if}

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
        <label class="flex items-center gap-1 text-xs text-slate-400"
          title="Drop relationships not yet asserted by this date (observed time, else inferred active window). Leave blank for all.">
          <span>as of</span>
          <input type="date" bind:value={asOf}
            class="bg-slate-800 border border-slate-700 rounded px-1.5 py-1 text-slate-200 [color-scheme:dark]" />
          {#if asOf}
            <button class="text-slate-500 hover:text-slate-300" title="clear" onclick={() => (asOf = "")}>✕</button>
          {/if}
        </label>
        <label class="flex items-center gap-1 text-xs text-slate-400"
          title="Synthesised answer uses the relationship-anchored (global) lens; entities/relationships use the broad (hybrid) lens.">
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
            <ul class="space-y-1 text-sm">
              {#each result.entities.slice(0, 40) as e}
                {@const s = e.structured}
                <li class="border-b border-slate-800/40 last:border-0">
                  <button class="w-full flex items-center gap-2 py-1.5 text-left hover:bg-slate-800/30 rounded px-1"
                          onclick={() => toggle(e.name)}>
                    <span class="text-slate-500 text-xs w-3">{expanded.has(e.name) ? "▾" : "▸"}</span>
                    <span class="text-slate-200 font-medium">{e.name}</span>
                    {#if e.type}<span class="text-slate-600 text-xs mono">{e.type}</span>{/if}
                    <span class="ml-auto flex items-center gap-1.5">
                      {#if s && s.prob != null}
                        <span class="rounded border px-1.5 text-[11px] {probClass(s.prob)}" title="belief score (prob)">{s.prob.toFixed(2)}</span>
                      {/if}
                      {#if s && s.evidenceCount}
                        <span class="text-[11px] text-slate-500" title="evidence items">{s.evidenceCount} ev</span>
                      {/if}
                      {#if s && s.runs && s.runs.length > 1}
                        <span class="text-[11px] text-sky-400" title="appears in {s.runs.length} investigations">×{s.runs.length}</span>
                      {/if}
                    </span>
                  </button>
                  {#if expanded.has(e.name) && s}
                    <div class="pl-6 pb-3 pt-1 text-xs space-y-1.5 text-slate-400">
                      <div class="flex flex-wrap gap-x-4 gap-y-1">
                        {#if s.score != null}<span>score <span class="mono text-slate-300">{s.score.toFixed(2)}</span></span>{/if}
                        {#if s.posterior_prob != null}<span>posterior <span class="mono text-slate-300">{s.posterior_prob.toFixed(2)}</span></span>{/if}
                        {#if s.data?.position}<span>role: <span class="text-slate-300">{s.data.position}</span></span>{/if}
                        {#if s.data?.location}<span>loc: <span class="text-slate-300">{s.data.location}</span></span>{/if}
                        {#if s.firstSeen}<span>active <span class="mono text-slate-300">{s.firstSeen} → {s.lastSeen}</span></span>{/if}
                      </div>
                      {#if s.data?.email || s.data?.phone_number || s.data?.financial_restrictions || s.data?.address}
                        <div class="flex flex-wrap gap-x-4 gap-y-1">
                          {#if s.data?.email}<span>email: <span class="text-slate-300">{s.data.email}</span></span>{/if}
                          {#if s.data?.phone_number}<span>phone: <span class="text-slate-300">{s.data.phone_number}</span></span>{/if}
                          {#if s.data?.address}<span>addr: <span class="text-slate-300">{s.data.address}</span></span>{/if}
                          {#if s.data?.financial_restrictions}<span class="text-amber-300">⚑ {s.data.financial_restrictions}</span>{/if}
                        </div>
                      {/if}
                      {#if s.labels && s.labels.length}
                        <div>aliases: <span class="text-slate-300">{s.labels.slice(0, 6).join(", ")}</span></div>
                      {/if}
                      {#if s.runs && s.runs.length}
                        <div>investigations: <span class="text-sky-300">{s.runs.join(", ")}</span></div>
                      {/if}
                      {#if s.timeline && s.timeline.length}
                        <div class="space-y-0.5">
                          <div class="text-slate-500">Timeline ({s.timeline.length}):</div>
                          {#each s.timeline.slice(0, 8) as t}
                            <div class="flex gap-2">
                              <span class="mono text-sky-300/80 w-20 shrink-0">{t.date || "—"}</span>
                              <span class="text-slate-300">{t.event}</span>
                            </div>
                          {/each}
                        </div>
                      {/if}
                      {#if s.evidence && s.evidence.length}
                        <div class="space-y-1">
                          <div class="text-slate-500">Evidence:</div>
                          {#each s.evidence as ev}
                            <div class="border-l-2 border-slate-700 pl-2">
                              <span class="{ev.supports ? 'text-emerald-400' : 'text-red-400'}">{ev.supports ? '✓' : '✕'}</span>
                              <span class="text-slate-300">{ev.reasoning}</span>
                              {#if ev.source?.startsWith?.("http")}
                                <a class="text-emerald-400 hover:underline ml-1" href={ev.source} target="_blank" rel="noopener">[{publisherOf(ev.source)}]</a>
                              {/if}
                            </div>
                          {/each}
                        </div>
                      {/if}
                      {#if s.sources && s.sources.length}
                        <div class="flex flex-wrap gap-x-2">
                          <span class="text-slate-500">sources:</span>
                          {#each s.sources.slice(0, 8) as u}
                            {#if u.startsWith("http")}<a class="text-emerald-400 hover:underline" href={u} target="_blank" rel="noopener">{publisherOf(u)}</a>{/if}
                          {/each}
                        </div>
                      {/if}
                    </div>
                  {/if}
                </li>
              {/each}
            </ul>
          </section>
          <section class="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
            <div class="text-xs uppercase tracking-wider text-slate-500 mb-2">
              Relationships ({result.relationships.length})
              {#if result.asOf}<span class="text-sky-400 normal-case tracking-normal"> · as of {result.asOf}</span>{/if}
            </div>
            <ul class="space-y-1.5 text-sm">
              {#each result.relationships.slice(0, 40) as r}
                <li class="text-slate-300">
                  <span class="text-slate-200">{r.src}</span>
                  <span class="text-slate-600">→</span>
                  <span class="text-slate-200">{r.dst}</span>
                  {#if r.activeWindow}
                    <span class="ml-1 text-[11px] mono text-sky-300/70" title="active window (valid time)">
                      {r.activeWindow[0]}{r.activeWindow[1] !== r.activeWindow[0] ? `–${r.activeWindow[1]}` : ""}
                    </span>
                  {:else if r.firstSeen}
                    <span class="ml-1 text-[11px] mono text-slate-500" title="first asserted (observed time)">{r.firstSeen}</span>
                  {/if}
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
