"""原 SOP lookup 的受控委托用例，复用其派发/事实守卫而不复制引擎。

AgentPolicy 是版本化角色与权限资产；CollaborationSession 由一次 execute 拥有。
lookup 顺序等待协调建议、专业决策、原工具和证据回收；所有 await 借用同一预算。
模型只建议代码已固定的任务/参数/证据，分支、事实冲突及审批仍由原 SOP 决定。
"""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from deephelp_app.adapters.mcp.business.mcp_protocol import tool_schema
from deephelp_app.adapters.observability.telemetry import annotate, observed
from deephelp_app.domain.checks import validate_question_access
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.collaboration import (
    AgentDecision,
    AgentReceipt,
    AgentTask,
    AgentTaskRecord,
    AgentTaskStatus,
)
from deephelp_app.domain.models.common import DTO, ErrorCode, ErrorDetail, QuestionStatus
from deephelp_app.domain.models.conversation import Question, RequestEnvelope
from deephelp_app.domain.models.model_io import ChatMessage, ChatRequest, ModelTool, ModelToolCall
from deephelp_app.domain.models.tools import ToolName, ToolParameters, ToolResult, ToolStatus
from deephelp_app.domain.ports import AgentTaskJournalPort, ChatPort


class AgentRole(DTO):
    """已登记专业角色；每个角色的工具权限只覆盖一种现有只读能力。"""

    role_id: Literal["order-benefits", "coupon-status"]
    version: str = Field(min_length=1)
    tool: ToolName
    goal: str = Field(min_length=1, max_length=1000)
    prompt: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def permission(self) -> AgentRole:
        allowed = {
            "order-benefits": ToolName.GET_ORDER_BENEFITS,
            "coupon-status": ToolName.CHECK_COUPON,
        }
        if self.tool != allowed[self.role_id]:
            raise ValueError("Role cannot acquire another role's tools")
        return self


class AgentPolicy(DTO):
    version: str = Field(min_length=1)
    coordinator_prompt: str = Field(min_length=1, max_length=4000)
    max_tasks: int = Field(default=2, ge=1, le=4)
    max_depth: Literal[1] = 1
    max_task_attempts: int = Field(default=6, ge=1, le=8)
    task_seconds: float = Field(default=80, gt=0, le=90)
    total_seconds: float = Field(default=90, gt=0, le=90)
    roles: tuple[AgentRole, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def registered(self) -> AgentPolicy:
        if len({r.role_id for r in self.roles}) != len(self.roles):
            raise ValueError("Agent roles must be unique")
        return self

    @property
    def digest(self) -> str:
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
        )


def load_agent_policy(path: Path | None = None) -> AgentPolicy:
    """只读取显式资产；不打开模型/工具，也不从正文发现任意角色。"""
    source = path or files("deephelp_app").joinpath("resources/sop_data/agent-policy-v2.json")
    return AgentPolicy.model_validate_json(source.read_text(encoding="utf-8"))


def validate_agent_task(
    task: AgentTask, question: Question, context: RequestEnvelope, run_id: str, policy: AgentPolicy
) -> None:
    """接收子任务前回查原授权、版本、来源与许可，拒绝伪造父链和过期上下文。"""
    validate_question_access(context, question)
    role = next((r for r in policy.roles if r.role_id == task.role_id), None)
    if (
        task.parent_run_id != run_id
        or task.parent_question_id != question.question_id
        or task.session_id != context.session_id
        or task.identity != context.identity
        or task.budget_ref != run_id
        or question.status != QuestionStatus.ACTIVE
    ):
        raise AppError(ErrorCode.FORBIDDEN, "Delegation parent or owner differs")
    if (
        task.question_version != question.version
        or task.versions != question.versions
        or task.policy_digest != policy.digest
        or question.versions.collaboration != policy.digest
    ):
        raise AppError(ErrorCode.VERSION_CONFLICT, "Delegation version differs")
    values = task.parameters.model_dump(mode="json", exclude_none=True)
    slots = tuple(e for e in question.entities if e.name.value in values)
    if (
        not role
        or task.role_version != role.version
        or task.allowed_tools != (role.tool,)
        or task.goal != role.goal
        or task.depth > policy.max_depth
        or task.slots != slots
        or {e.name.value: e.value for e in slots} != values
        or task.task_id
        != "agent-" + hashlib.sha256(f"{run_id}:{task.node_id}".encode()).hexdigest()[:32]
    ):
        raise AppError(ErrorCode.FORBIDDEN, "Delegation role, source or permission differs")


