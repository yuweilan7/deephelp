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
        raw_dense?: number | null;
        raw_sparse?: number | null;
        fusion_score?: number | null;
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
  window: {
    generation: number;
    questions?: {
      question_id: string;
      version: number;
      status: string;
      entities?: {
        name: string;
        value: string;
        source: { message_id: string };
      }[];
    }[];
  };
  redis: {
    status: string;
    ttl_seconds?: number;
    matches_authority?: boolean | null;
  };
  outbox: {
    event_id: number;
    question_id: string;
    version: number;
    done: boolean;
    attempts: number;
    last_error: string | null;
  }[];
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
  checkpoints: {
    id: string;
    parent: unknown;
    pending_writes: { task: string; channel: string }[];
    metadata: unknown;
  }[];
  next: string[];
}
export interface Observation {
  source: string;
  status: string;
  total: number;
  next_cursor: number | null;
  langfuse_url?: string;
  grafana_url?: string;
  spans: {
    type: string;
    model?: string;
    usage?: unknown;
    version?: string;
    span_id: string;
    trace_id: string;
    parent_span_id: string | null;
    service: string;
    name: string;
    start_unix_ns: string;
    duration_ms: number | null;
    attributes: Record<string, unknown>;
    input: unknown;
    output: unknown;
    incomplete: boolean;
  }[];
  associations: {
    trace_id: string;
    span_id: string;
    kind: string;
    predecessor_trace_id: string | null;
    predecessor_span_id: string | null;
  }[];
  explanation?: {
    unique_generations: number;
    duplicates_removed: number;
    conflicting_observations: number;
    known_usage_attempts: number;
    unknown_usage_attempts: number;
    known_token_subtotal: number;
    actual_total_tokens: number | null;
    trace_extents: {
      trace_id: string;
      observed_extent_ms: number | null;
      complete_intervals: boolean;
    }[];
    notes: string[];
  };
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
