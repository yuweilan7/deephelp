import type {
  MessageInput,
  Question,
  RunResponse,
  AgentReceipt,
} from "./types";
export interface EntityObservation {
  name: string;
  value: string;
  start: number;
  end: number;
  disposition: string;
  layer: string;
  source: { message_id: string; excerpt: string };
}
export interface Explanation {
  agent_receipts: AgentReceipt[] | null;
  schema_version: string;
  run_id: string;
  trace_id: string | null;
  diagnostic_trace_id: string | null;
  status: string;
  source: string;
  input: MessageInput;
  summary: string;
  receipt: RunResponse | null;
  question_now: Question;
  question_at_run: Question | null;
  entities: { observations: EntityObservation[]; conflicts: unknown[] } | null;
  event: {
    disposition: string;
    judgement?: {
      relation: string;
      decision: string;
      citations: { message_id: string; quote: string }[];
    } | null;
    diagnostics: string[];
    edges: { reason: string; accepted: boolean }[];
    candidates: { question_id: string; excluded_reason: string | null }[];
    target_question_id?: string;
  } | null;
  decision: {
    final_code: string | null;
    reason_code: string;
    decision: string;
    cascade_steps: {
      layer: string;
      action: string;
      reason: string;
      model_calls: number;
      retrieval_calls: number;
    }[];
  } | null;
  stages: (RunResponse["stages"][number] & {
    title: string;
    purpose: string;
    started_at: string | null;
    finished_at: string | null;
    error_code: string | null;
  })[];
  tools:
    | {
        call_id: string;
        tool_name?: string;
        name?: string;
        error_code?: string;
        result?: { status: string };
        parameters?: unknown;
        [key: string]: unknown;
      }[]
    | null;
  sop_nodes: unknown[] | null;
  gaps: string[];
  boundaries: string[];
}
export function explanationPath(run: string, session: string) {
  return `/history/runs/${encodeURIComponent(run)}/explanation?session_id=${encodeURIComponent(session)}`;
}
