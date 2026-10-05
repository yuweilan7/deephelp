"""Commit terminal facts before diagnostics; cancellation waits for the owned writer."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import TYPE_CHECKING

from deephelp_app.adapters.ledger import Receipt
from deephelp_app.adapters.trace import TraceEvent
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
    # Short terminal storage grace does not permit more model/tool execution.
    async def persist() -> None:
        async with asyncio.timeout(5):
            await conversation.ledger.finish(receipt, question, response)

    writer = asyncio.create_task(persist())
    storage_start = perf_counter()
    try:
        await asyncio.shield(writer)
    except asyncio.CancelledError:
        await writer
        raise
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
    from deephelp_app.adapters.native_observation import register_run

    await register_run(getattr(conversation.ledger, "pool", None), receipt.run_id)
    if conversation.approvals is not None and response.approval_operation_id:
        # The receipt/approval transaction is already authoritative if this fails.
        try:
            await conversation.approvals.pause(response.approval_operation_id, budget)
        except Exception:
            pass  # explicit resume reconstructs the graph from the committed ledger
