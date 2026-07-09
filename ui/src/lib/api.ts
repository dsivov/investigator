// Thin fetch wrapper around the Flask backend defined in
// ui/server.py and documented in docs/UI_API.md.
//
// All paths start with /api so the Vite dev proxy and a same-origin
// production deployment both work without changes.

import type {
  InvestigationRow,
  InvestigationFull,
  GraphPayload,
  TmfgPayload,
  Domain,
  SourcesPayload,
  ConnectorResult,
  OpenRegistryStatus,
  EnrichmentPayload,
  KbStats,
  KbConflicts,
  KbResult,
  MonitorWatchlist,
  MonitorDigest,
  MonitorRule,
  MonitorPatternMatch,
} from "./types";

async function post<T>(path: string): Promise<T> {
  const r = await fetch(path, { method: "POST" });
  if (!r.ok) {
    const b = await r.json().catch(() => ({}));
    throw new Error((b as any)?.message || `HTTP ${r.status}`);
  }
  return r.json();
}

const BASE = "";

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`);
  if (!r.ok) {
    let body: any = null;
    try {
      body = await r.json();
    } catch {}
    const msg = body?.message || `HTTP ${r.status} for ${path}`;
    const err: any = new Error(msg);
    err.code = body?.code;
    err.field = body?.field;
    throw err;
  }
  return r.json();
}

export interface ClaimEvidence {
  source: string;
  title: string;
  url: string;
  confidence: number;
  quote: string;
}
export interface ClaimVerdict {
  claim: string;
  assertion?: string;
  verdict: string;
  net: number;
  tempered_net: number;
  queries?: { support: string[]; refute: string[] };
  counts: {
    snippets: number;
    supports: number;
    refutes: number;
    neutral: number;
    support_sources: number;
    refute_sources: number;
  };
  support: ClaimEvidence[];
  refute: ClaimEvidence[];
}

export const api = {
  health: () => get<{ status: string }>("/api/health"),

  listInvestigations: () =>
    get<{ items: InvestigationRow[]; total: number }>("/api/investigations"),
  getInvestigation: (id: string) =>
    get<InvestigationFull>(`/api/investigations/${id}`),
  getGraph: (id: string) =>
    get<GraphPayload>(`/api/investigations/${id}/graph`),
  getTmfg: (id: string) =>
    get<TmfgPayload>(`/api/investigations/${id}/tmfg`),
  getSources: (id: string) =>
    get<SourcesPayload>(`/api/investigations/${id}/sources`),

  // Auto "key network": hidden-connections subgraph seeded with theme+bridge nodes.
  getKeyNetwork: (id: string) =>
    get<ConnectorResult>(`/api/investigations/${id}/key-network`),

  // External-records enrichment (SEC EDGAR + OpenRegistry) on company entities.
  getEnrichment: (id: string) =>
    get<EnrichmentPayload>(`/api/investigations/${id}/enrichment`),
  enrichInvestigation: async (id: string, topN = 12): Promise<EnrichmentPayload> => {
    const r = await fetch(`/api/investigations/${id}/enrich`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ topN }),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error((b as any)?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  // Connector subgraph between selected entities/events (shortest-path union).
  connect: async (
    id: string,
    entities: string[],
    mode: "shortest_path" | "hidden" | "induced" = "shortest_path"
  ): Promise<ConnectorResult> => {
    const r = await fetch(`/api/investigations/${id}/connect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entities, mode }),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error(b?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  // LLM summary of how the selected entities interconnect (connected nodes only).
  analyzeConnections: async (
    id: string,
    entities: string[],
    mode: "shortest_path" | "hidden" | "induced" = "shortest_path"
  ): Promise<{ report: string; connected?: number; message?: string }> => {
    const r = await fetch(`/api/investigations/${id}/connect/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ entities, mode }),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error(b?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  // Claim verification (additive; independent of the investigation pipeline).
  claimVerify: async (claim: string, entities?: string[]): Promise<ClaimVerdict> => {
    const r = await fetch("/api/claim-verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ claim, entities }),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error((b as any)?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  // LLM storyline narration of one Louvain community.
  analyzeCommunity: async (id: string, community: number): Promise<{ report: string; size: number; edges: number }> => {
    const r = await fetch(`/api/investigations/${id}/community/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ community }),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error((b as any)?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  // Plan-only claim expansion: assertion + the support/refute queries a claim
  // would seed, without any retrieval. Used to pre-fill editable wizard threads.
  claimPlan: async (claim: string): Promise<{ assertion: string; support: string[]; refute: string[] }> => {
    const r = await fetch("/api/claim-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ claim }),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error((b as any)?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  // Whole-investigation claim verdict over the graph's evidence.
  claimVerdict: async (id: string, claim?: string): Promise<ClaimVerdict> => {
    const q = claim ? `?claim=${encodeURIComponent(claim)}` : "";
    const r = await fetch(`/api/investigations/${id}/claim-verdict${q}`);
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error((b as any)?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  artifactUrl: (id: string, name: string) =>
    `/api/investigations/${id}/artifacts/${name}`,
  logUrl: (id: string) => `/api/investigations/${id}/log`,
  fetchArtifactText: async (id: string, name: string) => {
    const r = await fetch(`/api/investigations/${id}/artifacts/${name}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.text();
  },

  listDomains: () =>
    get<{ items: Domain[]; total: number }>("/api/domains"),

  // Configurable search sources (Wikipedia / GDELT / OpenSanctions / web).
  listSearchSources: () =>
    get<{ items: Array<{ id: string; label: string; description: string; requiresKey: boolean; available: boolean }> }>(
      "/api/search-sources"),

  // Cumulative cross-investigation knowledge base (LightRAG).
  kbStats: () => get<KbStats>("/api/kb/stats"),
  kbConflicts: () => get<KbConflicts>("/api/kb/conflicts"),

  // Standing monitor (CEP): watchlist, on-demand run, dated impact digests.
  monitorWatchlist: () => get<MonitorWatchlist>("/api/monitor/watchlist"),
  monitorEditWatchlist: async (
    body: { add?: string[]; remove?: string[]; domain?: string },
  ): Promise<MonitorWatchlist> => {
    const r = await fetch("/api/monitor/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({})) as any)?.message || `HTTP ${r.status}`);
    return r.json();
  },
  monitorRun: async (k = 8, period = "1d"): Promise<{ running: boolean; message: string }> => {
    const r = await fetch("/api/monitor/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ k, period }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({})) as any)?.message || `HTTP ${r.status}`);
    return r.json();
  },
  monitorDigests: () => get<{ dates: string[]; running: boolean }>("/api/monitor/digests"),
  monitorDigest: (date: string) => get<MonitorDigest>(`/api/monitor/digests/${date}`),
  monitorRules: () => get<{ rules: MonitorRule[] }>("/api/monitor/rules"),
  monitorEditRules: async (
    body: { add?: MonitorRule; remove?: string; rules?: MonitorRule[] },
  ): Promise<{ rules: MonitorRule[] }> => {
    const r = await fetch("/api/monitor/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({})) as any)?.message || `HTTP ${r.status}`);
    return r.json();
  },
  monitorPatterns: () =>
    get<{ matches: MonitorPatternMatch[]; count: number }>("/api/monitor/patterns"),
  // mode omitted -> backend picks per-endpoint defaults (entities=hybrid, answer=global).
  kbQuery: async (
    query: string,
    synthesize = true,
    mode?: "local" | "global" | "hybrid" | "mix",
    asOf?: string
  ): Promise<KbResult> => {
    const body: Record<string, unknown> = { query, synthesize };
    if (mode) body.mode = mode;
    if (asOf) body.asOf = asOf;
    const r = await fetch("/api/kb/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error((b as any)?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  // Integrations: OpenRegistry company-registry login (one-time browser OAuth).
  getOpenRegistry: () =>
    get<OpenRegistryStatus>("/api/integrations/openregistry"),
  openRegistryLogin: () =>
    post<OpenRegistryStatus>("/api/integrations/openregistry/login"),
  openRegistryLogout: () =>
    post<OpenRegistryStatus>("/api/integrations/openregistry/logout"),
  openRegistryComplete: async (redirectUrl: string) => {
    const r = await fetch("/api/integrations/openregistry/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ redirectUrl }),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error((b as any)?.message || `HTTP ${r.status}`);
    }
    return r.json() as Promise<OpenRegistryStatus>;
  },

  refineQuery: async (
    query: string,
    domain: string,
    hypothesisOverride?: string
  ): Promise<{ refined: string }> => {
    const r = await fetch("/api/refine-query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, domain, hypothesisOverride }),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error(b?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  // Upload one or more PDFs as manual sources. Returns the stored ids to
  // pass back in createInvestigation's extraSources.pdfs.
  uploadSources: async (
    files: File[]
  ): Promise<{ items: { id: string; name: string; bytes: number }[] }> => {
    const form = new FormData();
    for (const f of files) form.append("files", f);
    const r = await fetch("/api/uploads", { method: "POST", body: form });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error(b?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  createInvestigation: async (body: unknown): Promise<{ id: string; status: string }> => {
    const r = await fetch("/api/investigations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const b = await r.json().catch(() => ({}));
      throw new Error(b?.message || `HTTP ${r.status}`);
    }
    return r.json();
  },

  // Stop a running/queued investigation without deleting its artifacts.
  stopInvestigation: (id: string) =>
    fetch(`/api/investigations/${id}/stop`, { method: "POST" }),

  // Delete an investigation. Stops it first if it is still running, then
  // removes the raw artifact + all derivative reports and job records.
  deleteInvestigation: (id: string) =>
    fetch(`/api/investigations/${id}`, { method: "DELETE" }),

  // Server-Sent Events for live progress. Returns the EventSource so the
  // caller can close() it when the component unmounts.
  streamInvestigation: (id: string) =>
    new EventSource(`/api/investigations/${id}/stream`),
};
