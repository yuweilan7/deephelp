"""Canonical approval values and validation; no runtime resources."""

from __future__ import annotations

from typing import Literal

from pydantic import (
    AwareDatetime,
    Field,
    model_validator,
)

from .common import (
    DTO,
    ApprovalStatus,
    Count,
    Identifier,
    OperationStatus,
    PositiveCount,
    VerifiedIdentity,
)
from .responses import ResponseEnvelope
from .sop import SOPPlan


class ApprovalCommand(DTO):
    session_id: Identifier
    run_id: Identifier
    expected_question_version: PositiveCount
    parameters_hash: Identifier
    sop_version: Identifier
    snapshot_hash: Identifier
    decision: Literal["approve", "reject", "revoke"]


class ResumeCommand(DTO):
    session_id: Identifier
    run_id: Identifier


class OperationRecord(DTO):
    plan: SOPPlan
    run_id: Identifier
    session_id: Identifier
    question_version: PositiveCount
    status: OperationStatus = OperationStatus.PREPARED
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    expires_at: AwareDatetime
    approver: VerifiedIdentity | None = None
    dispatch_attempts: Count = 0
    query_attempts: Count = 0
    last_error: str | None = Field(default=None, max_length=256)
    pending_response: ResponseEnvelope
    response: ResponseEnvelope | None = None

    @model_validator(mode="after")
    def operation_binding(self) -> OperationRecord:
        if (
            self.pending_response.run_id != self.run_id
            or self.pending_response.question_id != self.plan.question_id
            or self.pending_response.approval_operation_id != self.plan.operation_id
            or self.question_version != self.plan.question_version + 1
        ):
            raise ValueError("Operation must bind the committed plan and run")
        return self
