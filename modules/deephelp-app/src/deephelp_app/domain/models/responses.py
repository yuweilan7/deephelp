"""主链终态、阶段计数与展示来源的响应契约。

closeout检查事实/证据、槽位、工具ID、拒绝和待审批状态的整体一致性。
缺槽回复不能夹带已执行事实，pending approval不能写成完成；诊断字段不授权新动作。
"""

from __future__ import annotations

from typing import Literal

from pydantic import (
    AwareDatetime,
    Field,
    model_validator,
)

from .collaboration import AgentReceipt
from .common import (
    CONTRACT_VERSION,
    DTO,
    BudgetUsed,
    Count,
    ErrorCode,
    ErrorDetail,
    Identifier,
    NextAction,
    Outcome,
    QuestionStatus,
    RunStatus,
)
from .entities import EntityName
from .events import EventClusterResult
from .evidence import EvidenceRef, EvidenceSource, Fact, validate_facts
from .intent_decisions import IntentDecision
from .release import VersionManifest
from .skills import SkillReceipt
from .sop import SOPPlan


class StageReport(DTO):
    stage: Identifier
    status: Literal["completed", "skipped", "disabled", "failed"]
    elapsed_ms: float = Field(default=0, ge=0, allow_inf_nan=False)
    model_calls: Count = 0
    tool_calls: Count = 0
    intent_calls: Count = 0
    retrieval_calls: Count = 0
    error_code: ErrorCode | None = None
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None


class CallCounts(DTO):
    intent_calls: Count = 0
    model_calls: Count = 0
    retrieval_calls: Count = 0
    tool_calls: Count = 0


class ReplyPresentation(DTO):
    template: str
    mode: Literal["template", "polished", "fallback"] = "template"
    reason: Literal["disabled", "accepted", "invalid_output", "unavailable", "deadline"] = (
        "disabled"
    )
    version: Literal["fact-reply-v1"] = "fact-reply-v1"


class DebugSnapshot(DTO):
    otel_trace_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    schema_version: Literal["debug-v1"] = "debug-v1"
    scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: Identifier
    question_id: Identifier
    trace_id: Identifier
    data: dict[str, object]
    incomplete: tuple[str, ...] = ()


