<script lang="ts">
  import { api } from "../lib/api";
  import type { MonitorWatchlist, MonitorDigest, MonitorRule } from "../lib/types";

  let watchlist = $state<MonitorWatchlist | null>(null);
  let dates = $state<string[]>([]);
  let running = $state(false);
  let digest = $state<MonitorDigest | null>(null);
  let selectedDate = $state("");
  let newEntity = $state("");
  let period = $state("1d");
  let k = $state(8);
  let err = $state("");
  let pollTimer: any = null;
  let rules = $state<MonitorRule[]>([]);
  let showRules = $state(false);

  function sevClass(s: string): string {
    return s === "high" ? "text-rose-300 border-rose-700/50 bg-rose-900/20"
      : s === "low" ? "text-slate-400 border-slate-700 bg-slate-800"
      : "text-amber-300 border-amber-700/50 bg-amber-900/20";
  }
  async function loadRules() {
    try { rules = (await api.monitorRules()).rules; } catch (e: any) { err = e?.message; }
  }
  async function removeRule(name: string) {
    rules = (await api.monitorEditRules({ remove: name })).rules;
  }

  async function loadWatchlist() {
    try { watchlist = await api.monitorWatchlist(); } catch (e: any) { err = e?.message; }
  }
  async function loadDigests() {
    try {
      const r = await api.monitorDigests();
      dates = r.dates;
      const wasRunning = running;
      running = r.running;
      if (!selectedDate && dates.length) selectDate(dates[0]);
      if (wasRunning && !running) { selectedDate && selectDate(selectedDate); } // refresh after a run
      if (running && !pollTimer) pollTimer = setInterval(loadDigests, 4000);
      if (!running && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    } catch (e: any) { err = e?.message; }
  }
  async function selectDate(d: string) {
    selectedDate = d;
    try { digest = await api.monitorDigest(d); } catch (e: any) { err = e?.message; }
  }

  async function addEntity() {
    const n = newEntity.trim();
    if (!n) return;
    watchlist = await api.monitorEditWatchlist({ add: [n] });
    newEntity = "";
  }
  async function removeEntity(n: string) {
    watchlist = await api.monitorEditWatchlist({ remove: [n] });
  }
  async function setDomain(v: string) {
    watchlist = await api.monitorEditWatchlist({ domain: v });
  }
  async function runNow() {
    err = "";
    try {
      const r = await api.monitorRun(k, period);
      running = r.running;
      if (running && !pollTimer) pollTimer = setInterval(loadDigests, 4000);
    } catch (e: any) { err = e?.message; }
  }

  loadWatchlist();
  loadDigests();
  loadRules();
</script>

<div class="flex-1 overflow-y-auto scrollbar">
  <div class="max-w-5xl mx-auto p-8">
    <div class="flex items-baseline gap-3 mb-1">
      <h1 class="text-lg font-semibold text-slate-200">Monitor</h1>
      <span class="text-xs text-slate-500">a standing watch over your graph — fresh news intersected with the KG, ranked by impact</span>
    </div>

    {#if err}<div class="text-red-400 text-sm my-3">{err}</div>{/if}

    <!-- Watchlist editor -->
    <section class="mt-4 rounded-lg border border-slate-800 bg-slate-900/50 p-4">
      <div class="text-xs uppercase tracking-wider text-slate-500 mb-2">Watchlist</div>
      <div class="flex flex-wrap gap-2 mb-3">
        {#each (watchlist?.entities || []) as e}
          <span class="flex items-center gap-1 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200">
            {e}
            <button class="text-slate-500 hover:text-red-400" onclick={() => removeEntity(e)}>✕</button>
          </span>
        {/each}
        {#if !(watchlist?.entities || []).length}
          <span class="text-xs text-slate-500">No entities yet — add KG canonical names you want to watch.</span>
        {/if}
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <input
          class="bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 min-w-[16rem]"
          placeholder="add entity (e.g. SAMIDOUN)"
          bind:value={newEntity}
          onkeydown={(e) => e.key === "Enter" && addEntity()} />
        <button class="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-1.5 text-sm text-emerald-200 hover:bg-emerald-900/70"
          onclick={addEntity}>Add</button>
        <span class="text-slate-700">·</span>
        <input
          class="bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 min-w-[14rem]"
          placeholder="optional domain query"
          value={watchlist?.domain || ""}
          onchange={(e) => setDomain((e.target as HTMLInputElement).value)} />
      </div>
    </section>

    <!-- Run controls -->
    <div class="mt-4 flex flex-wrap items-center gap-3 text-sm">
      <button class="rounded border border-sky-700 bg-sky-900/40 px-4 py-2 text-sky-200 hover:bg-sky-900/70 disabled:opacity-50"
        disabled={running || !(watchlist?.entities || []).length}
        onclick={runNow}>{running ? "Running…" : "Run monitor now"}</button>
      <label class="flex items-center gap-1 text-xs text-slate-400">window
        <select class="bg-slate-800 border border-slate-700 rounded px-2 py-1" bind:value={period}>
          <option value="1d">1 day</option><option value="7d">7 days</option><option value="30d">30 days</option>
        </select></label>
      <label class="flex items-center gap-1 text-xs text-slate-400">top-k
        <input type="number" min="1" max="20" bind:value={k} class="w-14 bg-slate-800 border border-slate-700 rounded px-2 py-1" /></label>
      {#if running}<span class="text-xs text-sky-300 animate-pulse">fetching news + extracting…</span>{/if}
      <div class="ml-auto flex items-center gap-2">
        <span class="text-xs text-slate-500">digest</span>
        <select class="bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm"
          value={selectedDate} onchange={(e) => selectDate((e.target as HTMLSelectElement).value)}>
          {#each dates as d}<option value={d}>{d}</option>{/each}
          {#if !dates.length}<option value="">— none yet —</option>{/if}
        </select>
      </div>
    </div>

    <!-- Digest -->
    {#if digest}
      <div class="mt-3 text-xs text-slate-500">
        {#if digest.intake}
          {digest.intake.articles} articles · {digest.intake.extractedNodes} extracted · {digest.intake.intersectedEvents} touched the graph ·
        {/if}
        {digest.counts.events} events · <span class="text-amber-300">{digest.counts.alerts} alerts</span>
        (threshold {digest.alertThreshold})
      </div>
      <ul class="mt-3 space-y-2">
        {#each digest.events as e}
          <li class="rounded-lg border {e.alert ? 'border-amber-700/50 bg-amber-900/10' : 'border-slate-800 bg-slate-900/50'} p-3">
            <div class="flex items-baseline gap-2">
              {#if e.alert}<span title="alert">🔔</span>{/if}
              <span class="rounded bg-slate-800 px-1.5 text-[11px] mono text-sky-300/90">{e.topScore.toFixed(3)}</span>
              <span class="mono text-xs text-slate-500 w-24 shrink-0">{e.event.date || "—"}</span>
              <span class="text-slate-200 text-sm">{e.event.id}</span>
              {#if e.dateConflict}<span class="text-amber-300 text-[11px]" title="dates disagree">⚠ dates</span>{/if}
            </div>
            <div class="mt-1 pl-2 text-xs text-slate-400">
              touched
              {#each e.touched as t}<span class="mono text-slate-300">{t}</span>{#if e.watched.includes(t)}<span class="text-emerald-400" title="watched">★</span>{/if} {/each}
            </div>
            {#if e.impacted.length}
              <div class="mt-1.5 pl-2 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-0.5">
                {#each e.impacted.slice(0, 8) as imp}
                  <div class="flex items-center gap-2 text-xs">
                    <span class="mono text-sky-300/80 w-12 text-right">{imp.score.toFixed(3)}</span>
                    <span class="mono text-slate-500 w-12 text-right" title="belief shift">Δ{imp.delta >= 0 ? "+" : ""}{imp.delta.toFixed(2)}</span>
                    <span class="text-slate-500" title="hops from touched entity">h{imp.hops}</span>
                    <span class="text-slate-300 truncate">{imp.entity}</span>
                    {#if imp.watched}<span class="text-emerald-400">★</span>{/if}
                    {#if imp.isBroker}<span class="text-violet-400 text-[10px]" title="broker">⬡</span>{/if}
                  </div>
                {/each}
              </div>
            {/if}
          </li>
        {/each}
      </ul>

      <!-- CEP patterns completed in this digest -->
      {#if digest.patterns && digest.patterns.length}
        <div class="mt-6 text-xs uppercase tracking-wider text-slate-500 mb-2">
          Patterns completed ({digest.patterns.length})
        </div>
        <ul class="space-y-2">
          {#each digest.patterns as p}
            <li class="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
              <div class="flex items-baseline gap-2">
                <span class="rounded border px-1.5 text-[11px] {sevClass(p.severity)}">{p.severity}</span>
                <span class="text-slate-200 text-sm">{p.rule}</span>
                <span class="text-slate-500 text-xs">· {p.span.days}d span</span>
                {#if p.bridges.length}<span class="text-xs text-slate-500">via <span class="mono text-emerald-300/80">{p.bridges.slice(0, 3).join(", ")}</span></span>{/if}
              </div>
              <ol class="mt-1 pl-2 space-y-0.5">
                {#each p.events as ev, i}
                  <li class="flex items-center gap-2 text-xs">
                    <span class="text-slate-600">{i + 1}.</span>
                    <span class="mono text-sky-300/80 w-24 shrink-0">{ev.date}</span>
                    <span class="rounded bg-slate-800 px-1 text-[10px] text-slate-400">{ev.type}</span>
                    <span class="text-slate-300 truncate">{ev.id}</span>
                  </li>
                {/each}
              </ol>
            </li>
          {/each}
        </ul>
      {/if}
    {:else}
      <div class="mt-6 text-sm text-slate-500">No digest yet. Add watched entities and click <b>Run monitor now</b>.</div>
    {/if}

    <!-- Rule library -->
    <div class="mt-8 rounded-lg border border-slate-800 bg-slate-900/40">
      <button class="w-full flex items-center gap-2 px-4 py-2.5 text-sm text-slate-300 text-left"
        onclick={() => (showRules = !showRules)}>
        <span class="text-slate-500">{showRules ? "▾" : "▸"}</span>
        Pattern rules ({rules.length})
        <span class="text-slate-600 text-xs font-normal ml-1">chronological event chains the monitor watches for</span>
      </button>
      {#if showRules}
        <ul class="px-4 pb-3 space-y-2">
          {#each rules as r}
            <li class="flex items-center gap-2 text-xs">
              <span class="rounded border px-1.5 text-[10px] {sevClass(r.severity)}">{r.severity}</span>
              <span class="text-slate-200">{r.name}</span>
              <span class="text-slate-500">≤{r.windowDays}d:</span>
              <span class="text-slate-400 mono truncate">
                {r.steps.map((s) => (s.types || s.keywords || []).join("/")).join(" → ")}
              </span>
              <button class="ml-auto text-slate-600 hover:text-red-400" title="remove rule"
                onclick={() => removeRule(r.name)}>✕</button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
  </div>
</div>
