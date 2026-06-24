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
  KbResult,
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

  // Cumulative cross-investigation knowledge base (LightRAG).
  kbStats: () => get<KbStats>("/api/kb/stats"),
  kbQuery: async (
    query: string,
    mode: "local" | "global" | "hybrid" | "mix" = "hybrid",
    synthesize = true
  ): Promise<KbResult> => {
    const r = await fetch("/api/kb/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, mode, synthesize }),
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
