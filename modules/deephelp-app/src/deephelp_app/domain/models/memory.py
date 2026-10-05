"""有界记忆窗口的值：原消息、当前问题与关闭摘要。

窗口不拥有缓存或向量客户端；摘要可陈旧，使用前需回权威仓储核对版本/状态。
"""

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
