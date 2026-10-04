"""Authorized history projections; stored receipts remain the source of results."""

from pydantic import AwareDatetime

from deephelp_app.domain.models import (
    DTO,
    ConverseInput,
    Count,
    Identifier,
    Question,
    ResponseEnvelope,
    RunStatus,
)


class SessionSummary(DTO):
    session_id: Identifier
    message_count: Count
    updated_at: AwareDatetime


class SessionPage(DTO):
    items: tuple[SessionSummary, ...]
    next_offset: Count | None = None


class HistoryMessage(DTO):
    input: ConverseInput
    run_id: Identifier
    status: RunStatus
    question: Question
    response: ResponseEnvelope | None = None


class MessagePage(DTO):
    items: tuple[HistoryMessage, ...]
    next_offset: Count | None = None
