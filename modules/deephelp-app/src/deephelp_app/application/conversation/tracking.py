"""记录当前请求实际调用与阶段，不执行第二套会话逻辑。

CountedModel、CountedRetriever和TrackedTools包住原Port，记录返回值与实际派发标识。
ContextVar按Task传播，主链负责set/reset；它不是进程共享计数器。
重投展示的旧事实与本次新增调用分开，预算差值来自同一个ExecutionBudget。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime

from deephelp_app.adapters.persistence.ledger import Receipt
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import (
    BudgetUsed,
    ErrorCode,
)
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.events import EventClusterResult
from deephelp_app.domain.models.memory import MemoryWindow
from deephelp_app.domain.models.model_io import (
    ChatRequest,
    ChatResult,
    EmbeddingResult,
    RerankRequest,
    RerankResult,
)
from deephelp_app.domain.models.responses import (
    CallCounts,
    StageReport,
)
from deephelp_app.domain.models.retrieval import DenseResult, DenseScope
from deephelp_app.domain.models.text import TextEntityResult
from deephelp_app.domain.models.tools import ToolRequest, ToolResult
from deephelp_app.domain.ports import (
    ChatPort,
    DenseRetrieverPort,
    EmbeddingPort,
    RerankPort,
    ToolPort,
)

STAGES = (
    "100_INPUT_VALIDATE",
    "200_SAFETY_CHECK",
    "300_TEXT_CLEAN",
    "400_BUILD_BIZ_CONTEXT",
    "500_EVENT_CLUSTER",
    "600_INTENT_RECOGNIZE",
    "700_INTENT_CHECK",
    "800_KEYWORD_CHECK",
    "900_FETCH_SOP_ID",
    "1000_SOP_EXECUTE",
    "1100_REPLY_POLISH",
    "1200_SESSION_MAINTAIN",
    "1300_RESPONSE_ENVELOPE",
)


DISABLED = (
    "cross_message_slots",
    "event_merge",
    "hybrid",
    "memory",
    "approval",
    "business_writes",
    "llm_reply_polish",
    "langgraph",
    "redis_projection",
)


@dataclass
class RunTrace:
    model_calls: int = 0
    stages: list[StageReport] = field(default_factory=list)
    tool_call_ids: list[str] = field(default_factory=list)
    stage_error: ErrorCode | None = None
    intent_calls: int = 0
    retrieval_calls: int = 0
    deferred: bool = False
    tools: list[dict[str, object]] = field(default_factory=list)
    sop_nodes: list[dict[str, object]] = field(default_factory=list)
    retrievals: list[dict[str, object]] = field(default_factory=list)

    def counts(self) -> CallCounts:
        return CallCounts(
            intent_calls=self.intent_calls,
            model_calls=self.model_calls,
            retrieval_calls=self.retrieval_calls,
            tool_calls=len(self.tool_call_ids),
        )


@dataclass
class PreparedState:
    receipt: Receipt
    text: TextEntityResult | None = None
    window: MemoryWindow | None = None
    event: EventClusterResult | None = None
    error: AppError | None = None


class EventClarification(Exception):
    """Short circuit business nodes, while retaining terminal session/envelope nodes."""


class CountedRetriever:
    def __init__(self, retrieval: DenseRetrieverPort) -> None:
        self.retrieval = retrieval

    async def retrieve(
        self,
        text: str,
        scope: DenseScope,
        budget: ExecutionBudget,
        *,
        top_k: int = 3,
    ) -> DenseResult:
        if stats := current_run.get():
            stats.retrieval_calls += 1
        return await self.retrieval.retrieve(text, scope, budget, top_k=top_k)


current_run: ContextVar[RunTrace | None] = ContextVar("m08_run", default=None)


class CountedModel:
    def __init__(
        self, chat: ChatPort, embedding: EmbeddingPort, rerank: RerankPort | None = None
    ) -> None:
        self.chat_port, self.embedding_port = chat, embedding
        self.rerank_port = rerank

    async def rerank(self, request: RerankRequest, budget: ExecutionBudget) -> RerankResult:
        if self.rerank_port is None:
            raise AppError(ErrorCode.MODEL_CAPABILITY_UNAVAILABLE, "Rerank is not configured")
        before = budget.attempts_used
        try:
            return await self.rerank_port.rerank(request, budget)
        finally:
            if stats := current_run.get():
                stats.model_calls += budget.attempts_used - before

    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult:
        before = budget.attempts_used
        try:
            return await self.chat_port.chat(request, budget)
        finally:
            if stats := current_run.get():
                stats.model_calls += budget.attempts_used - before

    async def embed(self, texts: list[str], budget: ExecutionBudget) -> EmbeddingResult:
        before = budget.attempts_used
        try:
            return await self.embedding_port.embed(texts, budget)
        finally:
            if stats := current_run.get():
                stats.model_calls += budget.attempts_used - before


class TrackedTools:
    def __init__(self, tools: ToolPort) -> None:
        self.tools = tools

    async def execute(
        self,
        request: ToolRequest,
        question: Question,
        budget: ExecutionBudget,
        *,
        context: RequestEnvelope,
        on_dispatch: Callable[[str], None] | None = None,
    ) -> ToolResult:
        started = datetime.now(UTC)
        observation: dict[str, object] = {
            "tool_name": request.tool_name.value,
            "tool_version": request.tool_version,
            "parameters": request.parameters.model_dump(mode="json"),
            "parameters_hash": request.parameters_hash,
            "started_at": started.isoformat(),
            "call_id": None,
            "timeout_seconds": request.timeout_seconds,
        }

        def record(call_id: str) -> None:
            observation["call_id"] = call_id
            if stats := current_run.get():
                stats.tool_call_ids.append(call_id)
            if on_dispatch:
                on_dispatch(call_id)

        try:
            result = await self.tools.execute(
                request, question, budget, context=context, on_dispatch=record
            )
            observation["result"] = result.model_dump(mode="json")
            return result
        except asyncio.CancelledError:
            observation["error_code"] = "CANCELLED"
            raise
        except Exception as exc:
            observation["error_code"] = (
                exc.code.value if isinstance(exc, AppError) else "INTERNAL_ERROR"
            )
            raise
        finally:
            observation["finished_at"] = datetime.now(UTC).isoformat()
            if stats := current_run.get():
                stats.tools.append(observation)


def usage_delta(budget: ExecutionBudget, initial: BudgetUsed) -> BudgetUsed:
    now = budget.usage()
    values = now.model_dump()
    for name, value in values.items():
        old = getattr(initial, name)
        if value is not None and old is not None:
            values[name] = max(value * 0, value - old)
    return BudgetUsed.model_validate(values)
