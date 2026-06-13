<script lang="ts">
  import { api } from "../../lib/api";
  import type { InvestigationFull } from "../../lib/types";

  let { id, inv }: { id: string; inv: InvestigationFull } = $props();
  let html = $state<string>("");
  let toc = $state<Array<{ text: string; id: string }>>([]);
  let error = $state<string | null>(null);

  $effect(() => {
    Promise.all([
      api.fetchArtifactText(id, "customer_report.md"),
      import("marked"),
    ])
      .then(([md, { marked }]) => {
        marked.setOptions({ headerIds: false, mangle: false } as any);
        const raw = marked.parse(md) as string;
        // Slug h2 ids for the TOC
        const parser = new DOMParser();
        const doc = parser.parseFromString(raw, "text/html");
        const tocItems: Array<{ text: string; id: string }> = [];
        doc.querySelectorAll("h2").forEach((h, i) => {
          const slug = `h-${i}`;
          h.id = slug;
          tocItems.push({ text: h.textContent || `Section ${i + 1}`, id: slug });
        });
        html = doc.body.innerHTML;
        toc = tocItems;
      })
      .catch((e) => {
        error = e.message;
      });
  });

  function downloadMd() {
    window.open(api.artifactUrl(id, "customer_report.md"));
  }
</script>

<div class="flex-1 flex min-h-0">
  <aside class="w-64 flex-shrink-0 border-r border-slate-800 bg-slate-900 overflow-y-auto scrollbar p-4 text-sm">
    <div class="flex items-center justify-between mb-2">
      <div class="text-slate-500 text-xs uppercase tracking-wider">Contents</div>
      <button
        class="text-xs text-emerald-400 hover:underline"
        onclick={downloadMd}
        title="Download Markdown">⤓ md</button
      >
    </div>
    <ul class="space-y-1">
      {#each toc as t}
        <li>
          <a
            href={`#${t.id}`}
            class="block text-xs text-slate-400 hover:text-emerald-400 py-1 border-l-2 border-transparent hover:border-emerald-400 pl-3"
            onclick={(e) => {
              e.preventDefault();
              const el = document.getElementById(t.id);
              if (el) el.scrollIntoView({ behavior: "smooth" });
            }}
          >{t.text}</a>
        </li>
      {/each}
    </ul>
  </aside>
  <article class="report-prose flex-1 overflow-y-auto scrollbar p-8 max-w-4xl">
    {#if error}
      <div class="text-red-400 text-sm">{error}</div>
    {:else}
      {@html html}
    {/if}
  </article>
</div>
