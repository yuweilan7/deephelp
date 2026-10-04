"""Pure case summary, lifecycle and bounded history rules."""

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models import (
    CaseSummary,
    ErrorCode,
    LifecycleCommand,
    MemoryMessage,
    Question,
    QuestionStatus,
)

OPEN = {QuestionStatus.ACTIVE, QuestionStatus.WAITING_SLOT}
CLOSED = {QuestionStatus.RESOLVED, QuestionStatus.HANDED_OFF, QuestionStatus.CANCELLED}


def summary(question: Question) -> CaseSummary:
    # Derived, deterministic, cited state; no model prose or fabricated conclusion.
    slots = "; ".join(f"{e.name.value}={e.value}" for e in question.entities)
    return CaseSummary(
        question_id=question.question_id,
        version=question.version,
        status=question.status,
        text=(
            f"{question.status}; {question.event_summary}; {slots}"
            if question.event_summary
            else f"{question.active_intent or 'UNCLASSIFIED'}; {question.status}; {slots}"
        )[:2000],
        message_ids=question.member_message_ids,
    )


def validate_transition(question: Question, command: LifecycleCommand) -> None:
    if question.version != command.expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, "Question version changed", 409)
    expected = {
        QuestionStatus.RESOLVED: {"user_confirmation", "process_result"},
        QuestionStatus.CANCELLED: {"operator_cancel"},
        QuestionStatus.HANDED_OFF: {"operator_handoff"},
        QuestionStatus.WAITING_SLOT: {"slot_check"},
        QuestionStatus.ACTIVE: {"explicit_reopen"} if question.status in CLOSED else {"slot_check"},
    }
    if (
        command.target == question.status
        or command.evidence_source not in expected.get(command.target, set())
        or (question.status in CLOSED and command.target != QuestionStatus.ACTIVE)
    ):
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Lifecycle transition/evidence rejected", 422)


def bounded_history(
    rows: list[MemoryMessage], *, count: int, token_limit: int
) -> tuple[tuple[MemoryMessage, ...], bool]:
    """UTF-8 byte count is a conservative token bound; never truncate an identifier silently."""
    selected: list[MemoryMessage] = []
    remaining = token_limit
    trimmed = len(rows) > count
    for row in rows[:count]:
        size = len(row.text.encode("utf-8"))
        if remaining <= 0:
            trimmed = True
            break
        if size > remaining:
            text = row.text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore").rstrip()
            trimmed = True
            if text:
                selected.append(row.model_copy(update={"text": text, "truncated": True}))
            break
        selected.append(row)
        remaining -= size
    return tuple(reversed(selected)), trimmed
