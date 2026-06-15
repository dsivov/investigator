<script lang="ts">
  import { api } from "../lib/api";
  import type { InvestigationFull } from "../lib/types";
  import { navigate, investigationUrl } from "../lib/router.svelte";

  import Overview from "./tabs/Overview.svelte";
  import Graph from "./tabs/Graph.svelte";
  import Tmfg from "./tabs/Tmfg.svelte";
  import Data from "./tabs/Data.svelte";
  import Report from "./tabs/Report.svelte";
  import Sources from "./tabs/Sources.svelte";
  import Help from "./tabs/Help.svelte";
  import ProgressPanel from "./ProgressPanel.svelte";
  import type { LiveProgress } from "./ProgressPanel.svelte";
  import { refreshInvestigations } from "../lib/store.svelte";

  let { id, tab }: { id: string; tab: string } = $props();

  let inv = $state<InvestigationFull | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);

  // Live-progress model, populated from the SSE stream while a run is active.
  // null when the investigation is finished/idle.
  let live = $state<LiveProgress | null>(null);

  // Map a phase to a monotonic pipeline-stage index.
  const PHASE_STAGE: Record<string, number> = {
    fetch: 0,
    extract: 1,
    post: 2,
    stage2_entity: 3,
  };

  $effect(() => {
    // Re-runs whenever `id` changes. Reset all per-investigation state up
    // front so a previously-open run's progress panel can't leak into the
    // view of a different (e.g. completed) investigation. `cancelled` guards
    // against a slow fetch or a late SSE event from the prior view resolving
    // into this one.
    const myId = id;
    let cancelled = false;
    loading = true;
    error = null;
    live = null;
    inv = null;
    let es: EventSource | null = null;

    api
      .getInvestigation(myId)
      .then((r) => {
        if (cancelled) return;
        inv = r;
        loading = false;
        if (r.status === "queued" || r.status === "running") {
          const threads = (r.threads ?? []).map((t) => t.name);
          live = {
            status: r.status,
            stageIndex: 0,
            phaseLabel: "",
            threads,
            perThread: {},
            startedAt: r.createdAt ? Date.parse(r.createdAt) : Date.now(),
            lastLine: "",
          };
          let currentThread = threads[0] ?? "";

          const bump = (stage: number) => {
            if (live && stage > live.stageIndex) live.stageIndex = stage;
          };

          es = api.streamInvestigation(myId);
          es.addEventListener("started", () => {
            if (live) live.status = "running";
          });
          es.addEventListener("thread_started", (e: any) => {
            const d = JSON.parse(e.data);
            currentThread = d.thread ?? currentThread;
            if (live) {
              live.perThread[currentThread] = { phase: "fetch" };
              live.phaseLabel = `${currentThread} · starting`;
              live.lastLine = `Starting ${currentThread}`;
            }
          });
          es.addEventListener("thread_progress", (e: any) => {
            const d = JSON.parse(e.data);
            if (!live) return;
            live.perThread[currentThread] = {
              phase: d.phase,
              current: d.current,
              total: d.total,
              entity: d.entity,
            };
            bump(PHASE_STAGE[d.phase] ?? live.stageIndex);
            if (d.phase === "fetch") live.phaseLabel = `${currentThread} · fetching ${d.total ?? ""} articles`;
            else if (d.phase === "extract") live.phaseLabel = `${currentThread} · extracting ${d.current}/${d.total}`;
            else if (d.phase === "stage2_entity") live.phaseLabel = `${currentThread} · deepening ${d.current}/${d.total}`;
            else if (d.phase === "post") live.phaseLabel = `${currentThread} · building graph`;
          });
          es.addEventListener("cross_event_analytics", (e: any) => {
            if (!live) return;
            bump(4);
            const d = JSON.parse(e.data);
            if (d.bridges !== undefined) live.bridges = d.bridges;
            if (d.crossThemes !== undefined) live.crossThemes = d.crossThemes;
            live.phaseLabel = "cross-event analytics";
          });
          es.addEventListener("artifacts_ready", () => {
            if (live) bump(5);
          });

          const finish = () => {
            es?.close();
            api.getInvestigation(myId).then((r2) => {
              if (!cancelled) inv = r2;
            });
            if (!cancelled) live = null;
          };
          es.addEventListener("succeeded", finish);
          es.addEventListener("failed", () => {
            if (live) {
              live.status = "failed";
              live.phaseLabel = "failed — see log";
            }
            es?.close();
          });
          es.addEventListener("cancelled", () => {
            if (live) live.status = "cancelled";
            es?.close();
          });
        }
      })
      .catch((e) => {
        if (cancelled) return;
        error = e.message;
        loading = false;
      });

    return () => {
      cancelled = true;
      es?.close();
    };
  });

  function cancelRun() {
    api.stopInvestigation(id);
  }

  async function deleteRun() {
    const label = inv?.title ?? id;
    if (!confirm(`Delete investigation "${label}"?\n\nThis permanently removes the graph data and all generated reports. This cannot be undone.`)) {
      return;
    }
    await api.deleteInvestigation(id);
    refreshInvestigations();
    navigate("#/");
  }

  const TABS = [
    { id: "overview", label: "Overview" },
    { id: "graph", label: "Graph" },
    { id: "tmfg", label: "TMFG themes" },
    { id: "data", label: "Data" },
    { id: "report", label: "Report" },
    { id: "sources", label: "Sources" },
    { id: "help", label: "Help" },
  ];
