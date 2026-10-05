"""显式归属实验的持久入口，复用原领取/终态账本，不调用600或SOP。

先查旧receipt再计算归属，accept原子领取后才保存；重投读原结果。
短保存Task的取消回收与主链相同，Redis/Milvus投影意图随原事实提交。
"""

import asyncio
from datetime import UTC, datetime

from deephelp_app.adapters.persistence.ledger import Receipt
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import (
    BudgetUsed,
    ErrorCode,
    NextAction,
    Outcome,
    QuestionStatus,
    RunStatus,
)
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.responses import ResponseEnvelope

from .aggregation import EventAggregationService
from .policy import EventLedger


class PersistentEventAggregation:
    """Save membership and projection intent through M10; never run tools/classification."""

    def __init__(self, ledger: EventLedger, service: EventAggregationService) -> None:
        self.ledger, self.service = ledger, service

    async def process(self, request: RequestEnvelope, budget: ExecutionBudget) -> ResponseEnvelope:
        async with asyncio.timeout_at(budget.deadline):
            return await self._process(request, budget)

    async def _process(self, request: RequestEnvelope, budget: ExecutionBudget) -> ResponseEnvelope:
        previous = await self.ledger.lookup(request)
        if previous:
            return self.replay(previous, request)
        result = await self.service.aggregate(request, budget)
        attribution = (
            (result.target_question_id, result.expected_version)
            if result.target_question_id and result.expected_version
            else None
        )
        receipt = await self.ledger.accept(request, attribution=attribution)
        if not receipt.acquired:
            return self.replay(receipt, request)
        context = result.current_event_context
        q = receipt.question
        unresolved = context.unresolved_fields if context else ()
        status = QuestionStatus.WAITING_SLOT if unresolved else QuestionStatus.ACTIVE
        question = Question.model_validate(
            q.model_copy(
                update={
                    "version": q.version + 1,
                    "updated_at": datetime.now(UTC),
                    "status": status,
                    "entities": context.entities if context else (),
                    "conflicts": context.conflicts if context else (),
                    "unresolved_fields": unresolved,
                    "event_summary": context.summary
                    if context
                    else request.raw_text[: self.service.policy.summary_chars],
                    "aggregation_pending": result.disposition == "clarify",
                }
            ).model_dump()
        )
        if context:
            context = context.model_copy(update={"question_id": question.question_id})
            result = result.model_copy(
                update={
                    "current_event_context": context,
                    "events": tuple(
                        context if e.event_id == context.event_id else e for e in result.events
                    ),
                }
            )
        response = ResponseEnvelope(
            request_id=request.request_id,
            trace_id=request.trace_id,
            run_id=receipt.run_id,
            run_status=RunStatus.SUCCEEDED,
            question_id=question.question_id,
            question_status=status,
            outcome=Outcome.CLARIFY,
            reply=(
                "无法确定归属，请明确问题编号或分条发送独立问题。"
                if result.disposition == "clarify"
                else "已保存事件上下文，等待主流程识别意图。"
            ),
            missing_slots=unresolved,
            next_action=NextAction.PROVIDE_SLOTS if unresolved else NextAction.RETRY_LATER,
            event_cluster=result.model_copy(update={"persisted": True}),
            budget_used=budget.usage(),
        )

        # A short terminal write may finish on cancellation, without more external business work.
        async def persist() -> None:
            async with asyncio.timeout(5):
                await self.ledger.finish(receipt, question, response)

        writer = asyncio.create_task(persist())
        try:
            await asyncio.shield(writer)
        except asyncio.CancelledError:
            await writer
            raise
        return response

    @staticmethod
    def replay(receipt: Receipt, request: RequestEnvelope) -> ResponseEnvelope:
        if receipt.response:
            return receipt.response.model_copy(
                update={
                    "request_id": request.request_id,
                    "trace_id": request.trace_id,
                    "replayed": True,
                    "budget_used": BudgetUsed(),
                }
            )
        raise AppError(
            ErrorCode.VERSION_CONFLICT, "Event aggregation run unfinished; review before retry"
        )
