// TypeScript types for the responses in docs/UI_API.md.
// These are intentionally minimal and additive: every response is allowed
// to carry extra fields the UI ignores; missing optional fields are
// handled by the components.

export type Thread = { name: string; query: string };

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

export type ConnectorNode = GraphNode & { role: "selected" | "connector" };

export interface ConnectorResult {
  nodes: ConnectorNode[];
  edges: GraphEdge[];
  selected: string[];
  connectors: string[];
  missing: string[];
  paths?: Array<{ from: string; to: string; path: string[]; hops: number }>;
  unreachablePairs: string[][];
  stats: {
    selectedCount: number;
    connectorCount: number;
    edgeCount: number;
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
