import type { MessageInput, Question } from "../api/types";
import { ApiError } from "../api/client";

export function newMessage(
  session: string,
  text: string,
  hint: Question | null,
): MessageInput {
  return {
    channel: "browser",
    session_id: session,
    message_id: crypto.randomUUID(),
    raw_text: text,
    occurred_at: new Date().toISOString(),
    question_hint: hint?.question_id ?? null,
    expected_question_version: hint?.version ?? null,
  };
}
export function uncertain(error: unknown): boolean {
  return !(
    error instanceof ApiError &&
    [400, 403, 404, 409, 422].includes(error.status)
  );
}
