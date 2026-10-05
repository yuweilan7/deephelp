"""在原子领取消息前判定归属，供Conversation.prepare绑定为同一实例方法。

先查旧receipt，再提取当前原文、读授权窗口并提出目标问题与期望版本。
这些读取与模型建议不等于领取成功；最后accept做原子竞争，只有赢家进入后续业务。
准备阶段暂存诊断，领取后才把它们绑定到真实run/question。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from deephelp_app.adapters.persistence.ledger import new_receipt
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import (
    ErrorCode,
)
from deephelp_app.domain.models.conversation import RequestEnvelope

from .tracking import STAGES, PreparedState, RunTrace, current_run

if TYPE_CHECKING:
    from .orchestrator import Conversation


async def prepare(
    self: Conversation,
    request: RequestEnvelope,
    budget: ExecutionBudget,
    stats: RunTrace,
) -> PreparedState:
    """返回领取后的receipt和准备结果；此阶段不执行主意图或工具。

    准备失败中，权限/输入/版本冲突直接拒绝，其余错误随新领取记录进入主链终态。
    取消不被Exception捕获，因此领取之前被取消不会继续accept。
    """
    assert self.events is not None
    previous = await self.event_ledger.lookup(request)
    if previous:
        return PreparedState(previous)
    state = PreparedState(new_receipt(request))
    stats.deferred = True
    token = current_run.set(stats)
    try:
        try:
            async with asyncio.timeout_at(budget.deadline):
                for name in STAGES[:2]:
                    async with self.stage(name, request, state.receipt, budget, stats):
                        budget.remaining_seconds()
                async with self.stage(STAGES[2], request, state.receipt, budget, stats):
                    state.text = await self.text.process(request, budget, fields=())
                async with self.stage(STAGES[3], request, state.receipt, budget, stats):
                    state.window = (
                        await self.memory.load(request.identity, request.session_id, budget)
                        if self.memory
                        else await self.events.assembler.load(request)
                    )
                async with self.stage(STAGES[4], request, state.receipt, budget, stats):
                    state.event = await self.events.aggregate(
                        request,
                        budget,
                        extracted=state.text,
                        window=state.window,
                        allow_unclassified=True,
                    )
        except TimeoutError:
            state.error = AppError(ErrorCode.BUDGET_EXHAUSTED, "Preparation deadline exhausted")
        except AppError as exc:
            if exc.code in {
                ErrorCode.FORBIDDEN,
                ErrorCode.INVALID_ARGUMENT,
                ErrorCode.VERSION_CONFLICT,
                ErrorCode.IDEMPOTENCY_CONFLICT,
            }:
                raise
            state.error = exc
        except Exception:
            state.error = AppError(ErrorCode.INTERNAL_ERROR, "Event preparation failed")
        attribution = (
            (state.event.target_question_id, state.event.expected_version)
            if state.event
            and state.event.target_question_id
            and state.event.expected_version
            and not state.error
            else None
        )
        state.receipt = await self.event_ledger.accept(request, attribution=attribution)
        if state.receipt.acquired:
            for report in stats.stages:
                await self.emit(request, state.receipt, report)
        return state
    finally:
        stats.deferred = False
        current_run.reset(token)
