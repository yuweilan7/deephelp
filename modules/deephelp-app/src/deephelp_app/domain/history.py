"""授权历史的只读分页值；结果仍取原消息与原receipt。

分页摘要不重执行消息、不迁移身份，也不把缺终态的记录包装成成功回复。
"""

from pydantic import AwareDatetime

from deephelp_app.domain.models.common import DTO, Count, Identifier, RunStatus
from deephelp_app.domain.models.conversation import ConverseInput, Question
from deephelp_app.domain.models.responses import ResponseEnvelope


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
