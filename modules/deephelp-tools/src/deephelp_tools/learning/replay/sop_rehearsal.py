"""SOP预演的固定观察与原执行入口；只有事实适配，没有第二份分支引擎。

FixedRehearsalFacts提供明确合成的有限样本，身份/参数/证据仍由原SOPExecutor校验。
execute_rehearsal拥有一个独立问题与ContextVar作用域；调用者拥有Port/预算/任务生命周期。
真实模式借用已打开的原模型和只读MCP，不拥有它们的关闭权，不进入Conversation/审批服务。
"""

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from deephelp_app.adapters.mcp.business.mcp_protocol import canonical
from deephelp_app.adapters.observability.trace import request_context, sop_node_observer
from deephelp_app.application.conversation.tracking import (
    CountedModel,
    RunTrace,
    TrackedTools,
    current_run,
)
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import TOOL_GRANTS, SOPRegistry
from deephelp_app.domain.checks import validate_tool_context
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget, request_execution
from deephelp_app.domain.models.common import ErrorCode, ErrorDetail, Money, VerifiedIdentity
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.evidence import EvidenceRef, EvidenceSource, Fact, FactKind
from deephelp_app.domain.models.tools import ToolName, ToolRequest, ToolResult, ToolStatus
from deephelp_app.domain.ports import ToolPort
from deephelp_tools.learning.course.catalog import catalog_question
from deephelp_tools.learning.course.skills import RecordedSkillModel


class FixedRehearsalFacts:
    """固定合成事实和三种反例。slow的等待由此Port拥有，任务取消会直接传播。

    模型不能改scenario或事实；不读数据库、不维护记录、不提交任何业务效果。
    on_dispatch先记录真实合成派发ID，取消后的已派发与未完成因此可以区分。
    """

    def __init__(self, scenario: str = "normal") -> None:
        self.scenario = scenario

    async def execute(
        self,
        request: ToolRequest,
        question: Question,
        budget: ExecutionBudget,
        *,
        context: RequestEnvelope,
        on_dispatch: Any = None,
    ) -> ToolResult:
        validate_tool_context(request, question)
        budget.claim_attempt(retry=False)
        budget.tool_steps_used += 1
        call = "rehearsal-call-" + uuid4().hex
        if on_dispatch:
            on_dispatch(call)
        if self.scenario == "slow":
            await asyncio.sleep(5)
        if self.scenario == "tool_failure":
            return ToolResult(
                operation_id=request.operation_id,
                run_id=request.run_id,
                tool_name=request.tool_name,
                tool_version=request.tool_version,
                call_id=call,
                status=ToolStatus.FAILED,
                error=ErrorDetail(
                    code=ErrorCode.UPSTREAM_UNAVAILABLE, message="明确离线工具失败样本"
                ),
            )
        p = request.parameters
        values: dict[str, Any]
        if request.tool_name == ToolName.GET_ORDER_BENEFITS:
            values = dict(
                order_id=p.order_id,
                paid=Money(amount=Decimal("173.45")),
                discount=Money(amount=Decimal("20.00")),
                discount_status="applied",
                activity_ids="ACT-43",
                activity_labels="明确合成的预演活动",
            )
        elif request.tool_name == ToolName.CHECK_COUPON:
            values = dict(
                order_id=p.order_id,
                coupon_id=p.coupon_id,
                coupon_status="threshold_not_met",
                minimum_spend=Money(amount=Decimal("200.00")),
                usable=False,
            )
        elif request.tool_name == ToolName.CHECK_ACTIVITY_ELIGIBILITY:
            values = dict(
                activity_id=p.activity_id,
                activity_label="合成预演活动",
                activity_rules="仅合成来源",
                eligibility="eligible",
                remaining_slots="7",
                eligibility_reason="固定合成观察",
            )
        elif request.tool_name == ToolName.GET_ORDER_CREATION_FAILURE:
            values = dict(
                attempt_id=p.attempt_id,
                attempt_status="failed",
                failure_code="STOCK_CHANGED",
                failure_reason="合成库存改变",
                follow_up="由用户核对后另行下单",
            )
        else:
            values = dict(
                refund_id=p.refund_id,
                coupon_id=p.coupon_id,
                refund_status="completed",
                return_status="returned",
                return_reason="合成返还记录",
            )
        locator = p.order_id or p.activity_id or p.attempt_id or p.refund_id
        if self.scenario == "invalid_fact":
            locator = "wrong-object"
        evidence = "rehearsal-evidence-" + uuid4().hex
        serialized = {
            k: v.model_dump(mode="json") if isinstance(v, Money) else v for k, v in values.items()
        }
        return ToolResult(
            operation_id=request.operation_id,
            run_id=request.run_id,
            tool_name=request.tool_name,
            tool_version=request.tool_version,
            call_id=call,
            status=ToolStatus.SUCCEEDED,
            facts=tuple(
                Fact(
                    name=k,
                    kind=FactKind.MONEY
                    if isinstance(v, Money)
                    else FactKind.FLAG
                    if isinstance(v, bool)
                    else FactKind.TEXT,
                    value=v,
                    evidence_ids=(evidence,),
                )
                for k, v in values.items()
            ),
            evidence_refs=(
                EvidenceRef(
                    evidence_id=evidence,
                    source=EvidenceSource.TOOL,
                    record_id=call,
                    version="business-fixtures-v1",
                    locator=locator,
                    content_hash=hashlib.sha256(canonical(serialized)).hexdigest(),
                    summary="明确离线的固定预演观察；非企业或PG事实。",
                ),
            ),
        )


