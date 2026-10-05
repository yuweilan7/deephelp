export interface Execution {
  mode: string;
  business_source: string;
  business_maintenance: boolean;
  expected_calls_per_message: number | string;
  remaining_calls: number;
  max_tokens: number;
  charged_tokens: number;
  request_timeout_seconds: number;
  collaboration_available?: boolean;
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
  execution_mode?: "single" | "collaboration";
}
export interface Question {
  question_id: string;
  version: number;
  status: string;
}
export interface RunResponse {
  skill_receipts?: SkillReceipt[];
  agent_receipts?: AgentReceipt[];
  run_id: string | null;
  trace_id: string;
  outcome: string;
  reply: string;
  replayed: boolean;
  missing_slots?: string[];
  next_action?: string | null;
  error?: { code: string; message: string } | null;
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
export interface SkillReceipt {
  execution_id: string;
  parent_id: string | null;
  node_id: string;
  skill_id: string;
  version: string;
  digest: string;
  status: string;
  observation: { tool_name: string; call_id: string | null } | null;
  error: { code: string; message: string } | null;
}
export interface AgentReceipt {
  task: {
    task_id: string;
    parent_run_id: string;
    parent_question_id: string;
    question_version: number;
    coordinator: string;
    role_id: string;
    role_version: string;
    policy_digest: string;
    node_id: string;
    goal: string;
    slots: unknown[];
    allowed_tools: string[];
    budget_ref: string;
  };
  status: string;
  evidence_ids: string[];
  decisions: {
    phase: string;
    model: string;
    call_id: string;
    usage: unknown;
  }[];
  observation: { call_id: string | null; status: string } | null;
  started_at: string;
  finished_at: string;
  error: { code: string; message: string } | null;
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

export interface AgentTaskRecord {
  task: AgentReceipt["task"];
  state: string;
  phase: string;
  decisions: AgentReceipt["decisions"];
  observation: AgentReceipt["observation"];
  receipt: AgentReceipt | null;
  updated_at: string;
}
