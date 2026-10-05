"""受控专业 Agent 的唯一委托与回执值，供 SOP、账本和工作台复用。

任务固定可信父链、槽位来源与许可；预算只存引用/快照，不复制可变额度。
回执中的工具观察才承载事实，模型只返回已登记证据 ID，不产生新的业务事实。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .common import DTO, BudgetSnapshot, ErrorDetail, Identifier, PositiveCount, VerifiedIdentity
from .entities import Entity
from .model_io import ModelUsage
from .release import VersionManifest
from .tools import ToolName, ToolParameters, ToolResult, ToolStatus


class AgentTaskStatus(StrEnum):
    COMPLETED = "COMPLETED"
    NEEDS_INPUT = "NEEDS_INPUT"
    REFUSED = "REFUSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


class AgentTask(DTO):
    """代码生成的只读子任务；正文不能指定身份、角色、父问题或扩展许可。"""

    task_id: Identifier
    parent_run_id: Identifier
    parent_question_id: Identifier
    session_id: Identifier
    identity: VerifiedIdentity
    question_version: PositiveCount
    coordinator: Literal["sop-coordinator-v1"] = "sop-coordinator-v1"
    role_id: Identifier
    role_version: Identifier
    policy_digest: Identifier
    node_id: Identifier
    goal: str = Field(min_length=1, max_length=1000)
    slots: tuple[Entity, ...]
    allowed_tools: tuple[ToolName, ...] = Field(min_length=1, max_length=1)
    parameters: ToolParameters
    versions: VersionManifest
    budget_ref: Identifier
    budget_at_dispatch: BudgetSnapshot
    depth: PositiveCount = 1


class AgentDecision(DTO):
    """一次实际模型建议的公开标识和已知 usage；不保存隐藏推理。"""

    phase: Literal["delegate", "select_tool", "return_evidence"]
    call_id: Identifier
    model: Identifier
    provider_request_id: Identifier | None = None
    usage: ModelUsage


class AgentReceipt(DTO):
    task: AgentTask
    status: AgentTaskStatus
    decisions: tuple[AgentDecision, ...] = ()
    observation: ToolResult | None = None
    evidence_ids: tuple[Identifier, ...] = ()
    started_at: AwareDatetime
    finished_at: AwareDatetime
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def grounded(self) -> AgentReceipt:
        if self.finished_at < self.started_at:
            raise ValueError("Task completion cannot precede dispatch")
        if self.observation and (
            self.observation.run_id != self.task.parent_run_id
            or self.observation.tool_name not in self.task.allowed_tools
        ):
            raise ValueError("Receipt observation differs from delegation")
        if self.status == AgentTaskStatus.COMPLETED:
            if (
                not self.observation
                or self.observation.status != ToolStatus.SUCCEEDED
                or self.error
                or set(self.evidence_ids) != {r.evidence_id for r in self.observation.evidence_refs}
                or len(self.evidence_ids) != len(set(self.evidence_ids))
                or tuple(d.phase for d in self.decisions)
                != ("delegate", "select_tool", "return_evidence")
            ):
                raise ValueError("Completed task requires three decisions and verified evidence")
        elif self.evidence_ids:
            raise ValueError("Incomplete task cannot claim returned evidence")
        return self