class ReadOnlyRehearsalTools:
    """每次派发核对原意图只读许可，底层仍是原ToolPort及其权限/证据守卫。"""

    def __init__(self, tools: ToolPort) -> None:
        self.tools = tools

    async def execute(
        self,
        request: ToolRequest,
        question: Question,
        budget: ExecutionBudget,
        *,
        context: RequestEnvelope,
        on_dispatch: Any = None,
    ) -> ToolResult:
        if question.active_intent is None or request.tool_name not in TOOL_GRANTS.get(
            question.active_intent, ()
        ):
            raise AppError(ErrorCode.FORBIDDEN, "Rehearsal only permits registered read tools", 403)
        return await self.tools.execute(
            request, question, budget, context=context, on_dispatch=on_dispatch
        )


async def execute_rehearsal(
    options: dict[str, Any],
    *,
    model: Any = None,
    tools: Any = None,
    budget: ExecutionBudget | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """独立问题只进入原1000执行器；预演hash/输入/实际节点保存在同一任务中。

    禁止含审批提案的候选，避免生成SOPPlan。节点观察来自原observe_sop_node；
    finally在取消/失败时也保存预算、派发/部分时间线，再reset本任务的上下文。
    """
    registry = SOPRegistry.model_validate(options["registry"])
    flow = registry.definition(options["intent_code"])
    if any(n.proposal for n in flow.nodes):
        raise AppError(ErrorCode.FORBIDDEN, "Rehearsal cannot generate approval proposals", 403)
    live = options["mode"] == "live-readonly"
    if live and (model is None or tools is None or budget is None):
        raise AppError(
            ErrorCode.NOT_IMPLEMENTED, "Original live read-only runtime unavailable", 503
        )
    if not live:
        model, tools = RecordedSkillModel(), FixedRehearsalFacts(options["scenario"])
        budget = ExecutionBudget.start(30, 40, 0)
    assert budget is not None
    owner = VerifiedIdentity.model_validate(options["owner"])
    now = datetime.now(UTC)
    key = options["run_id"]
    context = RequestEnvelope(
        channel="sop-rehearsal",
        session_id=key,
        message_id=key,
        raw_text="作者明确确认的预演槽位 " + " ".join(options["slots"].values()),
        occurred_at=now,
        received_at=now,
        identity=owner,
        request_id=key,
        trace_id=key,
    )
    question, context = catalog_question(
        registry, code=flow.intent_code, slots=options["slots"], context=context
    )
    executor = SOPExecutor(
        CountedModel(model, model) if live else model,
        TrackedTools(ReadOnlyRehearsalTools(tools)),
        registry=registry,
        evidence_version=getattr(tools, "evidence_version", "business-fixtures-v1"),
        skills_enabled=False,
    )
    stats = RunTrace()
    context_token = request_execution.set(context)
    trace_token = request_context.set((key, key))
    run_token = current_run.set(stats)

    def capture(node: dict[str, object]) -> None:
        stats.sop_nodes.append(node)
        if progress:
            progress(snapshot())

    def snapshot() -> dict[str, Any]:
        executed = {n["node_id"] for n in stats.sop_nodes}
        return dict(
            run_id=key,
            session_id=key,
            question_id=question.question_id,
            candidate_hash=registry.snapshot_hash,
            mode=options["mode"],
            input_source="author_confirmed_slots",
            fact_source="original_readonly_mcp" if live else "fixed_synthetic",
            timeline=stats.sop_nodes,
            tools=stats.tools,
            model_calls=stats.model_calls if live else 0,
            budget=budget.usage().model_dump(mode="json"),
            unexecuted_nodes=[n.node_id for n in flow.nodes if n.node_id not in executed],
            definition=flow.model_dump(mode="json"),
            business_writes=0,
        )

    node_token = sop_node_observer.set(capture)
    try:
        result = await executor.execute(question, budget, context=context, run_id=key)
        return {
            **snapshot(),
            "status": result.status.value,
            "result": result.model_dump(mode="json"),
        }
    finally:
        try:
            if progress:
                progress(snapshot())
        finally:
            sop_node_observer.reset(node_token)
            current_run.reset(run_token)
            request_context.reset(trace_token)
            request_execution.reset(context_token)
