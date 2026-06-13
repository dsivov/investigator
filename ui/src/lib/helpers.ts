export function publisherOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function escapeHtml(s: unknown): string {
  return String(s).replace(/[&<>"]/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
  })[c]!);
}

export function bridgeConfidence(b: { runs: string[]; score: number }, totalThreads: number): string {
  const n = b.runs.length;
  const s = b.score;
  if (n >= totalThreads && s >= 0.5) return "Almost certain";
  if (n >= totalThreads) return "Highly likely";
  if (n >= 2 && s >= 0.4) return "Likely";
  if (n >= 2) return "Even chance";
  return "Unlikely";
}

export function formatRunLabel(run: string): string {
  return run.replace(/_/g, " ");
}
