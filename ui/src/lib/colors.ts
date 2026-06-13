// Palettes shared across components. Keep small and stable so the same
// thread always has the same colour everywhere in the UI.

export const THREAD_PALETTE = [
  "#3b82f6",
  "#ef4444",
  "#f59e0b",
  "#a855f7",
  "#10b981",
  "#06b6d4",
  "#ec4899",
];

export const POLYGON_PALETTE = [
  "#10b981",
  "#ef4444",
  "#3b82f6",
  "#f59e0b",
  "#a855f7",
  "#06b6d4",
  "#ec4899",
  "#f43f5e",
  "#84cc16",
  "#0ea5e9",
  "#fb7185",
  "#22c55e",
];

export const ETYPE_COLOR: Record<string, string> = {
  affiliation: "#475569",
  event_participation: "#b45309",
  event_followed_by: "#7e22ce",
  event_coincident: "#7e22ce",
  claimed_caused_by: "#dc2626",
};

export function threadColourMap(runs: string[]): Record<string, string> {
  const out: Record<string, string> = {};
  runs.forEach((r, i) => (out[r] = THREAD_PALETTE[i % THREAD_PALETTE.length]));
  return out;
}

export function confidencePillClass(confidence: string): string {
  if (confidence === "Almost certain") return "bg-emerald-900/50 text-emerald-300";
  if (confidence === "Highly likely") return "bg-emerald-900/30 text-emerald-300";
  if (confidence === "Likely") return "bg-blue-900/40 text-blue-300";
  if (confidence === "Even chance") return "bg-slate-700/60 text-slate-300";
  return "bg-slate-800 text-slate-400";
}