class CollaborationSession:
    def __init__(
        self, model: ChatPort, policy: AgentPolicy, journal: AgentTaskJournalPort | None = None
    ) -> None:
        self.model, self.policy = model, AgentPolicy.model_validate(policy.model_dump())
        self.journal = journal
        self.receipts: list[AgentReceipt] = []
        self._seen: set[str] = set()

    @observed("agent.decision")
    async def _decision(
        self,
        messages: list[ChatMessage],
        name: str,
        expected: dict[str, object],
        budget: ExecutionBudget,
        decisions: list[AgentDecision],
        phase: Literal["delegate", "select_tool", "return_evidence"],
        model_seconds: float,
        *,
        parameters: dict[str, object] | None = None,
    ) -> ModelToolCall:
        """在原预算中等待一次受限原生建议，验证公开动作后记录已知 usage。"""
        annotate(**{"deephelp.agent_phase": phase})
        # 仅暴露当前动作，返回仍逐字段核验；tool_choice不能替代代码许可。
        # 原生工具协议显式声明标量/数组类型；返回的值仍与expected逐项相同才接收。
        schema = parameters or {
            "type": "object",
            "properties": {
                k: {
                    "type": "array",
                    "items": {"type": "string", "enum": v},
                    "minItems": len(v),
                    "maxItems": len(v),
                    "uniqueItems": True,
                }
                if isinstance(v, list)
                else {"type": "string", "enum": [v]}
                for k, v in expected.items()
            },
            "required": list(expected),
            "additionalProperties": False,
        }
        async with asyncio.timeout(min(model_seconds, budget.remaining_seconds())):
            result = await self.model.chat(
                ChatRequest(
                    messages=messages,
                    tools=[ModelTool(name=name, parameters=schema)],
                    tool_choice=name,
                    max_output_tokens=4096,
                    repair_once=True,
                ),
                budget,
            )
        if result.finish_reason == "length" or len(result.tool_calls) != 1:
            raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Agent requires one complete action")
        action = result.tool_calls[0]
        decisions.append(
            AgentDecision(
                phase=phase,
                call_id=action.call_id,
                model=result.model,
                provider_request_id=result.provider_request_id,
                usage=result.usage,
            )
        )
        if action.name != name or action.arguments != expected:
            raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Agent action differs from trusted task")
        return action

    @observed("agent.task")
    async def lookup(
        self,
        question: Question,
        context: RequestEnvelope,
        run_id: str,
        node_id: str,
        tool: ToolName,
        parameters: ToolParameters,
        budget: ExecutionBudget,
        dispatch: Callable[[], Awaitable[ToolResult]],
        *,
        model_seconds: float,
    ) -> ToolResult:
        """委托一个已就绪节点并保存结构化回执；取消向原调用者传播。

        dispatch 由原 SOP 拥有，负责校验实际工具 ID/参数/证据；此处不重复扣费。
        子任务不创建后台 Task，取消当前 await 会沿原工具适配器释放资源。
        """
        validate_question_access(context, question)
        role = next((r for r in self.policy.roles if r.tool == tool), None)
        if not role:
            raise AppError(ErrorCode.FORBIDDEN, "No registered agent for ready tool")
        if question.versions.collaboration != self.policy.digest:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Agent policy differs from pinned question")
        expected = parameters.model_dump(mode="json", exclude_none=True)
        slots = tuple(e for e in question.entities if e.name.value in expected)
        if {e.name.value: e.value for e in slots} != expected:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Delegation slots differ from question")
        task_id = "agent-" + hashlib.sha256(f"{run_id}:{node_id}".encode()).hexdigest()[:32]
        if task_id in self._seen or len(self._seen) >= self.policy.max_tasks:
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Repeated delegation or task limit")
        self._seen.add(task_id)
        task = AgentTask(
            task_id=task_id,
            parent_run_id=run_id,
            parent_question_id=question.question_id,
            session_id=context.session_id,
            identity=context.identity,
            question_version=question.version,
            role_id=role.role_id,
            role_version=role.version,
            policy_digest=self.policy.digest,
            node_id=node_id,
            goal=role.goal,
            slots=slots,
            allowed_tools=(tool,),
            parameters=parameters,
            versions=question.versions,
            budget_ref=run_id,
            budget_at_dispatch=budget.snapshot(),
        )
        validate_agent_task(task, question, context, run_id, self.policy)
        annotate(
            **{
                "deephelp.agent_task_id": task.task_id,
                "deephelp.agent_role": task.role_id,
                "deephelp.run_id": run_id,
                "deephelp.question_id": question.question_id,
                "deephelp.policy_version": self.policy.version,
            }
        )
        record = (
            await self.journal.begin(task, context, max_tasks=self.policy.max_tasks)
            if self.journal
            else None
        )
        if record and record.state == "COMPLETED":
            assert record.receipt and record.observation
            validate_agent_task(record.task, question, context, run_id, self.policy)
            self.receipts.append(record.receipt)
            return record.observation
        started = datetime.now(UTC)
        decisions: list[AgentDecision] = []
        observation: ToolResult | None = None
        phase = "delegate"
        closed = False
        journal = self.journal

        async def progress(
            next_phase: Literal["select_tool", "tool", "return_evidence"], *, observed: bool = False
        ) -> None:
            # 每次短事务完成后才开始下一次外部等待；不跨模型/工具持行锁。
            nonlocal record, phase
            phase = next_phase
            if journal is not None and record is not None:
                record = AgentTaskRecord.model_validate(
                    record.model_copy(
                        update={
                            "state": "OBSERVED" if observed else "RUNNING",
                            "phase": next_phase,
                            "decisions": tuple(decisions),
                            "observation": observation,
                            "updated_at": datetime.now(UTC),
                        }
                    ).model_dump()
                )
                await journal.write(record)

        async def finish(receipt: AgentReceipt) -> None:
            # 本execute拥有短保存任务。取消只给落盘5秒宽限，不追加模型/工具或延长业务deadline。
            nonlocal record, closed
            if journal is not None and record is not None:
                record = AgentTaskRecord.model_validate(
                    record.model_copy(
                        update={
                            "state": receipt.status.value,
                            "phase": "closed",
                            "receipt": receipt,
                            "decisions": receipt.decisions,
                            "observation": receipt.observation,
                            "updated_at": datetime.now(UTC),
                        }
                    ).model_dump()
                )

                async def persist() -> None:
                    assert journal is not None and record is not None
                    async with asyncio.timeout(5):
                        await journal.write(record)

                writer = asyncio.create_task(persist())
                try:
                    await asyncio.shield(writer)
                except asyncio.CancelledError:
                    await writer
                    closed = True
                    self.receipts.append(receipt)
                    raise
            closed = True
            self.receipts.append(receipt)

        try:
            with budget.limit_attempts(self.policy.max_task_attempts):
                async with asyncio.timeout(
                    min(self.policy.task_seconds, budget.remaining_seconds())
                ):
                    trusted = task.model_dump_json()
                    await self._decision(
                        [
                            ChatMessage(
                                role="system",
                                content=self.policy.coordinator_prompt
                                + "\nTRUSTED TASK:\n"
                                + trusted,
                            )
                        ],
                        "delegate_task",
                        {"task_id": task_id, "role_id": role.role_id},
                        budget,
                        decisions,
                        "delegate",
                        model_seconds,
                    )
                    await progress("select_tool")
                    # 专业上下文只含本任务与原文；不继承其他 question、协调者工具权限或隐藏推理。
                    messages = [
                        ChatMessage(
                            role="system", content=role.prompt + "\nTRUSTED TASK:\n" + trusted
                        ),
                        ChatMessage(
                            role="user",
                            content=json.dumps(
                                {"untrusted_user_text": context.raw_text}, ensure_ascii=False
                            ),
                        ),
                    ]
                    action = await self._decision(
                        messages,
                        tool.value,
                        expected,
                        budget,
                        decisions,
                        "select_tool",
                        model_seconds,
                        parameters=tool_schema(tool),
                    )
                    await progress("tool")
                    observation = await dispatch()
                    if observation.run_id != run_id or observation.tool_name != tool:
                        raise AppError(
                            ErrorCode.MODEL_OUTPUT_INVALID, "Agent observation parent mismatch"
                        )
                    if observation.status != ToolStatus.SUCCEEDED:
                        await finish(
                            AgentReceipt(
                                task=task,
                                status=AgentTaskStatus.FAILED,
                                decisions=tuple(decisions),
                                observation=observation,
                                error=observation.error,
                                started_at=started,
                                finished_at=datetime.now(UTC),
                            )
                        )
                        return observation
                    await progress("return_evidence", observed=True)
                    evidence_ids = tuple(r.evidence_id for r in observation.evidence_refs)
                    await self._decision(
                        [
                            *messages,
                            ChatMessage(role="assistant", tool_calls=[action]),
                            ChatMessage(
                                role="tool",
                                tool_call_id=action.call_id,
                                content=observation.model_dump_json(),
                            ),
                            ChatMessage(
                                role="system",
                                content="仅用return_evidence返回task_id、status=COMPLETED和本工具全部evidence_ids。不要新增事实或继续查询。",
                            ),
                        ],
                        "return_evidence",
                        {
                            "task_id": task_id,
                            "status": "COMPLETED",
                            "evidence_ids": list(evidence_ids),
                        },
                        budget,
                        decisions,
                        "return_evidence",
                        model_seconds,
                    )
                    await finish(
                        AgentReceipt(
                            task=task,
                            status=AgentTaskStatus.COMPLETED,
                            decisions=tuple(decisions),
                            observation=observation,
                            evidence_ids=evidence_ids,
                            started_at=started,
                            finished_at=datetime.now(UTC),
                        )
                    )
                    return observation
        except asyncio.CancelledError:
            if closed:
                raise
            await finish(
                AgentReceipt(
                    task=task,
                    status=AgentTaskStatus.CANCELLED,
                    decisions=tuple(decisions),
                    observation=observation,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                )
            )
            raise
        except (AppError, TimeoutError) as exc:
            if closed:
                raise
            code = exc.code if isinstance(exc, AppError) else ErrorCode.TIMEOUT
            await finish(
                AgentReceipt(
                    task=task,
                    status=AgentTaskStatus.UNKNOWN
                    if code == ErrorCode.TIMEOUT or (phase == "tool" and observation is None)
                    else AgentTaskStatus.FAILED,
                    decisions=tuple(decisions),
                    observation=observation,
                    started_at=started,
                    finished_at=datetime.now(UTC),
                    error=ErrorDetail(
                        code=code, message="Delegation stopped before verified return"
                    ),
                )
            )
            raise
