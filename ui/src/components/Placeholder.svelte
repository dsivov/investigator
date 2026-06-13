<script lang="ts">
  let { title, body }: { title: string; body: string } = $props();
</script>

<div class="flex-1 p-10 max-w-2xl">
  <h1 class="text-xl font-semibold text-slate-200">{title}</h1>
  <p class="mt-3 text-sm text-slate-400 leading-relaxed">{body}</p>
  <div class="mt-6 rounded-lg border border-slate-800 bg-slate-900 p-4 text-xs text-slate-500">
    This screen is intentionally stubbed in the scaffold. The pattern for
    extending it is documented in <code class="mono text-slate-300">ui/README.md</code>.
  </div>
</div>
