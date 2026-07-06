<script lang="ts">
  import { api } from "../lib/api";
  import type { Domain } from "../lib/types";
  import { navigate, investigationUrl } from "../lib/router.svelte";
  import AdvancedSettings from "./AdvancedSettings.svelte";

  type Kind = "single" | "multi";
  type ThreadInput = { name: string; query: string };

  let step = $state(1);
  let kind = $state<Kind>("multi");

  let domains = $state<Domain[]>([]);
  let selectedDomainKey = $state("sanctions_evasion");
  let hypothesisOverride = $state("");
  let thresholdOverride = $state<number | null>(null);
  let editingHypothesis = $state(false);

  let threads = $state<ThreadInput[]>([
    { name: "", query: "" },
    { name: "", query: "" },
    { name: "", query: "" },
  ]);

  let period = $state("1y");
  let showAdvanced = $state(false);
  let stage1Articles = $state(50);
  let stage2ArticlesPerEntity = $state(20);
  let topNEntities = $state(8);
  // Enhanced retrieval (opt-in): LLM query-expansion + title rerank + depth.
  let enhancedRetrieval = $state(false);
  let retrievalDepth = $state(2);
  let retrievalExpansions = $state(4);

  // Manual sources (PDF uploads + URLs) and the GNews toggle.
  let gnewsEnabled = $state(true);
  // Additional configurable search sources (Wikipedia / GDELT / ...).
  type SearchSource = { id: string; label: string; description: string; requiresKey: boolean; available: boolean };
  let searchSources = $state<SearchSource[]>([]);
  let enabledSources = $state<Set<string>>(new Set());
  api.listSearchSources().then(({ items }) => (searchSources = items)).catch(() => {});
  function toggleSource(id: string) {
    const s = new Set(enabledSources);
    s.has(id) ? s.delete(id) : s.add(id);
    enabledSources = s;
  }
  let urlsText = $state("");
  let uploadedPdfs = $state<{ id: string; name: string; bytes: number }[]>([]);
  let uploading = $state(false);
  let sourceError = $state<string | null>(null);

  function usableUrls(): string[] {
    return urlsText.split("\n").map((s) => s.trim()).filter(Boolean);
  }

  async function onPickPdfs(e: Event) {
    const input = e.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    if (!files.length) return;
    uploading = true;
    sourceError = null;
    try {
      const { items } = await api.uploadSources(files);
      uploadedPdfs = [...uploadedPdfs, ...items];
    } catch (err: any) {
      sourceError = err.message || "upload failed";
    } finally {
      uploading = false;
      input.value = "";
    }
  }
  function removePdf(id: string) {
    uploadedPdfs = uploadedPdfs.filter((p) => p.id !== id);
  }

  let submitting = $state(false);
  let error = $state<string | null>(null);

  $effect(() => {
    api.listDomains().then((r) => {
      domains = r.items;
    });
  });

  const selectedDomain = $derived(
    domains.find((d) => d.key === selectedDomainKey) ?? null
  );

  // When kind flips to single, collapse to one thread; multi expands to >= 2.
  function setKind(k: Kind) {
    kind = k;
    if (k === "single") {
      threads = [threads[0] ?? { name: "", query: "" }];
    } else if (threads.length < 2) {
      threads = [...threads, { name: "", query: "" }];
    }
  }

  function autoName(query: string): string {
    return query
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .split("_")
      .slice(0, 4)
      .join("_");
  }

  function onQueryBlur(i: number) {
    if (!threads[i].name && threads[i].query) {
      threads[i].name = autoName(threads[i].query);
      threads = [...threads];
    }
  }

  function addThread() {
    threads = [...threads, { name: "", query: "" }];
  }
  function removeThread(i: number) {
    threads = threads.filter((_, idx) => idx !== i);
  }

  // Claim seeding (multi kind): plan balanced support/refute threads from a
  // claim; the generated threads stay fully editable and are sent verbatim.
  // claimMeta marks the run as claim mode (stored claim + auto verdict tab).
  let claimText = $state("");
  let claimMeta = $state<{ claim: string; assertion: string } | null>(null);
  let planningClaim = $state(false);
  let claimPlanError = $state("");

  async function seedFromClaim() {
    const c = claimText.trim();
    if (!c || planningClaim) return;
    planningClaim = true;
    claimPlanError = "";
    try {
      const plan = await api.claimPlan(c);
      const gen = [
        ...plan.support.map((q, i) => ({ name: `support_${i + 1}`, query: q })),
        ...plan.refute.map((q, i) => ({ name: `refute_${i + 1}`, query: q })),
      ];
      if (!gen.length) throw new Error("Claim produced no search angles");
      threads = gen;
      claimMeta = { claim: c, assertion: plan.assertion };
    } catch (e: any) {
      claimPlanError = e?.message || "Claim planning failed";
    } finally {
      planningClaim = false;
    }
  }

  // Per-thread query-refinement state.
  let refining = $state<Record<number, boolean>>({});
  let refineError = $state<Record<number, string>>({});

  async function refineThread(i: number) {
    const q = threads[i].query.trim();
    if (!q) return;
    refining[i] = true;
    refineError[i] = "";
    refining = { ...refining };
    try {
      const { refined } = await api.refineQuery(
        q,
        `dom_${selectedDomainKey}`,
        editingHypothesis && hypothesisOverride.trim() ? hypothesisOverride.trim() : undefined
      );
      threads[i].query = refined;
      // Re-derive the name from the refined query if it was auto-derived.
      if (!threads[i].name || threads[i].name === autoName(q)) {
        threads[i].name = autoName(refined);
      }
      threads = [...threads];
    } catch (e: any) {
      refineError[i] = e.message || "refine failed";
      refineError = { ...refineError };
    } finally {
      refining[i] = false;
      refining = { ...refining };
    }
  }

  function validStep1() {
    return kind === "single" || kind === "multi";
  }
  function validStep2() {
    return !!selectedDomain || hypothesisOverride.trim().length > 0;
  }
  function validStep3() {
    const filled = threads.filter((t) => t.query.trim());
    const queryOk = kind === "single" ? filled.length === 1 : filled.length >= 2;
    if (!queryOk) return false;
    // With GNews disabled, at least one other source (search source / URL / PDF).
    if (!gnewsEnabled && enabledSources.size === 0 &&
        usableUrls().length === 0 && uploadedPdfs.length === 0) {
      return false;
    }
    return true;
  }

  // Review step: queries are auto-refined for the domain on launch, then
  // shown for the analyst to veto/edit before the run actually starts.
  type ReviewRow = { name: string; original: string; refined: string; useRefined: boolean };
  let review = $state<ReviewRow[] | null>(null);
  let autoRefining = $state(false);
  let refineNote = $state("");

  async function startReview() {
    error = null;
    refineNote = "";
    autoRefining = true;
    const usable = threads.filter((t) => t.query.trim());
    const rows: ReviewRow[] = [];
    for (const t of usable) {
      const original = t.query.trim();
      let refined = original;
      try {
        const r = await api.refineQuery(
          original,
          `dom_${selectedDomainKey}`,
          editingHypothesis && hypothesisOverride.trim() ? hypothesisOverride.trim() : undefined
        );
        refined = (r.refined || "").trim() || original;
      } catch {
        refineNote = "Auto-refine is unavailable — your queries will be used as typed. Edit below if needed.";
      }
      rows.push({
        name: (t.name || autoName(original)).trim(),
        original,
        refined,
        // Default to the refined query only when it actually differs.
        useRefined: refined.toLowerCase() !== original.toLowerCase(),
      });
    }
    review = rows;
    autoRefining = false;
  }

  async function finalLaunch() {
    if (!review) return;
    error = null;
    submitting = true;
    const usableThreads = review.map((r) => ({
      name: r.name || autoName(r.useRefined ? r.refined : r.original),
      query: (r.useRefined ? r.refined : r.original).trim(),
    }));
    const body: Record<string, unknown> = {
      kind,
      domain: `dom_${selectedDomainKey}`,
      period,
      threads: usableThreads,
      advanced: {
        stage1Articles, stage2ArticlesPerEntity, topNEntities,
        enhancedRetrieval, retrievalDepth, retrievalExpansions,
      },
      gnewsEnabled,
      sources: [...enabledSources],
      extraSources: { urls: usableUrls(), pdfs: uploadedPdfs.map((p) => p.id) },
    };
    if (claimMeta) {
      // Claim mode with pre-planned (possibly edited) threads: the server
      // respects the threads verbatim and stores the claim for the verdict tab.
      body.claim = claimMeta.claim;
      body.assertion = claimMeta.assertion;
    }
    if (editingHypothesis && hypothesisOverride.trim()) {
      body.hypothesisOverride = hypothesisOverride.trim();
    }
    if (thresholdOverride !== null) {
      body.thresholdOverride = thresholdOverride;
    }
    try {
      const res = await api.createInvestigation(body);
      navigate(investigationUrl(res.id, "overview"));
    } catch (e: any) {
      error = e.message;
      submitting = false;
    }
  }

  function beginEditHypothesis() {
    if (selectedDomain && !hypothesisOverride) {
      hypothesisOverride = selectedDomain.hypothesis;
      thresholdOverride = selectedDomain.threshold;
    }
    editingHypothesis = true;
  }
