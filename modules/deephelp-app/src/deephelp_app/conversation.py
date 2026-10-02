"""Shared conversation pipeline with explicit case context; event attribution belongs to M11."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter

from deephelp_app.domain.checks import response_from_sop
from deephelp_app.domain.models import (
    BudgetUsed,
    ChatRequest,
    ChatResult,
    EmbeddingResult,
    ErrorCode,
    ErrorDetail,
    IntentCode,
    IntentDecision,
    Money,
    NextAction,
    Outcome,
    Question,
    QuestionStatus,
    RequestEnvelope,
    ResponseEnvelope,
    RunStatus,
    SOPResult,
    SOPStatus,
    StageReport,
    ToolRequest,
    ToolResult,
    VersionManifest,
)
from deephelp_app.errors import AppError
from deephelp_app.execution import ExecutionBudget
from deephelp_app.intent import IntentService
from deephelp_app.ledger import MessageLedger, Receipt
from deephelp_app.ports import (
    CaseRepository,
    ChatPort,
    EmbeddingPort,
    MemoryPort,
    SOPExecutorPort,
    ToolPort,
)
from deephelp_app.sop_config import SOPDefinition, load_sops
from deephelp_app.text_entity import TextEntityProcessor
from deephelp_app.trace import TraceEvent, TraceSink

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


current_run: ContextVar[RunTrace | None] = ContextVar("m08_run", default=None)


class CountedModel:
    def __init__(self, chat: ChatPort, embedding: EmbeddingPort) -> None:
        self.chat_port, self.embedding_port = chat, embedding

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
        def record(call_id: str) -> None:
            if stats := current_run.get():
                stats.tool_call_ids.append(call_id)
            if on_dispatch:
                on_dispatch(call_id)

        return await self.tools.execute(
            request, question, budget, context=context, on_dispatch=record
        )


def usage_delta(budget: ExecutionBudget, initial: BudgetUsed) -> BudgetUsed:
    now = budget.usage()
    values = now.model_dump()
    for name, value in values.items():
        old = getattr(initial, name)
        if value is not None and old is not None:
            values[name] = max(value * 0, value - old)
    return BudgetUsed.model_validate(values)


def fact_reply(result: SOPResult) -> str:
    if result.status == SOPStatus.WAITING_SLOT:
        names = "、".join(s.value for s in result.missing_slots)
        return (
            f"缺少或无法确认 {names}。请用一条新消息重新发送完整问题及这些编号；"
            "当前不自动合并跨消息槽位。"
        )
    if result.status == SOPStatus.FAILED:
        return "本次查询未取得可验证结论，请稍后重试或联系人工。"
    facts = {f.name: f.value for f in result.facts}
    parts = [f"订单 {facts['order_id']}。"] if "order_id" in facts else []
    for key, label in (("paid", "实付"), ("discount", "已记录优惠")):
        value = facts.get(key)
        if isinstance(value, Money):
            parts.append(f"{label} {value.amount:.2f} {value.currency}。")
    if "coupon_id" in facts:
        parts.append(f"券 {facts['coupon_id']}，状态 {facts.get('coupon_status')}。")
    if "activity_ids" in facts:
        parts.append(f"活动：{facts['activity_ids'] or '未查询到活动记录'}。")
    # Only configured SOP conclusions are published. Raw tool/model prose is never a reply.
    if "sop_conclusion" in facts:
        parts.append(str(facts["sop_conclusion"]) + "。")
    if result.status == SOPStatus.HANDED_OFF:
        parts.append("需要人工进一步核实。")
    return "".join(parts)


class Conversation:
    def __init__(
        self,
        ledger: MessageLedger,
        text: TextEntityProcessor,
        intent: IntentService,
        sop: SOPExecutorPort,
        trace: TraceSink,
        versions: VersionManifest,
        definitions: dict[IntentCode, SOPDefinition] | None = None,
        memory: MemoryPort | None = None,
        cases: CaseRepository | None = None,
    ) -> None:
        self.ledger, self.text, self.intent, self.sop, self.trace = ledger, text, intent, sop, trace
        self.versions = versions
        self.memory = memory
        self.cases = cases
        self.disabled = tuple(
            f
            for f in DISABLED
            if (f != "hybrid" or not hasattr(intent.scope, "analyzer_version"))
            and (f not in {"memory", "redis_projection", "cross_message_slots"} or memory is None)
        )
        self.definitions = load_sops() if definitions is None else definitions

    @asynccontextmanager
    async def stage(
        self,
        name: str,
        request: RequestEnvelope,
        receipt: Receipt,
        budget: ExecutionBudget,
        stats: RunTrace,
    ) -> AsyncIterator[None]:
        start, models, tools = perf_counter(), stats.model_calls, budget.tool_steps_used
        stats.stage_error = None
        error: ErrorCode | None = None
        try:
            yield
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
            )
            stats.stages.append(report)
            await self.emit(request, receipt, report)

    async def emit(self, request: RequestEnvelope, receipt: Receipt, report: StageReport) -> None:
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
            )
        )

    async def run(self, request: RequestEnvelope, budget: ExecutionBudget) -> ResponseEnvelope:
        initial = budget.usage()
        # Authorize hints before receipt lookup; a hint never resumes an old run.
        if request.question_hint:
            old = await self.ledger.question(
                request.identity, request.session_id, request.question_hint
            )
            if old is None:
                raise AppError(ErrorCode.FORBIDDEN, "Object access denied")
            if not getattr(self.ledger, "supports_continuation", False):
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "M08 requires a new complete question without hint"
                )
        receipt = await self.ledger.accept(request)
        if not receipt.acquired:
            await self.trace.emit(
                TraceEvent(
                    event="message_replayed",
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    run_id=receipt.run_id,
                    question_id=receipt.question.question_id,
                )
            )
            if receipt.response:
                return receipt.response.model_copy(
                    update={
                        "request_id": request.request_id,
                        "trace_id": request.trace_id,
                        "replayed": True,
                        "budget_used": usage_delta(budget, initial),
                    }
                )
            return ResponseEnvelope(
                request_id=request.request_id,
                trace_id=request.trace_id,
                run_id=receipt.run_id,
                run_status=RunStatus.RUNNING,
                question_id=receipt.question.question_id,
                question_status=receipt.question.status,
                outcome=Outcome.CLARIFY,
                next_action=NextAction.RETRY_LATER,
                replayed=True,
                reply="该消息已有处理中或中断的执行记录；本次未重新调用工具。请联系人工核对。",
                disabled_features=self.disabled,
            )
        stats = RunTrace()
        token = current_run.set(stats)
        question = receipt.question
        decision: IntentDecision | None = None
        response: ResponseEnvelope | None = None
        cancelled = False
        try:
            try:
                async with asyncio.timeout_at(budget.deadline):
                    for name in STAGES[:2]:
                        async with self.stage(name, request, receipt, budget, stats):
                            budget.remaining_seconds()
                    async with self.stage(STAGES[2], request, receipt, budget, stats):
                        text = await self.text.process(
                            request,
                            budget,
                            confirmed=question.entities,
                            fields=question.active_intent.required_slots
                            if question.active_intent
                            else None,
                        )
                        confirmed_names = {
                            e.name
                            for e in text.observations
                            if e.disposition != "negated"
                            and (
                                e.disposition == "correction"
                                or any(
                                    e.name == old.name and e.value == old.value
                                    for old in question.entities
                                )
                                or e.name not in {old.name for old in question.entities}
                            )
                        }
                        unresolved = set(text.unresolved_fields) | (
                            set(question.unresolved_fields) - confirmed_names
                        )
                        text = text.model_copy(
                            update={
                                "unresolved_fields": tuple(
                                    sorted(unresolved, key=lambda n: n.value)
                                )
                            }
                        )
                    async with self.stage(STAGES[3], request, receipt, budget, stats):
                        if self.memory:
                            await self.memory.load(request.identity, request.session_id, budget)
                        entities = tuple(
                            e for e in text.entities if e.name not in text.unresolved_fields
                        )
                    async with self.stage(STAGES[4], request, receipt, budget, stats):
                        previous_question = question
                        # Explicit membership comes from acceptance; automatic grouping is M11.
                        question = question.model_copy(
                            update={
                                "entities": tuple(text.entities),
                                "conflicts": (*question.conflicts, *text.conflicts),
                                "unresolved_fields": text.unresolved_fields,
                                "versions": question.versions
                                if question.active_intent
                                else self.versions,
                            }
                        )
                    async with self.stage(STAGES[5], request, receipt, budget, stats):
                        if request.question_hint and question.active_intent:
                            try:
                                decision = await self.intent.recognize(
                                    text, budget, context_intent=question.active_intent
                                )
                            except AppError:
                                question = previous_question
                                raise
                        else:
                            decision = await self.intent.recognize(text, budget)
                    definition: SOPDefinition | None = None
                    async with self.stage(STAGES[6], request, receipt, budget, stats):
                        if decision.final_code:
                            definition = self.definitions.get(decision.final_code)
                        if decision.final_code is None or definition is None:
                            response = self.error(
                                request,
                                receipt,
                                ErrorCode.UNKNOWN_INTENT
                                if decision.final_code is None
                                else ErrorCode.NO_SOP,
                                "当前无法处理这条完整问题，请联系人工。",
                                handoff=True,
                            )
                    if response is None:
                        assert definition is not None and decision.final_code is not None
                        if (
                            request.question_hint
                            and question.active_intent
                            and question.versions.sop != definition.version
                        ):
                            raise AppError(ErrorCode.VERSION_CONFLICT, "Pinned SOP version changed")
                        question = question.model_copy(
                            update={
                                "active_intent": decision.final_code,
                                "versions": (
                                    question.versions if request.question_hint else self.versions
                                ).model_copy(update={"sop": definition.version}),
                            }
                        )
                        async with self.stage(STAGES[7], request, receipt, budget, stats):
                            missing = tuple(
                                s
                                for s in definition.required_slots
                                if s not in {e.name for e in entities}
                            )
                        async with self.stage(STAGES[8], request, receipt, budget, stats):
                            if question.versions.sop != definition.version:
                                raise AppError(ErrorCode.VERSION_CONFLICT, "SOP version changed")
                        if missing:
                            result = SOPResult(
                                status=SOPStatus.WAITING_SLOT,
                                sop_id=definition.sop_id,
                                sop_version=definition.version,
                                missing_slots=missing,
                                next_action=NextAction.PROVIDE_SLOTS,
                            )
                        else:
                            question = question.model_copy(update={"status": QuestionStatus.ACTIVE})
                            async with self.stage(STAGES[9], request, receipt, budget, stats):
                                result = await self.sop.execute(
                                    question, budget, context=request, run_id=receipt.run_id
                                )
                                if result.error:
                                    stats.stage_error = result.error.code
                        async with self.stage(STAGES[10], request, receipt, budget, stats):
                            response = response_from_sop(
                                request,
                                run_id=receipt.run_id,
                                question_id=question.question_id,
                                result=result,
                                reply=fact_reply(result),
                                versions=question.versions,
                                budget_used=usage_delta(budget, initial),
                            )
                            if self.memory and result.status == SOPStatus.WAITING_SLOT:
                                response = response.model_copy(
                                    update={
                                        "reply": "缺少或无法确认 "
                                        + "、".join(s.value for s in result.missing_slots)
                                        + "。请带本问题编号补充或更正；也可以发送完整的新问题。"
                                    }
                                )
            except asyncio.CancelledError:
                cancelled = True
                response = self.error(request, receipt, ErrorCode.TIMEOUT, "执行已取消。")
                response = response.model_copy(
                    update={
                        "run_status": RunStatus.CANCELLED,
                        "question_status": QuestionStatus.CANCELLED,
                    }
                )
            except TimeoutError:
                response = self.error(
                    request, receipt, ErrorCode.BUDGET_EXHAUSTED, "执行总时限已耗尽。"
                )
            except AppError as exc:
                response = self.error(request, receipt, exc.code, exc.safe_message)
            except Exception:
                response = self.error(
                    request, receipt, ErrorCode.INTERNAL_ERROR, "执行未完成，请联系人工。"
                )
            assert response is not None
            if response.error and stats.tool_call_ids and not response.tool_call_ids:
                response = response.model_copy(
                    update={"tool_call_ids": tuple(stats.tool_call_ids), "outcome": Outcome.ERROR}
                )
            async with self.stage(STAGES[11], request, receipt, budget, stats):
                question = Question.model_validate(
                    question.model_copy(
                        update={
                            "status": response.question_status,
                            "version": receipt.question.version + 1,
                            "updated_at": datetime.now(UTC),
                        }
                    ).model_dump()
                )
            async with self.stage(STAGES[12], request, receipt, budget, stats):
                response = ResponseEnvelope.model_validate(response.model_dump())
            by_name = {s.stage: s for s in stats.stages}
            for name in STAGES:
                if name not in by_name:
                    skipped = StageReport(stage=name, status="skipped")
                    by_name[name] = skipped
                    await self.emit(request, receipt, skipped)
            response = response.model_copy(
                update={
                    "stages": tuple(by_name[n] for n in STAGES),
                    "intent_decision": decision,
                    "disabled_features": self.disabled,
                    "versions": question.versions,
                    "budget_used": usage_delta(budget, initial),
                }
            )

            # Short terminal storage grace does not permit more model/tool execution.
            async def persist() -> None:
                async with asyncio.timeout(5):
                    await self.ledger.finish(receipt, question, response)

            writer = asyncio.create_task(persist())
            storage_start = perf_counter()
            try:
                await asyncio.shield(writer)
            except asyncio.CancelledError:
                await writer
                raise
            await self.trace.emit(
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
            if cancelled:
                raise asyncio.CancelledError
            return response
        finally:
            current_run.reset(token)

    @staticmethod
    def error(
        request: RequestEnvelope,
        receipt: Receipt,
        code: ErrorCode,
        message: str,
        *,
        handoff: bool = False,
    ) -> ResponseEnvelope:
        rejected = code == ErrorCode.FORBIDDEN
        return ResponseEnvelope(
            request_id=request.request_id,
            trace_id=request.trace_id,
            run_id=receipt.run_id,
            question_id=receipt.question.question_id,
            run_status=RunStatus.SUCCEEDED if handoff else RunStatus.FAILED,
            question_status=QuestionStatus.HANDED_OFF if handoff else QuestionStatus.ACTIVE,
            outcome=Outcome.HANDOFF if handoff else Outcome.REJECTED if rejected else Outcome.ERROR,
            reply=message,
            next_action=NextAction.CONTACT_SUPPORT,
            error=ErrorDetail(code=code, message=message),
        )
