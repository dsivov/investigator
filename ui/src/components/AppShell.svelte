<script lang="ts">
  import { api } from "../lib/api";
  import type { InvestigationRow } from "../lib/types";
  import { currentRoute, navigate } from "../lib/router.svelte";
  import { dataVersion } from "../lib/store.svelte";
  import type { Snippet } from "svelte";

  let { children }: { children?: Snippet } = $props();

  let recent = $state<InvestigationRow[]>([]);
  let health = $state<"ok" | "down" | "checking">("checking");

  // Health: once on mount.
  $effect(() => {
    api
      .health()
      .then(() => (health = "ok"))
      .catch(() => (health = "down"));
  });

  // Recent list: re-fetch on navigation and whenever dataVersion bumps
  // (e.g. after a delete or a new launch), so the sidebar never shows a
  // stale or deleted run.
  let route = $derived(currentRoute.value);
  $effect(() => {
    route; // re-run on navigation
    dataVersion.value; // re-run on explicit refresh signal
    api
      .listInvestigations()
      .then((r) => (recent = r.items.slice(0, 8)))
      .catch(() => {});
  });
  let activeId = $derived(route.name === "investigation" ? route.params.id : null);
</script>

<div class="flex h-screen overflow-hidden">
  <!-- Sidebar -->
  <aside class="w-60 flex-shrink-0 border-r border-slate-800 bg-slate-900 flex flex-col">
    <div class="px-5 py-4">
      <div class="text-emerald-400 font-bold tracking-tight text-lg">OSINTGraph</div>
      <div class="text-xs text-slate-500 mt-0.5">
        OSINT cross-event analysis
      </div>
    </div>

    <nav class="px-3 space-y-1 text-sm">
      <button
        class="nav-link {route.name === 'dashboard' ? 'active' : ''}"
        onclick={() => navigate("#/")}>Investigations</button
      >
      <button
        class="nav-link {route.name === 'new' ? 'active' : ''}"
        onclick={() => navigate("#/new")}
        >+ New investigation</button
      >
      <button
        class="nav-link {route.name === 'domains' ? 'active' : ''}"
        onclick={() => navigate("#/domains")}>Domains</button
      >
    </nav>

    <div class="mt-6 px-5 text-[10px] uppercase tracking-wider text-slate-500">Recent</div>
    <div class="mt-1 flex-1 overflow-y-auto scrollbar px-2 space-y-0.5">
      {#each recent as r}
        <button
          class="w-full text-left rounded-md px-3 py-1.5 text-xs hover:bg-slate-800
                 {activeId === r.id ? 'bg-slate-800 text-slate-100' : 'text-slate-400'}"
          onclick={() => navigate(`#/investigations/${r.id}`)}
          title={r.title}
        >
          <div class="truncate">{r.title}</div>
          <div class="text-[10px] text-slate-500 mt-0.5 mono">
            {r.summary?.nodes ?? "-"} nodes · {r.summary?.bridges ?? "-"} bridges
          </div>
        </button>
      {/each}
    </div>

    <div class="px-5 py-3 border-t border-slate-800 text-[10px] mono text-slate-500 flex items-center gap-2">
      <span
        class="inline-block w-2 h-2 rounded-full"
        style="background: {health === 'ok'
          ? '#10b981'
          : health === 'down'
          ? '#ef4444'
          : '#f59e0b'}"
      ></span>
      Backend {health === "ok" ? "online" : health === "down" ? "offline" : "checking..."}
    </div>
  </aside>

  <!-- Main content -->
  <main class="flex-1 min-w-0 flex flex-col">
    {@render children?.()}
  </main>
</div>

<style>
  .nav-link {
    display: block;
    width: 100%;
    text-align: left;
    padding: 0.5rem 0.75rem;
    border-radius: 0.375rem;
    color: #cbd5e1;
    cursor: pointer;
  }
  .nav-link:hover {
    background: #1e293b;
  }
  .nav-link.active {
    background: #1e3a8a;
    color: #dbeafe;
  }
</style>
