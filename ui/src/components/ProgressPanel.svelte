<script lang="ts">
  import { threadColourMap } from "../lib/colors";

  // Live progress model populated by InvestigationView from the SSE stream.
  export interface LiveProgress {
    status: string; // queued | running | failed | cancelled
    stageIndex: number; // 0..5, current pipeline stage
    phaseLabel: string; // human-readable current phase
    threads: string[]; // thread names
    perThread: Record<string, { phase: string; current?: number; total?: number; entity?: string }>;
    startedAt: number; // epoch ms
    bridges?: number;
    crossThemes?: number;
    lastLine: string;
  }

  let { live, onCancel }: { live: LiveProgress; onCancel: () => void } = $props();

  const STAGES = [
    { label: "Fetch", hint: "Searching the news aggregator" },
    { label: "Extract", hint: "Reading articles, pulling actors + events" },
    { label: "Build graph", hint: "Merging evidence into a network" },
    { label: "Deepen", hint: "Stage-2 queries on top entities" },
    { label: "Analytics", hint: "Cross-event bridges + themes" },
    { label: "Done", hint: "Writing artifacts" },
  ];

  const colours = $derived(threadColourMap(live.threads));

  // Tick once a second so the elapsed clock updates.
  let now = $state(Date.now());
  $effect(() => {
    const t = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(t);
  });
  const elapsed = $derived.by(() => {
    const s = Math.max(0, Math.floor((now - live.startedAt) / 1000));
    const mm = String(Math.floor(s / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return `${mm}:${ss}`;
  });

  function phaseText(p: { phase: string; current?: number; total?: number; entity?: string }): string {
    if (!p) return "queued";
    if (p.phase === "fetch") return `fetching ${p.total ?? ""} articles`;
    if (p.phase === "extract") return `extracting ${p.current ?? 0}/${p.total ?? 0}`;
    if (p.phase === "stage2_entity") return `deepening ${p.current ?? 0}/${p.total ?? 0}${p.entity ? ` · ${p.entity}` : ""}`;
    if (p.phase === "post") return "building graph";
    if (p.phase === "done") return "done";
    return p.phase;
  }
</script>

<div class="rounded-xl border border-slate-800 bg-slate-900 p-6">
  <!-- Header -->
  <div class="flex items-center justify-between mb-5">
    <div class="flex items-center gap-3">
      <span class="relative flex h-3 w-3">
        <span
          class="animate-ping absolute inline-flex h-full w-full rounded-full opacity-60
                 {live.status === 'failed' ? 'bg-red-400' : live.status === 'cancelled' ? 'bg-slate-400' : 'bg-amber-400'}"
        ></span>
        <span
          class="relative inline-flex rounded-full h-3 w-3
                 {live.status === 'failed' ? 'bg-red-500' : live.status === 'cancelled' ? 'bg-slate-500' : 'bg-amber-500'}"
        ></span>
      </span>
      <div>
        <div class="text-slate-100 font-semibold">
          {live.status === "queued"
            ? "Queued"
            : live.status === "failed"
            ? "Failed"
            : live.status === "cancelled"
            ? "Cancelled"
            : "Investigation running"}
        </div>
        <div class="text-xs text-slate-400">{live.phaseLabel || "waiting for worker…"}</div>
      </div>
    </div>
    <div class="flex items-center gap-4">
      <div class="text-right">
        <div class="text-lg font-bold text-slate-200 mono">{elapsed}</div>
        <div class="text-[10px] text-slate-500 uppercase tracking-wider">elapsed</div>
      </div>
      {#if live.status === "running" || live.status === "queued"}
        <button
          class="text-xs text-amber-300 hover:text-amber-100 border border-amber-700/50 rounded px-3 py-1.5"
          onclick={onCancel}>Cancel</button
        >
      {/if}
    </div>
  </div>

  <!-- Pipeline stepper -->
  <div class="flex items-center gap-1 mb-6">
    {#each STAGES as s, i}
      {@const state = i < live.stageIndex ? "done" : i === live.stageIndex ? "active" : "todo"}
      <div class="flex-1 flex flex-col items-center">
        <div class="flex items-center w-full">
          <div class="flex-1 h-0.5 {i === 0 ? 'opacity-0' : state === 'todo' ? 'bg-slate-800' : 'bg-emerald-600'}"></div>
          <div
            class="w-7 h-7 rounded-full flex items-center justify-center text-[11px] border-2 flex-shrink-0
                   {state === 'done'
              ? 'bg-emerald-900/40 border-emerald-500 text-emerald-300'
              : state === 'active'
              ? 'border-amber-400 text-amber-300 bg-amber-900/20'
              : 'border-slate-700 text-slate-600'}"
          >
            {#if state === "done"}✓{:else if state === "active"}
              <span class="animate-pulse">●</span>
            {:else}{i + 1}{/if}
          </div>
          <div class="flex-1 h-0.5 {i === STAGES.length - 1 ? 'opacity-0' : i < live.stageIndex ? 'bg-emerald-600' : 'bg-slate-800'}"></div>
        </div>
        <div class="mt-1.5 text-[11px] {state === 'active' ? 'text-amber-300 font-medium' : state === 'done' ? 'text-slate-400' : 'text-slate-600'}">
          {s.label}
        </div>
      </div>
    {/each}
  </div>

  <!-- Per-thread status -->
  <div class="space-y-2">
    <div class="text-xs text-slate-500 uppercase tracking-wider mb-1">Threads</div>
    {#each live.threads as t}
      {@const p = live.perThread[t]}
      <div class="flex items-center gap-3 text-sm">
        <span class="inline-block w-2.5 h-2.5 rounded-full flex-shrink-0" style="background: {colours[t] || '#64748b'}"></span>
        <span class="text-slate-300 w-48 truncate mono text-xs">{t}</span>
        <span class="text-slate-400 text-xs flex-1">{phaseText(p)}</span>
        {#if p?.phase === "extract" && p.total}
          <div class="w-28 bg-slate-800 rounded h-1.5">
            <div class="h-1.5 rounded bg-emerald-500" style="width: {Math.min(100, ((p.current ?? 0) / p.total) * 100)}%"></div>
          </div>
        {:else if p?.phase === "stage2_entity" && p.total}
          <div class="w-28 bg-slate-800 rounded h-1.5">
            <div class="h-1.5 rounded bg-sky-500" style="width: {Math.min(100, ((p.current ?? 0) / p.total) * 100)}%"></div>
          </div>
        {/if}
      </div>
    {/each}
  </div>

  {#if live.bridges !== undefined || live.crossThemes !== undefined}
    <div class="mt-5 pt-4 border-t border-slate-800 flex gap-6 text-sm">
      {#if live.bridges !== undefined}
        <div><span class="mono text-emerald-300 font-bold">{live.bridges}</span> <span class="text-slate-500">bridges found</span></div>
      {/if}
      {#if live.crossThemes !== undefined}
        <div><span class="mono text-emerald-300 font-bold">{live.crossThemes}</span> <span class="text-slate-500">cross-thread themes</span></div>
      {/if}
    </div>
  {/if}

  {#if live.lastLine}
    <div class="mt-4 text-[11px] text-slate-600 mono truncate" title={live.lastLine}>
      {live.lastLine}
    </div>
  {/if}
</div>
