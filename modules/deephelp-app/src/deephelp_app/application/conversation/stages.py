"""为主链阶段记录起止、错误和观测事件，事实仍归原账本。

stage是异步上下文管理器：进入时开始计时，离开时即使异常也生成阶段结果。
准备阶段延迟emit，领取后再发真实标识；事件只解释实际执行，不触发业务重试。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING

from deephelp_app.adapters.observability.telemetry import observation, stage_event
from deephelp_app.adapters.observability.trace import TraceEvent
from deephelp_app.adapters.persistence.ledger import Receipt
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import (
    ErrorCode,
)
from deephelp_app.domain.models.conversation import RequestEnvelope
from deephelp_app.domain.models.responses import (
    StageReport,
)

from .tracking import EventClarification, RunTrace

if TYPE_CHECKING:
    from .orchestrator import Conversation


@asynccontextmanager
async def stage(
    self: Conversation,
    name: str,
    request: RequestEnvelope,
    receipt: Receipt,
    budget: ExecutionBudget,
    stats: RunTrace,
) -> AsyncIterator[None]:
    start, models, tools = perf_counter(), stats.model_calls, budget.tool_steps_used
    started_at = datetime.now(UTC)
    intents, retrievals = stats.intent_calls, stats.retrieval_calls
    stats.stage_error = None
    error: ErrorCode | None = None
    with observation(
        "stage." + name,
        attributes={
            "deephelp.stage": name,
            "deephelp.run_id": receipt.run_id,
            "deephelp.question_id": receipt.question.question_id,
        },
    ):
        try:
            yield
        except EventClarification:
            raise
        except AppError as exc:
            error = exc.code
            raise
        except TimeoutError, asyncio.CancelledError:
            error = ErrorCode.TIMEOUT
            raise
        except Exception:
            error = ErrorCode.INTERNAL_ERROR
            raise
        finally:
            error = error or stats.stage_error
            report = StageReport(
                stage=name,
                status="failed" if error else "completed",
                elapsed_ms=(perf_counter() - start) * 1000,
                model_calls=stats.model_calls - models,
                tool_calls=budget.tool_steps_used - tools,
                error_code=error,
                intent_calls=stats.intent_calls - intents,
                retrieval_calls=stats.retrieval_calls - retrievals,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
            stats.stages.append(report)
            if not stats.deferred:
                await self.emit(request, receipt, report)


async def emit(
    self: Conversation, request: RequestEnvelope, receipt: Receipt, report: StageReport
) -> None:
    stage_event(report.stage, report.status)
    await self.trace.emit(
        TraceEvent(
            event="stage_finished",
            request_id=request.request_id,
            trace_id=request.trace_id,
            run_id=receipt.run_id,
            question_id=receipt.question.question_id,
            stage=report.stage,
            elapsed_ms=report.elapsed_ms,
            model_calls=report.model_calls,
            tool_calls=report.tool_calls,
            stage_status=report.status,
            error_code=report.error_code,
            intent_calls=report.intent_calls,
            retrieval_calls=report.retrieval_calls,
        )
    )
