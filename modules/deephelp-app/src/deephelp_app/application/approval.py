"""Explicit human decisions and restartable approval graph; MySQL ledger is authority."""

import asyncio
from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from deephelp_app.adapters.approval_store import TERMINAL, ApprovalRepository
from deephelp_app.adapters.checkpoints import MySQLSaver
from deephelp_app.adapters.synthetic_rights import RightsClient, SyntheticEffect, binding_hash
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import (
    ApprovalCommand,
    ApprovalStatus,
    ErrorCode,
    EvidenceRef,
    EvidenceSource,
    Fact,
    FactKind,
    NextAction,
    OperationRecord,
    OperationStatus,
    Outcome,
    QuestionStatus,
    ResponseEnvelope,
    ResumeCommand,
    RunStatus,
    VerifiedIdentity,
)


class ApprovalState(TypedDict, total=False):
    operation_id: str
    effect: dict[str, Any] | None
    status: str


def effect_response(op: OperationRecord, effect: SyntheticEffect) -> ResponseEnvelope:
    if (
        effect.operation_id != op.plan.operation_id
        or effect.binding_hash != binding_hash(op)
        or effect.order_id != op.plan.parameters.order_id
    ):
        raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Effect binding differs")
    evidence_id = "m15-" + effect.call_id
    return ResponseEnvelope.model_validate(
        op.pending_response.model_copy(
            update={
                "outcome": Outcome.ANSWERED,
                "question_status": QuestionStatus.RESOLVED,
                "run_status": RunStatus.SUCCEEDED,
                "next_action": NextAction.NONE,
                "sop_plan": None,
                "reply_presentation": None,
                "facts": [
                    *op.pending_response.facts,
                    Fact(
                        name="synthetic_adjustment_status",
                        kind=FactKind.TEXT,
                        value="applied",
                        evidence_ids=(evidence_id,),
                    ),
                ],
                "evidence_refs": [
                    *op.pending_response.evidence_refs,
                    EvidenceRef(
                        evidence_id=evidence_id,
                        source=EvidenceSource.TOOL,
                        record_id=effect.call_id,
                        version="synthetic-rights-v1",
                        content_hash=binding_hash(op),
                        summary="合成权益服务持久记录，已按操作编号核对。",
                    ),
                ],
                "tool_call_ids": (*op.pending_response.tool_call_ids, effect.call_id),
                "call_counts": op.pending_response.call_counts.model_copy(
                    update={
                        "tool_calls": op.pending_response.call_counts.tool_calls
                        + op.dispatch_attempts
                        + op.query_attempts,
                    }
                ),
                "reply": "已根据明确审批完成合成权益调整，并核对了持久效果记录。",
            }
        ).model_dump()
    )