</script>

<header class="border-b border-slate-800 bg-slate-900 px-6 py-4">
  <h1 class="text-lg font-semibold text-slate-100">New investigation</h1>
  <p class="text-xs text-slate-500 mt-0.5">
    Three steps: shape, domain, queries. The run executes server-side; you can
    leave and come back.
  </p>
</header>

<!-- Step indicator -->
<div class="flex items-center gap-2 px-6 py-3 border-b border-slate-800 text-xs">
  {#each [{ n: 1, label: "Shape" }, { n: 2, label: "Domain" }, { n: 3, label: "Queries" }] as s}
    <div
      class="flex items-center gap-1.5 {step === s.n
        ? 'text-emerald-300'
        : step > s.n
        ? 'text-slate-400'
        : 'text-slate-600'}"
    >
      <span
        class="w-5 h-5 rounded-full flex items-center justify-center text-[10px] border
               {step === s.n ? 'border-emerald-400 bg-emerald-900/30' : step > s.n ? 'border-slate-500' : 'border-slate-700'}"
      >
        {step > s.n ? "✓" : s.n}
      </span>
      {s.label}
    </div>
    {#if s.n < 3}<span class="text-slate-700">→</span>{/if}
  {/each}
</div>

<div class="flex-1 overflow-y-auto scrollbar p-6">
  <div class="max-w-3xl">
    {#if step === 1}
      <!-- STEP 1: shape -->
      <h2 class="text-slate-200 font-semibold mb-4">What shape of investigation?</h2>
      <div class="grid grid-cols-2 gap-4">
        <button
          class="text-left rounded-xl border p-5 {kind === 'single'
            ? 'border-emerald-600 bg-emerald-900/10'
            : 'border-slate-800 hover:border-slate-600'}"
          onclick={() => setKind("single")}
        >
          <div class="text-slate-100 font-semibold">Single query</div>
          <p class="text-xs text-slate-400 mt-2 leading-relaxed">
            One focused dig on a single question, one corpus. Fast (~5–15 min).
            Best when you already know the subject and just want the network
            around it.
          </p>
        </button>
        <button
          class="text-left rounded-xl border p-5 {kind === 'multi'
            ? 'border-emerald-600 bg-emerald-900/10'
            : 'border-slate-800 hover:border-slate-600'}"
          onclick={() => setKind("multi")}
        >
          <div class="text-slate-100 font-semibold">Multiple queries (cross-event)</div>
          <p class="text-xs text-slate-400 mt-2 leading-relaxed">
            Three or more parallel angles. The system surfaces the actors that
            <em>bridge</em> the threads — the structural backbone of any
            cross-story claim. Slower (~60–90 min).
          </p>
        </button>
      </div>
    {:else if step === 2}
      <!-- STEP 2: domain -->
      <h2 class="text-slate-200 font-semibold mb-4">Choose a domain</h2>
      <p class="text-xs text-slate-500 mb-4">
        The domain sets the relevance hypothesis — the test each candidate
        entity must pass to count as relevant — and the strictness threshold.
      </p>
      <div class="space-y-2">
        {#each domains as d}
          <button
            class="w-full text-left rounded-lg border p-3 {selectedDomainKey === d.key
              ? 'border-emerald-600 bg-emerald-900/10'
              : 'border-slate-800 hover:border-slate-600'}"
            onclick={() => {
              selectedDomainKey = d.key;
              editingHypothesis = false;
              hypothesisOverride = "";
              thresholdOverride = null;
            }}
          >
            <div class="flex items-center justify-between">
              <span class="text-slate-200 font-medium">{d.name}</span>
              <span class="text-[10px] mono text-slate-500">threshold {d.threshold}</span>
            </div>
            <div class="text-xs text-slate-400 mt-1">{d.description}</div>
          </button>
        {/each}
      </div>

      {#if selectedDomain}
        <div class="mt-5 rounded-lg border border-slate-800 bg-slate-900 p-4">
          <div class="flex items-center justify-between mb-2">
            <div class="text-xs text-slate-500 uppercase tracking-wider">Relevance hypothesis</div>
            {#if !editingHypothesis}
              <button class="text-xs text-emerald-400 hover:underline" onclick={beginEditHypothesis}
                >Edit for this run</button
              >
            {/if}
          </div>
          {#if editingHypothesis}
            <textarea
              class="w-full h-32 bg-slate-800 border border-slate-700 rounded p-2 text-sm text-slate-200"
              bind:value={hypothesisOverride}
            ></textarea>
            <div class="flex items-center gap-3 mt-2">
              <span class="text-xs text-slate-400">Threshold</span>
              <input
                type="number"
                min="0"
                max="1"
                step="0.05"
                aria-label="Relevance threshold"
                class="bg-slate-800 border border-slate-700 rounded px-2 py-1 w-20 text-sm text-slate-200"
                bind:value={thresholdOverride}
              />
              <span class="text-[11px] text-slate-500">
                Override applies to this run only; the preset is not changed.
              </span>
            </div>
          {:else}
            <p class="text-sm text-slate-300 leading-relaxed">{selectedDomain.hypothesis}</p>
          {/if}
        </div>
      {/if}
    {:else if step === 3}
      <!-- STEP 3: queries -->
      <h2 class="text-slate-200 font-semibold mb-1">
        {kind === "single" ? "Your query" : "Your investigative threads"}
      </h2>
      <p class="text-xs text-slate-500 mb-4">
        {kind === "single"
          ? "One GNews search string. The thread name is auto-derived; edit if you like."
          : "Each thread is a separate GNews search. Cross-thread bridges surface from the union. Two minimum; three recommended."}
      </p>

      {#if review !== null}
        <!-- Review state: domain-refined queries, vetoable before launch -->
        <div class="rounded-lg border border-sky-800/50 bg-sky-900/10 p-3 mb-4 text-xs text-sky-100/80">
          We rewrote your queries to target <strong>{selectedDomain?.name ?? "the domain"}</strong>.
          Keep the refined version, switch back to your original, or edit either — then launch.
          {#if refineNote}<div class="mt-1 text-amber-300">{refineNote}</div>{/if}
        </div>
        <div class="space-y-3">
          {#each review as r, i}
            <div class="rounded-lg border border-slate-800 bg-slate-900 p-3">
              <div class="flex items-center justify-between mb-2">
                <span class="text-xs mono text-slate-400">{r.name}</span>
                <div class="flex gap-1">
                  <button
                    class="chip {r.useRefined ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-0.5 text-[11px]"
                    onclick={() => (r.useRefined = true)}>Refined</button
                  >
                  <button
                    class="chip {!r.useRefined ? 'chip-on' : 'chip-off'} rounded-md border px-2 py-0.5 text-[11px]"
                    onclick={() => (r.useRefined = false)}>Original</button
                  >
                </div>
              </div>
              {#if r.useRefined}
                <input
                  class="w-full bg-slate-800 border border-sky-700/50 rounded px-2 py-1.5 text-sm text-slate-100"
                  bind:value={r.refined}
                />
                <div class="text-[11px] text-slate-500 mt-1">
                  original: <span class="mono">{r.original}</span>
                </div>
              {:else}
                <input
                  class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-slate-200"
                  bind:value={r.original}
                />
                <div class="text-[11px] text-slate-500 mt-1">
                  refined suggestion: <span class="mono">{r.refined}</span>
                </div>
              {/if}
            </div>
          {/each}
        </div>
        <button class="mt-3 text-xs text-slate-400 hover:text-slate-200" onclick={() => (review = null)}>
          ‹ Back to edit queries
        </button>
      {:else}

      {#if kind === "multi"}
        <!-- Claim seeding: optional alternative to hand-writing threads -->
        <div class="mb-4 rounded-lg border {claimMeta ? 'border-emerald-800/60 bg-emerald-900/10' : 'border-slate-800 bg-slate-900'} p-4">
          {#if claimMeta}
            <div class="flex items-start justify-between gap-3">
              <div>
                <div class="text-xs text-emerald-300 uppercase tracking-wider mb-1">Claim mode</div>
                <p class="text-sm text-slate-300">{claimMeta.assertion}</p>
                <p class="text-[11px] text-slate-500 mt-1">
                  The threads below were planned from this claim — edit them freely.
                  The claim is stored with the run and the Claim-verdict tab auto-runs when it finishes.
                </p>
              </div>
              <button
                class="text-xs text-slate-400 hover:text-red-300 whitespace-nowrap"
                title="Keep the threads but launch as a regular (non-claim) investigation"
                onclick={() => (claimMeta = null)}
              >
                × disable claim mode
              </button>
            </div>
          {:else}
            <div class="text-xs text-slate-500 uppercase tracking-wider mb-2">
              Seed threads from a claim (optional)
            </div>
            <div class="flex gap-2">
              <input
                class="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-slate-200 placeholder-slate-600"
                placeholder="e.g. Company X supplies country Y with surveillance technology"
                bind:value={claimText}
                onkeydown={(e) => e.key === "Enter" && seedFromClaim()}
              />
              <button
                class="px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 disabled:opacity-40 whitespace-nowrap"
                disabled={!claimText.trim() || planningClaim}
                onclick={seedFromClaim}
              >
                {planningClaim ? "Planning…" : "Plan support/refute threads"}
              </button>
            </div>
            {#if claimPlanError}
              <div class="text-xs text-red-400 mt-2">{claimPlanError}</div>
            {/if}
            <p class="text-[11px] text-slate-500 mt-2">
              Replaces the threads below with balanced supporting + refuting searches and
              enables the claim verdict. Each thread is a full run (~30–40 min at default depth) —
              delete some, or lower the Advanced knobs, for a faster investigation.
            </p>
          {/if}
        </div>
      {/if}

      <div class="space-y-3">
        {#each threads as t, i}
          <div class="rounded-lg border border-slate-800 bg-slate-900 p-3">
            <div class="flex items-center gap-2 mb-2">
              <span class="text-xs text-slate-500 w-16">Thread {i + 1}</span>
              <input
                class="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-slate-300 mono placeholder-slate-600"
                placeholder="auto_name (optional)"
                bind:value={t.name}
              />
              {#if kind === "multi" && threads.length > 2}
                <button class="text-slate-600 hover:text-red-400 px-1" onclick={() => removeThread(i)}>×</button>
              {/if}
            </div>
            <input
              class="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1.5 text-sm text-slate-200 placeholder-slate-600"
              placeholder="GNews search string, e.g. Russia oil sanctions evasion dark fleet 2026"
              bind:value={t.query}
              onblur={() => onQueryBlur(i)}
            />
            <div class="flex items-center gap-2 mt-1.5">
              <button
                class="text-[11px] text-sky-400 hover:text-sky-300 disabled:opacity-40 disabled:cursor-default flex items-center gap-1"
                disabled={!t.query.trim() || refining[i]}
                title="Rewrite this query to target the selected domain (you can edit the result)"
                onclick={() => refineThread(i)}
              >
                {#if refining[i]}
                  <span class="animate-spin inline-block">◠</span> refining…
                {:else}
                  ✦ Refine for {selectedDomain?.name ?? "domain"}
                {/if}
              </button>
              {#if refineError[i]}
                <span class="text-[11px] text-red-400">{refineError[i]}</span>
              {/if}
            </div>
          </div>
        {/each}
      </div>

      {#if kind === "multi"}
        <button class="mt-3 text-xs text-emerald-400 hover:underline" onclick={addThread}>+ Add thread</button>
      {/if}

      <!-- Sources -->
      <div class="mt-5 rounded-lg border border-slate-800 bg-slate-900 p-4">
        <div class="text-xs text-slate-500 uppercase tracking-wider mb-2">Sources</div>
        <label class="flex items-center gap-2 text-sm text-slate-200 cursor-pointer">
          <input type="checkbox" class="accent-emerald-500" bind:checked={gnewsEnabled} />
          Google News
        </label>
        {#each searchSources as s}
          <label class="flex items-center gap-2 text-sm cursor-pointer mt-1
                        {s.available ? 'text-slate-200' : 'text-slate-500'}"
                 title={s.available ? s.description : `${s.description} (needs configuration)`}>
            <input type="checkbox" class="accent-emerald-500"
              disabled={!s.available}
              checked={enabledSources.has(s.id)}
              onchange={() => toggleSource(s.id)} />
            {s.label}
            <span class="text-[11px] text-slate-500">— {s.description}</span>
            {#if !s.available}<span class="text-[11px] text-amber-500/80">needs key</span>{/if}
          </label>
        {/each}
        <p class="text-[11px] text-slate-500 mt-2">
          Add your own documents to analyse alongside (or instead of) news.
          Uploaded PDFs and URLs are always included — they skip the relevance
          cutoff but are still scored against the domain hypothesis. The query
          above still frames the analysis.
        </p>

        <!-- URLs -->
        <div class="mt-3">
          <span class="text-xs text-slate-500">URLs (one per line)</span>
          <textarea
            class="mt-1 w-full h-20 bg-slate-800 border border-slate-700 rounded p-2 text-xs text-slate-200 mono placeholder-slate-600"
            placeholder="https://example.com/report&#10;https://example.org/filing"
            bind:value={urlsText}
          ></textarea>
        </div>

        <!-- PDFs -->
        <div class="mt-2">
          <label class="text-xs text-sky-400 hover:text-sky-300 cursor-pointer inline-flex items-center gap-1">
            {#if uploading}
              <span class="animate-spin inline-block">◠</span> Uploading…
            {:else}
              + Upload PDF
            {/if}
            <input type="file" accept="application/pdf" multiple class="hidden" onchange={onPickPdfs} />
          </label>
          {#each uploadedPdfs as p}
            <div class="flex items-center justify-between text-xs text-slate-300 mt-1 rounded bg-slate-800 px-2 py-1">
              <span class="mono truncate">{p.name}</span>
              <button class="text-slate-500 hover:text-red-400 ml-2" onclick={() => removePdf(p.id)}>remove</button>
            </div>
          {/each}
        </div>

        {#if !gnewsEnabled && usableUrls().length === 0 && uploadedPdfs.length === 0}
          <div class="text-[11px] text-amber-300 mt-2">
            GNews is off — add at least one URL or PDF to run.
          </div>
        {/if}
        {#if sourceError}
          <div class="text-[11px] text-red-400 mt-1">{sourceError}</div>
        {/if}
      </div>

      <!-- Advanced -->
      <div class="mt-5">
        <button
          class="text-xs text-slate-400 hover:text-slate-200"
          onclick={() => (showAdvanced = !showAdvanced)}
        >
          {showAdvanced ? "▾" : "▸"} Advanced
        </button>
        {#if showAdvanced}
          <AdvancedSettings
            bind:period
            bind:stage1Articles
            bind:stage2ArticlesPerEntity
            bind:topNEntities
            bind:enhancedRetrieval
            bind:retrievalDepth
            bind:retrievalExpansions
          />
        {/if}
      </div>

      {#if kind === "multi"}
        <div class="mt-4 rounded-lg border border-amber-700/40 bg-amber-900/10 p-3 text-xs text-amber-100/70">
          A cross-event run typically takes 60–90 minutes. It executes
          server-side — you can close this tab and return to the investigation
          from the sidebar.
        </div>
      {/if}
      {/if}

      {#if error}
        <div class="mt-4 text-sm text-red-400">{error}</div>
      {/if}
    {/if}
  </div>
</div>

<!-- Footer nav -->
<div class="border-t border-slate-800 bg-slate-900 px-6 py-3 flex items-center justify-between">
  <button
    class="text-sm text-slate-400 hover:text-slate-200 disabled:opacity-30"
    disabled={step === 1}
    onclick={() => (step = Math.max(1, step - 1))}
  >
    ‹ Back
  </button>
  <div class="flex items-center gap-2">
    {#if step < 3}
      <button
        class="bg-emerald-600 hover:bg-emerald-500 text-emerald-50 text-sm rounded-md px-4 py-1.5 disabled:opacity-40"
        disabled={(step === 1 && !validStep1()) || (step === 2 && !validStep2())}
        onclick={() => (step += 1)}
      >
        Next ›
      </button>
    {:else if review === null}
      <button
        class="bg-emerald-600 hover:bg-emerald-500 text-emerald-50 text-sm rounded-md px-4 py-1.5 disabled:opacity-40 flex items-center gap-1"
        disabled={!validStep3() || autoRefining}
        onclick={startReview}
      >
        {#if autoRefining}
          <span class="animate-spin inline-block">◠</span> Refining queries…
        {:else}
          ✦ Review &amp; launch ›
        {/if}
      </button>
    {:else}
      <button
        class="bg-emerald-600 hover:bg-emerald-500 text-emerald-50 text-sm rounded-md px-4 py-1.5 disabled:opacity-40"
        disabled={submitting}
        onclick={finalLaunch}
      >
        {submitting ? "Launching…" : "Launch investigation"}
      </button>
    {/if}
  </div>
</div>
