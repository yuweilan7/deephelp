"""Canonical sop values and validation; no runtime resources."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import (
    Field,
    model_validator,
)

from .common import (
    DTO,
    ErrorDetail,
    Identifier,
    NextAction,
    PositiveCount,
    RawText,
    VerifiedIdentity,
)
from .entities import EntityName
from .evidence import EvidenceRef, EvidenceSource, Fact, validate_facts
from .tools import ToolParameters, tool_parameters_hash


class SOPStatus(StrEnum):
    RESOLVED = "RESOLVED"
    WAITING_SLOT = "WAITING_SLOT"
    HANDED_OFF = "HANDED_OFF"
    FAILED = "FAILED"
    NEEDS_APPROVAL = "NEEDS_APPROVAL"


class SOPPlan(DTO):
    """A bound proposal only; no approval record, execution token or business effect."""

    operation_id: Identifier
    action: Literal["simulate_discount_adjustment"]
    identity: VerifiedIdentity
    question_id: Identifier
    question_version: PositiveCount
    parameters: ToolParameters
    parameters_hash: Identifier
    sop_version: Identifier
    snapshot_hash: Identifier
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def binding(self) -> SOPPlan:
        if (
            self.parameters_hash != tool_parameters_hash(self.parameters)
            or self.parameters.coupon_id is not None
        ):
            raise ValueError("Plan parameters hash mismatch")
        return self


class SOPResult(DTO):
    status: SOPStatus
    sop_id: Identifier
    sop_version: Identifier
    facts: tuple[Fact, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    tool_call_ids: tuple[Identifier, ...] = ()
    missing_slots: tuple[EntityName, ...] = ()
    next_action: NextAction
    reason: RawText | None = None
    error: ErrorDetail | None = None
    plan: SOPPlan | None = None
    node_path: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def sop_closeout(self) -> SOPResult:
        if self.next_action == NextAction.REQUEST_APPROVAL and self.plan is None:
            raise ValueError("Approval request requires a bound proposal")
        validate_facts(self.facts, self.evidence_refs)
        calls = {ref.record_id for ref in self.evidence_refs if ref.source == EvidenceSource.TOOL}
        if len(set(self.tool_call_ids)) != len(self.tool_call_ids) or not calls <= set(
            self.tool_call_ids
        ):
            raise ValueError("SOP evidence must reference a unique recorded tool call")
        if self.status == SOPStatus.RESOLVED:
            if not self.facts or not self.evidence_refs or self.missing_slots or self.error:
                raise ValueError("Resolved SOP requires supported facts")
            if self.next_action != NextAction.NONE:
                raise ValueError("Resolved SOP has no next action")
        if self.status == SOPStatus.WAITING_SLOT:
            if not self.missing_slots or self.tool_call_ids or self.facts or self.error:
                raise ValueError("Missing-slot SOP cannot call tools or publish success facts")
            if self.next_action != NextAction.PROVIDE_SLOTS:
                raise ValueError("Missing-slot SOP asks for slots")
        if self.status == SOPStatus.FAILED and self.error is None:
            raise ValueError("Failed SOP requires an error")
        if self.status == SOPStatus.HANDED_OFF and not self.reason:
            raise ValueError("Handoff requires a reason")
        if self.status == SOPStatus.NEEDS_APPROVAL:
            if (
                self.plan is None
                or self.next_action != NextAction.REQUEST_APPROVAL
                or not self.facts
                or self.error
                or self.missing_slots
                or self.plan.sop_version != self.sop_version
                or not set(self.plan.evidence_ids) <= {r.evidence_id for r in self.evidence_refs}
            ):
                raise ValueError("Approval proposal requires a bound, evidenced plan")
        elif self.plan is not None:
            raise ValueError("Only an approval proposal may carry a plan")
        return self
