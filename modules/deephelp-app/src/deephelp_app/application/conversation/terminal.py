"""先提交问题与原receipt终态，再尽力记录诊断和审批暂停。

finish_run由当前消息请求调用；它只拥有一个最多五秒的保存Task。
shield保护这次短保存，外层取消仍须交还调用者；它不授权新的模型或工具执行。
保存失败会向上抛出，不能把尚未落账的回复宣称为已完成。
"""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import TYPE_CHECKING

from deephelp_app.adapters.observability.trace import TraceEvent
from deephelp_app.adapters.persistence.ledger import Receipt
from deephelp_app.application.sop_config import SOPDefinition
from deephelp_app.application.sop_governance import GovernedSOP
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.memory import MemoryWindow
from deephelp_app.domain.models.responses import (
    ResponseEnvelope,
)
from deephelp_app.domain.models.text import TextEntityResult

from .tracking import STAGES, RunTrace

if TYPE_CHECKING:
    from .orchestrator import Conversation


async def finish_run(
    conversation: Conversation,
    request: RequestEnvelope,
    receipt: Receipt,
    question: Question,
    response: ResponseEnvelope,
    budget: ExecutionBudget,
    text: TextEntityResult | None,
    window: MemoryWindow | None,
    definition: SOPDefinition | GovernedSOP | None,
    stats: RunTrace,
) -> None:
    # 当前请求只拥有这个短时存储Task；5秒宽限不能继续模型/工具业务。
    async def persist() -> None:
        async with asyncio.timeout(5):
            await conversation.ledger.finish(receipt, question, response)

    writer = asyncio.create_task(persist())
    storage_start = perf_counter()
    try:
        # shield只隔离这一条外层取消到writer的传播，外层仍会收到CancelledError。
        await asyncio.shield(writer)
    except asyncio.CancelledError:
        # 先收回第一次取消后的writer再传播；二次取消/进程强杀并非此处的提交保证。
        await writer
        raise
    # 账本提交完成后才发诊断；导出/调试不能成为重新执行工具的理由。
    await conversation.trace.emit(
        TraceEvent(
            event="ledger_committed",
            request_id=request.request_id,
            trace_id=request.trace_id,
            run_id=receipt.run_id,
            question_id=question.question_id,
            stage=STAGES[11],
            elapsed_ms=(perf_counter() - storage_start) * 1000,
        )
    )
    await conversation.capture_debug(
        request,
        response,
        question,
        text,
        window,
        definition,
        stats,
        input_question_version=receipt.question.version,
    )
    from deephelp_app.adapters.observability.native_observation import register_run

    await register_run(getattr(conversation.ledger, "pool", None), receipt.run_id)
    if conversation.approvals is not None and response.approval_operation_id:
        # The receipt/approval transaction is already authoritative if this fails.
        try:
            await conversation.approvals.pause(response.approval_operation_id, budget)
        except Exception:
            pass  # explicit resume reconstructs the graph from the committed ledger
