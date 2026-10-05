"""显式审批与可恢复图；操作事实属于PG账本，checkpoint只保存图进度。

主链落账后调用pause；独立审批/恢复API调用decide与resume，普通新消息不会批准旧操作。
先读resume的归属与领取，再读graph中的等待、执行和对账节点。
IN_FLIGHT/UNKNOWN先查询同一操作，观察值通过绑定校验后才能提交完成。
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from deephelp_app.adapters.approval.approval_store import TERMINAL, ApprovalRepository
from deephelp_app.adapters.approval.postgres_checkpoints import (
    PostgresCheckpoints,
    official_checkpoints,
)
from deephelp_app.adapters.approval.synthetic_rights import (
    RightsClient,
    SyntheticEffect,
    binding_hash,
)
from deephelp_app.adapters.observability.telemetry import (
    annotate,
    observation,
    observed,
    operation_link,
)
from deephelp_app.adapters.persistence.postgres import application_config
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.approval import ApprovalCommand, OperationRecord, ResumeCommand
from deephelp_app.domain.models.common import (
    ApprovalStatus,
    ErrorCode,
    NextAction,
    OperationStatus,
    Outcome,
    QuestionStatus,
    RunStatus,
    VerifiedIdentity,
)
from deephelp_app.domain.models.evidence import EvidenceRef, EvidenceSource, Fact, FactKind
from deephelp_app.domain.models.responses import ResponseEnvelope


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
        saver: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        if saver is None or (
            getattr(repo.pool, "backend", None) == "postgresql"
            and not isinstance(saver, PostgresCheckpoints)
        ):
            raise AppError(
                ErrorCode.NOT_IMPLEMENTED,
                "Approval execution/resume requires an explicitly opened official saver",
                501,
            )
        self.repo, self.rights, self.snapshot, self.fault = repo, rights, snapshot, fault
        self.saver = saver

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        repo: ApprovalRepository,
        rights: RightsClient,
        snapshot: str,
        *,
        root: Path | None = None,
        fault: Callable[[str], None] | None = None,
    ) -> AsyncIterator[ApprovalService]:
        async with official_checkpoints(
            getattr(repo.pool, "config", None) or application_config(root or Path.cwd()),
            owner=getattr(repo.pool, "owner", None),
        ) as saver:
            yield cls(repo, rights, snapshot, fault=fault, saver=saver)

    def inject(self, stage: str) -> None:
        if self.fault:
            self.fault(stage)

    def graph(self, budget: ExecutionBudget, token: str | None = None) -> Any:
        @observed("graph.approval_wait")
        async def wait(state: ApprovalState) -> ApprovalState:
            # interrupt恢复可能重入该节点；暂停前只读取原账本，不做不可逆业务效果。
            op = await self.repo.get(state["operation_id"])
            if op.approval_status == ApprovalStatus.PENDING:
                annotate(
                    **{
                        "deephelp.status": "interrupted",
                        "deephelp.operation_id": op.plan.operation_id,
                        "deephelp.synthetic": True,
                    }
                )
                interrupt(
                    {
                        "operation_id": op.plan.operation_id,
                        "action": op.plan.action,
                        "summary": "等待此操作的明确审批；普通消息不会批准。",
                        "expires_at": op.expires_at.isoformat(),
                    }
                )
            return {"status": op.status.value}

        @observed("graph.execute_operation")
        async def execute(state: ApprovalState) -> ApprovalState:
            op = await self.repo.get(state["operation_id"])
            # 终态来自原业务账本；图重入或重复resume只能读取，不再派发下游。
            if op.status in TERMINAL:
                return {"status": op.status.value, "effect": None}
            assert token is not None
            try:
                # 丢响应/提交未知先query原operation；明确ABSENT后才考虑同一操作派发。
                if op.status in {OperationStatus.IN_FLIGHT, OperationStatus.UNKNOWN}:
                    op = await self.repo.query_dispatched(op.plan.operation_id, token)
                    result = await self.rights.call("query", op, budget)
                    if result.status == "SUCCEEDED":
                        assert result.effect is not None
                        return {"effect": result.effect.model_dump(), "status": "OBSERVED"}
                op = await self.repo.dispatch(op.plan.operation_id, token, self.snapshot)
                self.inject("before_tool")
                result = await self.rights.call("execute", op, budget)
                # await取得观察值而非自行证明落账；下一节点校验同operation效果后提交。
                self.inject("after_tool")
                assert result.effect is not None
                return {"effect": result.effect.model_dump(), "status": "OBSERVED"}
            # 派发后取消可能只丢了响应，不能证明下游未执行。
            # 先尽力保存UNKNOWN再传播取消，下一次显式恢复按同operation查询。
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

        @observed("graph.reconcile_effect")
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
        from deephelp_app.adapters.observability.native_observation import graph_callbacks

        return {
            "configurable": {"thread_id": "m15-approval-v1:" + op.run_id},
            "callbacks": graph_callbacks(),
        }

    @observed("approval.pause")
    async def pause(self, operation_id: str, budget: ExecutionBudget) -> None:
        self.inject("before_checkpoint")
        op = await self.repo.get(operation_id)
        from deephelp_app.adapters.observability.native_observation import register_run

        await register_run(self.repo.pool, op.run_id, kind="pause", operation_id=operation_id)
        await operation_link(self.repo.pool, operation_id, save=True)
        graph = self.graph(budget)
        config = self.config(op)
        state = await graph.aget_state(config)
        if not state.values:
            await graph.ainvoke({"operation_id": operation_id}, config, durability="sync")

    @observed("approval.decision")
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
        """显式恢复一个已授权操作；审批与业务效果均从原repo读取。

        command绑定session/run，acquire只允许一个恢复者取得token。
        checkpoint缺口可从账本重建；IN_FLIGHT/UNKNOWN先查询下游，最后释放本次领取token。
        """
        scoped = await self.repo.scoped(identity, command.session_id, command.run_id, operation_id)
        # scoped先校验可信归属，acquire原子领取；普通消息或trace ID均不能授权resume。
        links = await operation_link(self.repo.pool, operation_id)
        with observation(
            "approval.resume",
            links=links,
            attributes={"deephelp.operation_id": operation_id, "deephelp.synthetic": True},
        ):
            from deephelp_app.adapters.observability.native_observation import register_run

            await register_run(
                self.repo.pool,
                scoped.run_id,
                kind="resume",
                operation_id=operation_id,
                predecessor=links,
            )
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
                    # 等本次图/官方保存完成；操作事实仍回repo读取，checkpoint不是账本。
                return await self.repo.get(operation_id)
            finally:
                # 当前恢复调用拥有领取token，无论成功/失败/取消都释放，不删除UNKNOWN事实。
                await self.repo.release(operation_id, token)
