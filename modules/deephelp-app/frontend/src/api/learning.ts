export interface LearningStatus {
  installed: boolean;
  demo_enabled: boolean;
  demo_identity_required: string;
}
export interface ProtocolDiscovery {
  tools: { name: string }[];
  initialize: unknown;
  source: string;
  model_calls: number;
}
export interface ProtocolResult {
  action: string;
  model_calls: number;
  rejected?: boolean;
  dispatched: boolean;
  boundary: string;
  error?: unknown;
  result?: {
    status: string;
    facts: { name: string; value: unknown }[];
    evidence_refs: unknown[];
  };
  trace_id: string;
}
export interface RulesResult {
  model_calls: number;
  observations: {
    start: number;
    end: number;
    source: { excerpt: string };
    name: string;
    disposition: string;
  }[];
  confirmed: unknown[];
  conflicts: unknown[];
  unresolved: unknown[];
}
export interface RetrievalResult {
  elapsed_ms: number;
  trace_id: string;
  receipt: { model_calls: number };
  routes: Record<
    string,
    {
      candidates: {
        rank: number;
        intent_code: string;
        evidence_doc_id: string;
        score_kind: string;
        raw_score: number;
      }[];
      hits: {
        doc_id: string;
        intent_code: string;
        content: string;
        raw_score: number;
        score_kind: string;
        rank: number;
      }[];
      rerank?: {
        status: string;
        reason: string | null;
        policy: {
          model: string;
          instruct: string | null;
          candidate_budget: number;
        };
        input_doc_ids: string[];
        elapsed_ms: number;
        result?: {
          usage: { total_tokens: number | null } | null;
          hits: { score: number; document: { doc_id: string } }[];
        } | null;
      };
    }
  >;
}
export interface MemoryResult {
  authority: string;
  window: { generation: number };
  redis: { status: string };
  outbox: unknown[];
}
export interface Operation {
  session_id: string;
  run_id: string;
  question_version: number;
  status: string;
  approval_status: string;
  dispatch_attempts: number;
  query_attempts: number;
  plan: {
    operation_id: string;
    parameters_hash: string;
    sop_version: string;
    snapshot_hash: string;
    [key: string]: unknown;
  };
}
export interface DemoResult {
  operation: Operation;
  effects: number;
  checkpoints: unknown[];
  next: string[];
}
export interface Observation {
  source: string;
  status: string;
  total: number;
  next_cursor: number | null;
  langfuse_url?: string;
  grafana_url?: string;
  spans: { type: string; model?: string; usage?: unknown; version?: string }[];
  associations: unknown[];
}
export function safeUrl(url?: string): string | undefined {
  if (!url) return;
  try {
    const parsed = new URL(url);
    return ["http:", "https:"].includes(parsed.protocol)
      ? parsed.href
      : undefined;
  } catch {
    return;
  }
}