</script>

<header class="flex items-center justify-between border-b border-slate-800 bg-slate-900 px-6 py-3">
  <div class="min-w-0 flex items-center gap-3">
    <button class="text-slate-500 hover:text-slate-300" onclick={() => navigate("#/")}>‹ All</button>
    <div class="text-sm text-slate-300 truncate">
      {inv?.title ?? "Loading…"}
    </div>
  </div>
  <div class="flex items-center gap-4">
    {#if inv}
      <div class="text-xs text-slate-400 mono">
        {(inv.threads ?? []).length} threads · {(inv.domain || "general").replace(/_/g, " ")} ·
        {inv.period} · <span class="text-slate-500">{inv.id}</span>
      </div>
    {/if}
    {#if live && (live.status === "running" || live.status === "queued")}
      <button
        class="text-xs text-amber-300 hover:text-amber-100 border border-amber-700/50 rounded px-2 py-1"
        onclick={cancelRun}>Stop</button
      >
    {:else if inv}
      <button
        class="text-xs text-slate-400 hover:text-red-300 border border-slate-700 hover:border-red-700/60 rounded px-2 py-1"
        onclick={deleteRun}>Delete</button
      >
    {/if}
  </div>
</header>

<!-- Compact progress strip: shown on every tab EXCEPT overview (which gets
     the full ProgressPanel in its body). -->
{#if live && tab !== "overview"}
  <div class="flex items-center gap-3 border-b border-amber-700/40 bg-amber-900/15 px-6 py-2 text-xs">
    <span class="inline-block w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
    <span class="text-amber-200 font-medium uppercase tracking-wider">{live.status}</span>
    <span class="text-amber-100/70 mono">{live.phaseLabel || "waiting for worker…"}</span>
    <button
      class="ml-auto text-xs text-amber-300 hover:text-amber-100"
      onclick={cancelRun}
    >
      Cancel
    </button>
  </div>
{:else if live && live.status === "failed"}
  <div class="flex items-center gap-3 border-b border-red-700/40 bg-red-900/15 px-6 py-2 text-xs">
    <span class="text-red-300 font-medium uppercase tracking-wider">failed</span>
    <a class="text-red-300 hover:underline mono" href={api.logUrl(id)} target="_blank">view log</a>
  </div>
{/if}

<nav class="flex items-center gap-1 border-b border-slate-800 bg-slate-900 px-6">
  {#each TABS as t}
    <button
      class="px-3 py-2 text-sm font-medium {tab === t.id
        ? 'text-emerald-300 border-b-2 border-emerald-400'
        : 'text-slate-500 hover:text-slate-300'}"
      onclick={() => navigate(investigationUrl(id, t.id))}
    >
      {t.label}
    </button>
  {/each}
</nav>

<div class="flex-1 min-h-0 flex flex-col">
  {#if loading}
    <div class="p-6 text-slate-500 text-sm italic">Loading investigation…</div>
  {:else if error}
    <div class="p-6 text-red-400 text-sm">{error}</div>
  {:else if inv}
    {#if tab === "overview"}
      {#if live}
        <div class="p-6">
          <ProgressPanel {live} onCancel={cancelRun} />
        </div>
      {:else}
        <Overview {inv} />
      {/if}
    {:else if tab === "graph"}
      <Graph {id} runs={(inv.threads ?? []).map((t) => t.name)} />
    {:else if tab === "tmfg"}
      <Tmfg {id} runs={(inv.threads ?? []).map((t) => t.name)} />
    {:else if tab === "data"}
      <Data {id} />
    {:else if tab === "report"}
      <Report {id} {inv} />
    {:else if tab === "sources"}
      <Sources {id} />
    {:else if tab === "help"}
      <Help />
    {/if}
  {/if}
</div>
