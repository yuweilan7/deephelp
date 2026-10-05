"""One sequential 13-stage message use case; helpers own preparation and closeout."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

from deephelp_app.adapters.observability.telemetry import annotate, observed
from deephelp_app.adapters.observability.trace import TraceEvent, TraceSink, sop_node_observer
from deephelp_app.adapters.persistence.ledger import MessageLedger, Receipt
from deephelp_app.application.approval import ApprovalService
from deephelp_app.application.cascade import enhanced_query
from deephelp_app.application.event_cluster.aggregation import EventAggregationService
from deephelp_app.application.event_cluster.policy import EventLedger
from deephelp_app.application.intent import IntentService
from deephelp_app.application.reply import ReplyComposer
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_config import SOPDefinition, load_sops
from deephelp_app.application.sop_governance import GovernedSOP
from deephelp_app.application.text_entity import TextEntityProcessor
from deephelp_app.domain.checks import response_from_sop
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import (
    ErrorCode,
    ErrorDetail,
    NextAction,
    Outcome,
    QuestionStatus,
    RunStatus,
)
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.intent_decisions import Decision, IntentDecision
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.memory import MemoryWindow
from deephelp_app.domain.models.release import VersionManifest
from deephelp_app.domain.models.responses import (
    ResponseEnvelope,
    StageReport,
)
from deephelp_app.domain.models.sop import SOPResult, SOPStatus
from deephelp_app.domain.models.text import TextEntityResult
from deephelp_app.domain.ports import (
    CaseRepository,
    MemoryPort,
    SOPExecutorPort,
)

from .diagnostics import capture_debug
from .preparation import prepare
from .stages import emit, stage
from .state import QuestionState, advance_question
from .terminal import finish_run
from .tracking import DISABLED, STAGES, EventClarification, RunTrace, current_run, usage_delta


class Conversation:
    stage = stage
    emit = emit
    prepare = prepare
    capture_debug = capture_debug

    def __init__(
        self,
        ledger: MessageLedger,
        text: TextEntityProcessor,
        intent: IntentService,
        sop: SOPExecutorPort,
        trace: TraceSink,
        versions: VersionManifest,
        definitions: dict[IntentCode, SOPDefinition | GovernedSOP] | None = None,
        memory: MemoryPort | None = None,
        cases: CaseRepository | None = None,
        events: EventAggregationService | None = None,
        reply_composer: ReplyComposer | None = None,
        approvals: ApprovalService | None = None,
    ) -> None:
        self.ledger, self.text, self.intent, self.sop, self.trace = ledger, text, intent, sop, trace
        self.versions = versions
        self.memory = memory
        self.cases = cases
        self.events = events
        self.reply_composer = reply_composer or ReplyComposer()
        self.approvals = approvals
        self.event_ledger = cast(EventLedger, ledger)
        if events and not getattr(ledger, "supports_continuation", False):
            raise ValueError("Automatic events require a continuation-capable ledger")
        self.disabled = tuple(
            f
            for f in DISABLED
            if (f != "hybrid" or not hasattr(intent.scope, "analyzer_version"))
            and (f not in {"memory", "redis_projection", "cross_message_slots"} or memory is None)
            and (f != "event_merge" or events is None)
            and (f != "llm_reply_polish" or self.reply_composer.model is None)
            and (f not in {"approval", "langgraph"} or approvals is None)
        )
        if not intent.cascade or not getattr(intent.cascade.fallback, "enabled", False):
            self.disabled += ("fasttext",)
        self.definitions: dict[IntentCode, SOPDefinition | GovernedSOP] = (
            (dict(sop.definitions) if isinstance(sop, SOPExecutor) else dict(load_sops()))
            if definitions is None
            else definitions
        )

    @observed("conversation.run")
    async def run(self, request: RequestEnvelope, budget: ExecutionBudget) -> ResponseEnvelope:
        from deephelp_app.domain.execution import request_execution

        token = request_execution.set(request)
        try:
            return await self._run(request, budget)
        finally:
            request_execution.reset(token)

    async def _run(self, request: RequestEnvelope, budget: ExecutionBudget) -> ResponseEnvelope:
        initial = budget.usage()
        annotate(
            **{
                "deephelp.registry_version": self.versions.registry,
                "deephelp.sop_version": self.versions.sop or "none",
                "deephelp.policy_version": self.versions.policy or "none",
                "deephelp.embedding_signature": self.versions.embedding_signature or "none",
                "deephelp.release_manifest": self.versions.release_manifest or "none",
            }
        )
        # Authorize hints before receipt lookup; a hint never resumes an old run.
        if request.question_hint:
            old = await self.ledger.question(
                request.identity, request.session_id, request.question_hint
            )
            if old is None:
                raise AppError(ErrorCode.FORBIDDEN, "Object access denied")
            if (
                old.status == QuestionStatus.WAITING_APPROVAL
                and request.message_id not in old.member_message_ids
            ):
                raise AppError(
                    ErrorCode.VERSION_CONFLICT,
                    "Pending approval must be rejected or revoked before a correction",
                    409,
                )
            if not getattr(self.ledger, "supports_continuation", False):
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "M08 requires a new complete question without hint"
                )
        stats = RunTrace()
        prepared = await self.prepare(request, budget, stats) if self.events else None
        receipt = prepared.receipt if prepared else await self.ledger.accept(request)
        annotate(
            **{
                "deephelp.run_id": receipt.run_id,
                "deephelp.question_id": receipt.question.question_id,
            }
        )
        if not receipt.acquired:
            annotate(**{"deephelp.replayed": True})
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
                        "call_counts": stats.counts(),
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
                call_counts=stats.counts(),
                budget_used=usage_delta(budget, initial),
            )
        token = current_run.set(stats)
        question = receipt.question
        node_token = sop_node_observer.set(stats.sop_nodes.append)
        decision: IntentDecision | None = None
        response: ResponseEnvelope | None = None
        text: TextEntityResult | None = prepared.text if prepared else None
        window: MemoryWindow | None = prepared.window if prepared else None
        definition: SOPDefinition | GovernedSOP | None = None
        cancelled = False
        event = prepared.event if prepared else None
        if event and event.current_event_context:
            bound_context = event.current_event_context.model_copy(
                update={"question_id": question.question_id}
            )
            event = event.model_copy(
                update={
                    "current_event_context": bound_context,
                    "events": tuple(
                        bound_context if e.event_id == bound_context.event_id else e
                        for e in event.events
                    ),
                }
            )
        try:
            try:
                async with asyncio.timeout_at(budget.deadline):
                    if prepared and prepared.error:
                        raise prepared.error
                    for name in () if prepared else STAGES[:2]:
                        async with self.stage(name, request, receipt, budget, stats):
                            budget.remaining_seconds()
                    state = QuestionState(question, text, window, (), question)
                    try:
                        await advance_question(
                            self, request, receipt, state, prepared, event, budget, stats
                        )
                    finally:
                        # Preserve completed preparation even if a later await fails or cancels.
                        question, text, window = state.question, state.text, state.window
                    entities, previous_question = state.entities, state.previous_question
                    assert text is not None
                    if event and event.disposition == "clarify":
                        raise EventClarification
                    async with self.stage(STAGES[5], request, receipt, budget, stats):
                        stats.intent_calls += 1
                        if (
                            request.question_hint or (event and event.disposition == "attached")
                        ) and question.active_intent:
                            try:
                                decision = await self.intent.recognize(
                                    text, budget, context_intent=question.active_intent
                                )
                            except AppError:
                                question = previous_question
                                raise
                        else:
                            memory_query = None
                            if (
                                event
                                and event.disposition == "attached"
                                and not question.active_intent
                            ):
                                memory_query = enhanced_query(
                                    question.event_summary, question.entities
                                )
                            decision = await self.intent.recognize(
                                text, budget, memory_query=memory_query
                            )
                    definition = None
                    async with self.stage(STAGES[6], request, receipt, budget, stats):
                        if decision.decision == Decision.CLARIFY and (
                            decision.final_code is None
                            or set(decision.final_code.required_slots) <= {e.name for e in entities}
                        ):
                            raise EventClarification
                        if decision.decision == Decision.REJECT:
                            raise AppError(ErrorCode.FORBIDDEN, "意图请求被拒绝。")
                        if decision.decision == Decision.HANDOFF:
                            response = self.error(
                                request,
                                receipt,
                                ErrorCode.UNKNOWN_INTENT,
                                "当前需要人工进一步核实。",
                                handoff=True,
                            )
                        if decision.final_code:
                            definition = (
                                self.sop.definition_for(question)
                                if isinstance(self.sop, SOPExecutor) and question.active_intent
                                else self.definitions.get(decision.final_code)
                            )
                        if response is None and (decision.final_code is None or definition is None):
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
                            (request.question_hint or (event and event.disposition == "attached"))
                            and question.active_intent
                            and question.versions.sop != definition.version
                        ):
                            raise AppError(ErrorCode.VERSION_CONFLICT, "Pinned SOP version changed")
                        question = question.model_copy(
                            update={
                                "active_intent": decision.final_code,
                                "versions": (
                                    question.versions
                                    if (
                                        request.question_hint
                                        or (event and event.disposition == "attached")
                                    )
                                    else self.versions
                                ).model_copy(update={"sop": definition.version}),
                            }
                        )
                        if isinstance(self.sop, SOPExecutor) and isinstance(
                            definition, GovernedSOP
                        ):
                            if question.versions.sop_snapshot is None:
                                question = question.model_copy(
                                    update={
                                        "versions": self.sop.pin_versions(
                                            question.versions, decision.final_code
                                        )
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
                            reply, presentation = await self.reply_composer.compose(
                                result, budget, continuation=self.memory is not None
                            )
                            response = response_from_sop(
                                request,
                                run_id=receipt.run_id,
                                question_id=question.question_id,
                                result=result,
                                reply=reply,
                                versions=question.versions,
                                budget_used=usage_delta(budget, initial),
                            )
                            response = response.model_copy(
                                update={"reply_presentation": presentation}
                            )
                            if self.approvals is not None and result.plan is not None:
                                response = ResponseEnvelope.model_validate(
                                    response.model_copy(
                                        update={
                                            "outcome": Outcome.PENDING_APPROVAL,
                                            "question_status": QuestionStatus.WAITING_APPROVAL,
                                            "run_status": RunStatus.WAITING_APPROVAL,
                                            "approval_operation_id": result.plan.operation_id,
                                            "reply": "预检已完成，变更等待审批，尚未执行。",
                                            "reply_presentation": None,
                                        }
                                    ).model_dump()
                                )
            except EventClarification:
                question = question.model_copy(update={"aggregation_pending": True})
                response = ResponseEnvelope(
                    request_id=request.request_id,
                    trace_id=request.trace_id,
                    run_id=receipt.run_id,
                    question_id=question.question_id,
                    run_status=RunStatus.SUCCEEDED,
                    question_status=QuestionStatus.WAITING_SLOT
                    if question.unresolved_fields
                    else QuestionStatus.ACTIVE,
                    outcome=Outcome.CLARIFY,
                    next_action=NextAction.PROVIDE_SLOTS,
                    missing_slots=question.unresolved_fields,
                    reply="无法确定归属或存在多个诉求，请明确问题编号或分条发送独立问题。",
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
                            "approval_operation_id": response.approval_operation_id,
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
                    "call_counts": stats.counts(),
                    "budget_used": usage_delta(budget, initial),
                    "event_cluster": event.model_copy(update={"persisted": True})
                    if event
                    else None,
                }
            )

            await finish_run(
                self,
                request,
                receipt,
                question,
                response,
                budget,
                text,
                window,
                definition,
                stats,
            )
            if cancelled:
                raise asyncio.CancelledError
            return response
        finally:
            sop_node_observer.reset(node_token)
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