class ResponseEnvelope(DTO):
    schema_version: Literal["0.2.0-m02"] = CONTRACT_VERSION
    request_id: Identifier
    trace_id: Identifier
    run_id: Identifier | None = None
    run_status: RunStatus | None = None
    question_id: Identifier | None = None
    outcome: Outcome
    question_status: QuestionStatus | None = None
    reply: str
    facts: list[Fact] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    tool_call_ids: tuple[Identifier, ...] = ()
    missing_slots: tuple[EntityName, ...] = ()
    next_action: NextAction | None = None
    error: ErrorDetail | None = None
    versions: VersionManifest = Field(default_factory=VersionManifest)
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)
    stages: tuple[StageReport, ...] = ()
    intent_decision: IntentDecision | None = None
    disabled_features: tuple[Identifier, ...] = ()
    replayed: bool = False
    event_cluster: EventClusterResult | None = None
    call_counts: CallCounts = Field(default_factory=CallCounts)
    sop_plan: SOPPlan | None = None
    sop_node_path: tuple[Identifier, ...] = ()
    reply_presentation: ReplyPresentation | None = None
    approval_operation_id: Identifier | None = None
    agent_receipts: tuple[AgentReceipt, ...] = ()
    skill_receipts: tuple[SkillReceipt, ...] = ()

    @model_validator(mode="after")
    def closeout(self) -> ResponseEnvelope:
        if len({r.execution_id for r in self.skill_receipts}) != len(self.skill_receipts) or any(
            r.run_id != self.run_id
            or r.question_id != self.question_id
            or r.registry_digest != self.versions.skills
            or (r.observation and r.observation.call_id not in self.tool_call_ids)
            for r in self.skill_receipts
        ):
            raise ValueError("Skill receipts must bind original run, question, registry and calls")
        skill_index = {r.execution_id: r for r in self.skill_receipts}
        if any(
            (r.parent_id and r.parent_id not in skill_index)
            or any(
                child not in skill_index or skill_index[child].parent_id != r.execution_id
                for child in r.children
            )
            or not set(r.evidence_ids) <= {e.evidence_id for e in self.evidence_refs}
            for r in self.skill_receipts
            if r.status == "COMPLETED" and r.children
        ):
            raise ValueError("Composition receipt lacks its actual child/evidence closure")
        if len({r.task.task_id for r in self.agent_receipts}) != len(self.agent_receipts):
            raise ValueError("Agent task receipts must be unique")
        if any(
            r.task.parent_run_id != self.run_id
            or r.task.parent_question_id != self.question_id
            or r.task.policy_digest != self.versions.collaboration
            or (r.observation and r.observation.call_id not in self.tool_call_ids)
            for r in self.agent_receipts
        ):
            raise ValueError("Agent receipts must belong to the original run, question and calls")
        if self.next_action == NextAction.REQUEST_APPROVAL and self.sop_plan is None:
            raise ValueError("Approval request requires a bound proposal")
        if self.sop_plan is not None and (
            self.outcome not in {Outcome.HANDOFF, Outcome.PENDING_APPROVAL}
            or self.next_action != NextAction.REQUEST_APPROVAL
            or self.sop_plan.question_id != self.question_id
            or self.sop_plan.sop_version != self.versions.sop
            or self.sop_plan.snapshot_hash != self.versions.sop_snapshot
            or self.sop_plan.skills_digest != self.versions.skills
            or not set(self.sop_plan.evidence_ids) <= {r.evidence_id for r in self.evidence_refs}
        ):
            raise ValueError("Proposal must be a bound handoff or pending approval")
        validate_facts(self.facts, self.evidence_refs)
        calls = {ref.record_id for ref in self.evidence_refs if ref.source == EvidenceSource.TOOL}
        if len(set(self.tool_call_ids)) != len(self.tool_call_ids) or not calls <= set(
            self.tool_call_ids
        ):
            raise ValueError("Response evidence must reference a unique recorded tool call")
        if (
            self.outcome == Outcome.PENDING_APPROVAL
            or self.question_status == QuestionStatus.WAITING_APPROVAL
            or self.run_status == RunStatus.WAITING_APPROVAL
        ):
            if (
                self.outcome != Outcome.PENDING_APPROVAL
                or self.question_status != QuestionStatus.WAITING_APPROVAL
                or self.run_status != RunStatus.WAITING_APPROVAL
                or self.sop_plan is None
                or self.approval_operation_id != self.sop_plan.operation_id
                or self.next_action != NextAction.REQUEST_APPROVAL
            ):
                raise ValueError("Pending approval requires a bound plan/run/operation")
        if self.run_status is not None and self.run_id is None:
            raise ValueError("Run status needs a run id")
        if self.question_status == QuestionStatus.RESOLVED:
            if self.outcome != Outcome.ANSWERED or not self.facts:
                raise ValueError("Resolved question requires an answered response with facts")
        if self.outcome == Outcome.ANSWERED and (
            not self.facts or self.error or self.missing_slots
        ):
            raise ValueError(
                "Answered response requires supported facts and no error/missing slots"
            )
        if self.question_status == QuestionStatus.WAITING_SLOT:
            if self.outcome != Outcome.CLARIFY or not self.missing_slots:
                raise ValueError("Waiting-slot question requires clarify and missing slots")
        if self.missing_slots:
            if (
                self.outcome != Outcome.CLARIFY
                or self.question_status != QuestionStatus.WAITING_SLOT
            ):
                raise ValueError("Missing slots map to CLARIFY / WAITING_SLOT")
            if self.tool_call_ids or self.facts or self.next_action != NextAction.PROVIDE_SLOTS:
                raise ValueError("Missing slots must ask for input without tools/facts")
        if self.error and self.error.code == ErrorCode.FORBIDDEN:
            rejected = self.outcome == Outcome.REJECTED and not self.tool_call_ids
            query_denied = (
                self.outcome == Outcome.ERROR
                and self.run_status == RunStatus.FAILED
                and bool(self.tool_call_ids)
            )
            if self.facts or not (rejected or query_denied):
                raise ValueError("Forbidden entry/query cannot publish object facts")
        return self
