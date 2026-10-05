export interface Task {
  id: string;
  state: string;
  attempt: number;
  options: { action: string };
  result?: { evidence_status?: string };
  partial_evidence?: { summary?: { status?: string } };
  error?: string;
}
export interface Catalog {
  reports: string[];
  platform_imports: string[];
  platform_review_enabled: boolean;
  prompt: { name: string; version: number; request_digest: string } | null;
  capture_sources: string[];
  candidates: { id: string; revision: number; text: string }[];
  assets: string[];
  tasks: Task[];
  releases: { hash: string; version: string; state: string }[];
  pointer: {
    channel: string;
    revision: number;
    active: string | null;
    previous: string | null;
  };
  dataset: {
    cases: number;
    turns: number;
    independent_cases: number;
    declared_source_groups: number;
    split_counts: unknown;
  };
}
export interface Plan {
  offline: { conversations: number; messages: number; modes: number };
  build: {
    base_corpus_rows: number;
    max_candidates: number;
    embedding_calls_before_retry: number[];
    embedding_input_tokens_estimate: number[];
  };
  validate: {
    conversations: number;
    messages: number;
    remote_calls_estimate: number[];
    per_message_tokens_estimate: { input: number[]; output: number[] };
  };
  timeout_seconds: number;
  max_task_attempts: number;
  concurrency: number;
}
export interface Report {
  summary: {
    status: string;
    live?: boolean;
    dataset?: unknown;
    selected_case_ids?: string[];
  };
  error_count: number;
  errors: { mode: string; case_id: string; text: string; score: unknown }[];
  metrics: Record<string, unknown>;
  next_offset: number | null;
}
export interface Candidate {
  candidate_id: string;
  revision: number;
  text: string;
  reviews: unknown;
}
export interface PlatformEvidence {
  candidate_id: string;
  revision: number;
  status: string;
  url?: string;
  scores: { id: string; stringValue: string; comment: string }[];
}
export interface HistoricalImport {
  state: { status: string; dataset_url?: string; experiment_url?: string };
  expected_rows: number;
  summary: {
    selected_case_ids: string[];
    selected_modes: string[];
    source_report_sha256: string;
    release_gate: { accepted: boolean };
  };
}
export function canStart(tasks: Task[]): boolean {
  return !tasks.some((t) => ["RUNNING", "CANCELLING"].includes(t.state));
}
export function canCancel(task: Task | null): boolean {
  return !!task && ["RUNNING", "CANCELLING"].includes(task.state);
}
export function canResume(task: Task | null, maxAttempts: number): boolean {
  return (
    !!task &&
    ["CANCELLED", "INTERRUPTED", "FAILED"].includes(task.state) &&
    task.attempt < maxAttempts
  );
}
export function lines(text: string): string[] {
  return text
    .split("\n")
    .map((t) => t.trim())
    .filter(Boolean);
}