class ApprovalService:
    def __init__(
        self,
        repo: ApprovalRepository,
        rights: RightsClient,
        snapshot: str,
        *,
        fault: Callable[[str], None] | None = None,
    ) -> None:
        self.repo, self.rights, self.snapshot, self.fault = repo, rights, snapshot, fault
        self.saver = MySQLSaver(repo.pool)

    def inject(self, stage: str) -> None:
        if self.fault:
            self.fault(stage)

    def graph(self, budget: ExecutionBudget, token: str | None = None) -> Any:
        async def wait(state: ApprovalState) -> ApprovalState:
            op = await self.repo.get(state["operation_id"])
            if op.approval_status == ApprovalStatus.PENDING:
                interrupt(
                    {
                        "operation_id": op.plan.operation_id,
                        "action": op.plan.action,
                        "summary": "等待此操作的明确审批；普通消息不会批准。",
                        "expires_at": op.expires_at.isoformat(),
                    }
                )
            return {"status": op.status.value}

        async def execute(state: ApprovalState) -> ApprovalState:
            op = await self.repo.get(state["operation_id"])
            if op.status in TERMINAL:
                return {"status": op.status.value, "effect": None}
            assert token is not None
            try:
                # A lost response/commit is queried before any possible redispatch.
                if op.status in {OperationStatus.IN_FLIGHT, OperationStatus.UNKNOWN}:
                    op = await self.repo.query_dispatched(op.plan.operation_id, token)
                    result = await self.rights.call("query", op, budget)
                    if result.status == "SUCCEEDED":
                        assert result.effect is not None
                        return {"effect": result.effect.model_dump(), "status": "OBSERVED"}
                op = await self.repo.dispatch(op.plan.operation_id, token, self.snapshot)
                self.inject("before_tool")
                result = await self.rights.call("execute", op, budget)
                self.inject("after_tool")
                assert result.effect is not None
                return {"effect": result.effect.model_dump(), "status": "OBSERVED"}
            except asyncio.CancelledError:
                await asyncio.shield(self.repo.unknown(op.plan.operation_id, token))
                raise
            except Exception as exc:
                current = await self.repo.get(op.plan.operation_id)
                if current.status == OperationStatus.PREPARED:
                    raise  # validation rejected before dispatch; no uncertain effect exists
                safe_error = (
                    exc.code.value + ": " + exc.safe_message
                    if isinstance(exc, AppError)
                    else type(exc).__name__
                )
                await self.repo.unknown(op.plan.operation_id, token, safe_error)
                return {"effect": None, "status": OperationStatus.UNKNOWN.value}

        async def reconcile(state: ApprovalState) -> ApprovalState:
            if state.get("effect"):
                assert token is not None
                op = await self.repo.get(state["operation_id"])
                effect = SyntheticEffect.model_validate(state["effect"])
                await self.repo.complete(op.plan.operation_id, token, effect_response(op, effect))
                self.inject("after_business_commit")
                return {"status": OperationStatus.SUCCEEDED.value}
            return {"status": state["status"]}

        graph = StateGraph(ApprovalState)
        graph.add_node("approval_wait", wait).add_node("execute_operation", execute)
        graph.add_node("reconcile_effect", reconcile)
        graph.add_edge(START, "approval_wait").add_edge("approval_wait", "execute_operation")
        graph.add_edge("execute_operation", "reconcile_effect").add_edge("reconcile_effect", END)
        return graph.compile(checkpointer=self.saver)

    @staticmethod
    def config(op: OperationRecord) -> Any:
        return {"configurable": {"thread_id": "m15-approval-v1:" + op.run_id}}

    async def pause(self, operation_id: str, budget: ExecutionBudget) -> None:
        self.inject("before_checkpoint")
        op = await self.repo.get(operation_id)
        graph = self.graph(budget)
        config = self.config(op)
        state = await graph.aget_state(config)
        if not state.values:
            await graph.ainvoke({"operation_id": operation_id}, config, durability="sync")

    async def decide(
        self,
        identity: VerifiedIdentity,
        operation_id: str,
        command: ApprovalCommand,
    ) -> OperationRecord:
        op = await self.repo.decide(identity, operation_id, command, self.snapshot)
        self.inject("after_decision")
        return op

    async def resume(
        self,
        identity: VerifiedIdentity,
        operation_id: str,
        command: ResumeCommand,
        budget: ExecutionBudget,
    ) -> OperationRecord:
        await self.repo.scoped(identity, command.session_id, command.run_id, operation_id)
        op, token = await self.repo.acquire(operation_id, self.snapshot)
        if token is None:
            graph = self.graph(budget)
            config = self.config(op)
            state = await graph.aget_state(config)
            if state.next or not state.values:
                await graph.aupdate_state(
                    config,
                    {
                        "operation_id": operation_id,
                        "status": op.status.value,
                        "effect": None,
                    },
                    as_node="reconcile_effect",
                )
            return op
        try:
            async with asyncio.timeout(min(60, budget.remaining_seconds())):
                graph = self.graph(budget, token)
                config = self.config(op)
                state = await graph.aget_state(config)
                # Missing checkpoint is a recoverable ledger/checkpoint gap.
                if not state.values or not state.next:
                    value: Any = {"operation_id": operation_id, "effect": None}
                elif any(t.interrupts for t in state.tasks):
                    value = Command(resume="ledger_decision")
                else:
                    value = None
                await graph.ainvoke(value, config, durability="sync")
            return await self.repo.get(operation_id)
        finally:
            await self.repo.release(operation_id, token)
