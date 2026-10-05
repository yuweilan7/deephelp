export interface Execution {
  mode: string;
  business_source: string;
  business_maintenance: boolean;
  expected_calls_per_message: number | string;
  remaining_calls: number;
  max_tokens: number;
  charged_tokens: number;
  request_timeout_seconds: number;
}
export interface Workspace {
  csrf: string;
  identity: { tenant_id: string; user_id: string };
  execution: Execution;
}
export interface MessageInput {
  channel: "browser";
  session_id: string;
  message_id: string;
  raw_text: string;
  occurred_at: string;
  question_hint: string | null;
  expected_question_version: number | null;
}
export interface Question {
  question_id: string;
  version: number;
  status: string;
}
export interface RunResponse {
  run_id: string | null;
  trace_id: string;
  outcome: string;
  reply: string;
  replayed: boolean;
  facts: { name: string; value: unknown }[];
  evidence_refs: { evidence_id?: string; [key: string]: unknown }[];
  stages: {
    stage: string;
    status: string;
    elapsed_ms: number;
    model_calls: number;
    tool_calls: number;
  }[];
  call_counts: {
    model_calls: number;
    retrieval_calls: number;
    tool_calls: number;
    intent_calls: number;
  };
  budget_used: unknown;
  versions: unknown;
}
export interface HistoryMessage {
  input: MessageInput;
  run_id: string;
  status: string;
  question: Question;
  response: RunResponse | null;
}
export interface SessionSummary {
  session_id: string;
  message_count: number;
  updated_at: string;
}
export interface Page<T> {
  items: T[];
  next_offset: number | null;
}
export interface Flow {
  run_id: string;
  trace_id: string;
  data: { response?: RunResponse; [key: string]: unknown };
  incomplete?: string[];
  dropped_events?: number;
}
