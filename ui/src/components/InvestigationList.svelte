<script lang="ts">
  import { api } from "../lib/api";
  import type { InvestigationRow } from "../lib/types";
  import { navigate, investigationUrl } from "../lib/router.svelte";
  import { refreshInvestigations } from "../lib/store.svelte";

  let rows = $state<InvestigationRow[]>([]);
  let loading = $state(true);
  let error = $state<string | null>(null);
  let busy = $state<Record<string, boolean>>({});

  function load() {
    api
      .listInvestigations()
      .then((r) => {
        rows = r.items;
        loading = false;
      })
      .catch((e) => {
        error = e.message;
        loading = false;
      });
  }

  $effect(() => {
    load();
  });

  async function stopRow(e: MouseEvent, r: InvestigationRow) {
    e.stopPropagation();
    busy[r.id] = true;
    busy = { ...busy };
    await api.stopInvestigation(r.id);
    setTimeout(() => {
      load();
      refreshInvestigations();
    }, 400);
  }

  async function deleteRow(e: MouseEvent, r: InvestigationRow) {
    e.stopPropagation();
    if (!confirm(`Delete "${r.title}"?\n\nThis permanently removes the graph data and all generated reports.`)) return;
    busy[r.id] = true;
    busy = { ...busy };
    await api.deleteInvestigation(r.id);
    rows = rows.filter((x) => x.id !== r.id);
    refreshInvestigations();
  }

  function statusColour(s: string) {
    return s === "succeeded" ? "text-emerald-300"
      : s === "running" ? "text-amber-300"
      : s === "failed" ? "text-red-300"
      : s === "cancelled" ? "text-slate-400"
      : "text-slate-500";
  }
</script>

<header class="flex items-center justify-between border-b border-slate-800 bg-slate-900 px-6 py-4">
  <div>
    <h1 class="text-lg font-semibold text-slate-100">Investigations</h1>
    <p class="text-xs text-slate-500 mt-0.5">
      Past and in-flight cross-event runs. Click any row to open it.
    </p>
  </div>
  <button
    class="bg-emerald-600 hover:bg-emerald-500 text-emerald-50 text-sm rounded-md px-3 py-1.5"
    onclick={() => navigate("#/new")}
  >
    + New investigation
  </button>
</header>

<div class="flex-1 overflow-y-auto scrollbar p-6">
  {#if loading}
    <div class="text-slate-500 text-sm italic">Loading…</div>
  {:else if error}
    <div class="text-red-400 text-sm">Failed to load investigations: {error}</div>
    <div class="text-slate-500 text-xs mt-2">
      Is the backend running? Try
      <code class="mono">python research/ui_server.py</code>.
    </div>
  {:else if rows.length === 0}
    <div class="text-slate-500 text-sm italic">No investigations yet.</div>
  {:else}
    <div class="rounded-xl border border-slate-800 overflow-hidden">
      <table class="w-full text-sm">
        <thead class="text-xs uppercase tracking-wider text-slate-500 bg-slate-900/40">
          <tr>
            <th class="text-left py-2 px-4">Title</th>
            <th class="text-left py-2 px-3">Status</th>
            <th class="text-right py-2 px-3">Nodes</th>
            <th class="text-right py-2 px-3">Edges</th>
            <th class="text-right py-2 px-3">Bridges</th>
            <th class="text-right py-2 px-3">All-thread</th>
            <th class="text-right py-2 px-3">Threads</th>
            <th class="text-right py-2 px-4">Created</th>
            <th class="text-right py-2 px-4"></th>
          </tr>
        </thead>
        <tbody>
          {#each rows as r}
            <tr
              class="border-t border-slate-800 hover:bg-slate-800/40 cursor-pointer {busy[r.id] ? 'opacity-40' : ''}"
              onclick={() => navigate(investigationUrl(r.id))}
            >
              <td class="py-2 px-4 text-slate-200">
                {r.title}
                <div class="text-[10px] mono text-slate-500">{r.id}</div>
              </td>
              <td class="py-2 px-3 text-xs uppercase tracking-wider {statusColour(r.status)}">{r.status}</td>
              <td class="py-2 px-3 text-right mono text-slate-300">{r.summary?.nodes ?? "-"}</td>
              <td class="py-2 px-3 text-right mono text-slate-300">{r.summary?.edges ?? "-"}</td>
              <td class="py-2 px-3 text-right mono text-slate-300">{r.summary?.bridges ?? "-"}</td>
              <td class="py-2 px-3 text-right mono text-emerald-300">
                {r.summary?.bridges_all_threads ?? "-"}
              </td>
              <td class="py-2 px-3 text-right mono text-slate-400">{r.summary?.threads ?? "-"}</td>
              <td class="py-2 px-4 text-right text-xs text-slate-500 mono">
                {r.createdAt ? r.createdAt.slice(0, 10) : "-"}
              </td>
              <td class="py-2 px-4 text-right whitespace-nowrap">
                {#if r.status === "running" || r.status === "queued"}
                  <button
                    class="text-xs text-amber-300 hover:text-amber-100"
                    title="Stop this run"
                    onclick={(e) => stopRow(e, r)}>Stop</button
                  >
                {:else}
                  <button
                    class="text-xs text-slate-500 hover:text-red-300"
                    title="Delete this investigation"
                    onclick={(e) => deleteRow(e, r)}>Delete</button
                  >
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
