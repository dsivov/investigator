<script lang="ts">
  import { onDestroy } from "svelte";
  import { api } from "../lib/api";
  import type { OpenRegistryStatus } from "../lib/types";

  let or = $state<OpenRegistryStatus | null>(null);
  let busy = $state(false);
  let note = $state("");
  let err = $state("");
  let pastedUrl = $state("");
  let poll: ReturnType<typeof setInterval> | null = null;

  async function refresh() {
    try {
      or = await api.getOpenRegistry();
      // Stop polling once the login subprocess has finished.
      if (or && !or.loginInProgress && poll) {
        clearInterval(poll);
        poll = null;
      }
    } catch (e: any) {
      err = e?.message || "Failed to load status";
    }
  }
  refresh();

  function startPolling() {
    if (poll) return;
    poll = setInterval(refresh, 2000);
  }
  onDestroy(() => poll && clearInterval(poll));

  async function connect() {
    busy = true;
    err = "";
    note = "";
    try {
      or = await api.openRegistryLogin();
      note = or.message || "Authorize in the browser window that opened.";
      startPolling();
    } catch (e: any) {
      err = e?.message || "Login failed";
    } finally {
      busy = false;
    }
  }
  async function complete() {
    if (!pastedUrl.trim()) return;
    busy = true;
    err = "";
    note = "";
    try {
      or = await api.openRegistryComplete(pastedUrl.trim());
      note = or.connected ? "Connected." : (or.message || "Submitted.");
      if (!or.connected) startPolling();
      pastedUrl = "";
    } catch (e: any) {
      err = e?.message || "Could not complete login";
    } finally {
      busy = false;
    }
  }
  async function disconnect() {
    busy = true;
    err = "";
    note = "";
    try {
      or = await api.openRegistryLogout();
      note = or.removed ? "Disconnected." : "Nothing to disconnect.";
    } catch (e: any) {
      err = e?.message || "Disconnect failed";
    } finally {
      busy = false;
    }
  }
</script>

<div class="flex-1 overflow-y-auto scrollbar">
  <div class="max-w-3xl mx-auto p-8">
    <h1 class="text-lg font-semibold text-slate-200 mb-1">Settings</h1>
    <p class="text-sm text-slate-500 mb-6">Connect external data providers used for entity enrichment.</p>

    <section class="rounded-lg border border-slate-800 bg-slate-900/60">
      <div class="px-5 py-4 border-b border-slate-800 flex items-center gap-3">
        <div class="min-w-0">
          <div class="text-slate-200 font-medium">OpenRegistry</div>
          <div class="text-xs text-slate-500 truncate">
            30 national company registries (beneficial owners, officers, shareholders)
          </div>
        </div>
        <div class="ml-auto flex items-center gap-2 text-xs">
          {#if or?.loginInProgress}
            <span class="inline-block w-2 h-2 rounded-full bg-amber-400 animate-pulse"></span>
            <span class="text-amber-300">Awaiting authorization…</span>
          {:else if or?.connected}
            <span class="inline-block w-2 h-2 rounded-full bg-emerald-400"></span>
            <span class="text-emerald-300">Connected{or.method === "static_token" ? " (token)" : ""}</span>
          {:else}
            <span class="inline-block w-2 h-2 rounded-full bg-slate-600"></span>
            <span class="text-slate-400">Not connected</span>
          {/if}
        </div>
      </div>

      <div class="px-5 py-4 space-y-3">
        <div class="text-xs text-slate-500 mono break-all">{or?.url ?? ""}</div>

        {#if or?.method === "static_token"}
          <p class="text-xs text-slate-400">
            Using a static <span class="mono">INVESTIGATOR_OPENREGISTRY_TOKEN</span>; no login required.
          </p>
        {:else}
          <div class="flex items-center gap-2">
            {#if or?.connected}
              <button
                class="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:bg-slate-800 disabled:opacity-50"
                disabled={busy}
                onclick={disconnect}>Disconnect</button>
              <button
                class="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                disabled={busy || or?.loginInProgress}
                onclick={connect}>Re-authorize</button>
            {:else}
              <button
                class="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-1.5 text-sm text-emerald-200 hover:bg-emerald-900/70 disabled:opacity-50"
                disabled={busy || or?.loginInProgress}
                onclick={connect}>{or?.loginInProgress ? "Authorizing…" : "Connect"}</button>
            {/if}
          </div>
          <p class="text-xs text-slate-500">
            Connect opens a browser window to authorize access. Tokens are stored locally and refresh automatically.
          </p>
        {/if}

        {#if or?.loginInProgress}
          <div class="rounded border border-slate-800 bg-slate-950/50 p-3 space-y-2">
            <div class="text-xs text-slate-300 font-medium">Finish authorizing</div>
            {#if or?.authorizeUrl}
              <p class="text-xs text-slate-400">
                1. <a class="text-emerald-400 hover:underline break-all" href={or.authorizeUrl} target="_blank" rel="noopener">Open the authorization page</a> and click Authorise.
              </p>
            {/if}
            <p class="text-xs text-slate-400">
              2. If your browser doesn't connect automatically, copy the URL it lands on
              (it looks like <span class="mono">http://localhost:8765/callback?code=…</span>) and paste it here:
            </p>
            <div class="flex items-center gap-2">
              <input
                class="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-slate-200 placeholder-slate-500 text-xs mono"
                placeholder="http://localhost:8765/callback?code=…&state=…"
                bind:value={pastedUrl} />
              <button
                class="rounded border border-emerald-700 bg-emerald-900/40 px-3 py-1.5 text-sm text-emerald-200 hover:bg-emerald-900/70 disabled:opacity-50"
                disabled={busy || !pastedUrl.trim()}
                onclick={complete}>Complete</button>
            </div>
          </div>
        {/if}
        {#if note}<p class="text-xs text-emerald-300/90">{note}</p>{/if}
        {#if err}<p class="text-xs text-red-400">{err}</p>{/if}
      </div>
    </section>
  </div>
</div>
