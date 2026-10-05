"""Canonical memory values and validation; no runtime resources."""

from __future__ import annotations

from pydantic import (
    AwareDatetime,
)

from .common import DTO, Count, Identifier, PositiveCount, QuestionStatus, RawText
from .conversation import Question


class MemoryMessage(DTO):
    channel: Identifier
    message_id: Identifier
    question_id: Identifier
    occurred_at: AwareDatetime
    text: RawText
    truncated: bool = False


class CaseSummary(DTO):
    question_id: Identifier
    version: PositiveCount
    status: QuestionStatus
    text: RawText
    message_ids: tuple[Identifier, ...]


class MemoryWindow(DTO):
    history: tuple[MemoryMessage, ...] = ()
    active_questions: tuple[Question, ...] = ()
    closed_summaries: tuple[CaseSummary, ...] = ()
    generation: Count = 0
    trimmed: bool = False
