// TypeScript types for the responses in docs/UI_API.md.
// These are intentionally minimal and additive: every response is allowed
// to carry extra fields the UI ignores; missing optional fields are
// handled by the components.

export type Thread = { name: string; query: string };

export interface KbStats {
  available: boolean;
  store: string;
  entities: number;
  edges: number;
  canonicals: number;
}

export interface KbResult {
  query: string;
  mode: string;
  answer: string | null;
  entities: Array<{ name: string; type: string; description: string }>;
  relationships: Array<{ src: string; dst: string; description: string }>;
}

export interface InvestigationSummary {
  fetched?: number;
  extracted_full_body?: number;
  extracted_headline_only?: number;
  nodes?: number;
  edges?: number;
  bridges?: number;
  bridges_all_threads?: number;
  themes?: number;
  cross_event_themes?: number;
  leads?: number;
  asymmetric_corpus?: boolean;
  sparse_threads?: string[];
  threads?: number;
}

export interface InvestigationRow {
  id: string;
  title: string;
  kind?: "single" | "multi";
  status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  createdAt?: string;
  finishedAt?: string | null;
  summary: InvestigationSummary;
}

export interface InvestigationFull extends InvestigationRow {
  domain: string;
  period: string;
  threads: Thread[];
  params: Record<string, unknown>;
  artifacts: Record<string, string | null>;
}

export interface EvidenceRecord {
  reasoning: string;
  quotes: string[];
  source: string;
  strength: number;
  confidence: number;
  supports: boolean;
  // Claim-level corroboration of THIS evidence's claim (independent sources
  // confirming the same fact). weak = 1, moderate = 2, strong = 3+.
  corroboration: "weak" | "moderate" | "strong";
  corroborationSources: number;
}

export interface GraphNode {
  id: string;
  label: string;
  type: "entity" | "event";
  runs: string[];
  isBridge: boolean;
  labels: string[];
  evidenceCount: number;
  corroboration: "weak" | "moderate" | "strong";
  corroborationSources: number;
  corroboratedClaim: string;
  corroboratedClaims: number;
  posterior: number;
  score: number;
  data: Record<string, unknown>;
  evidence?: EvidenceRecord[];
}

export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  structural?: boolean;
  rtype: string;
  context: string;
  url: string;
  publisher: string;
}

export interface GraphPayload {
  title: string;
  runs: string[];
  domain: string;
  period: string;
  bridges: Array<{ id: string; runs: string[]; posterior: number; score: number }>;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface EnrichmentItem {
  id: string;
  enrichment: {
    edgar?: {
      matched_name?: string;
      ticker?: string;
      cik?: number;
      sic_description?: string;
      recent_filings?: Array<{ form: string; date: string }>;
      _provenance?: { url?: string };
    };
    openregistry?: {
      matched_name?: string;
      company_id?: string;
      jurisdiction?: string;
      status?: string;
      beneficial_owners?: unknown;
      _provenance?: { url?: string };
    };
  };
}

export interface EnrichmentPayload {
  items?: EnrichmentItem[];
  total?: number;
  running: boolean;
  hasEnriched: boolean;
  recordCount: number;
  message?: string;
}

export interface OpenRegistryStatus {
  provider: string;
  url: string;
  connected: boolean;
  method: "static_token" | "oauth" | "none";
  loginInProgress: boolean;
  authorizeUrl: string;
  message?: string;
  removed?: boolean;
}

export type ConnectorNode = GraphNode & {
  role: "selected" | "connector";
  betweenness: number;
  isBroker: boolean;
};

export interface ConnectorResult {
  nodes: ConnectorNode[];
  edges: GraphEdge[];
  selected: string[];
  connectors: string[];
  brokers: string[];
  missing: string[];
  paths?: Array<{ from: string; to: string; path: string[]; hops: number }>;
  unreachablePairs: string[][];
  stats: {
    selectedCount: number;
    connectorCount: number;
    brokerCount?: number;
    edgeCount: number;
    pathCount?: number;
    unreachablePairs: number;
  };
}

export interface ThemePayload {
  idx: number;
  members: string[];
  weight: number;
  runs: string[];
  isCross: boolean;
  urls: string[];
}

export interface TmfgPayload {
  title: string;
  runs: string[];
  domain: string;
  period: string;
  themes: ThemePayload[];
  nodes: GraphNode[];
  edges: Array<{
    source: string;
    target: string;
    kind: "attested" | "fillin";
    type: string;
    rtype?: string;
    context?: string;
    url?: string;
  }>;
  bridges: string[];
}

export interface Domain {
  id: string;
  key: string;
  name: string;
  isPreset: boolean;
  hypothesis: string;
  threshold: number;
  description: string;
}

export interface SourcesPayload {
  publisherCount: number;
  topConcentration: number;
  publishers: Array<{
    publisher: string;
    count: number;
    urls: Array<{ url: string; backsEntity: string; backsEdgeType: string }>;
  }>;
}
