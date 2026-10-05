"""只读工具的参数、请求、观察值与内部委托，模型不能自由填写可信元数据。

参数hash绑定确认编号；SUCCEEDED必须含实际call_id与同来源证据。
ToolInvocationEnvelope只走内部签名通道，不暴露为模型可选择的工具参数。
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    Field,
    model_validator,
)

from .common import DTO, BudgetSnapshot, ErrorCode, ErrorDetail, Identifier, VerifiedIdentity
from .entities import EntityName
from .evidence import EvidenceRef, EvidenceSource, Fact, validate_facts


class ToolName(StrEnum):
    GET_ORDER_BENEFITS = "get_order_benefits"
    CHECK_COUPON = "check_coupon"


class ToolParameters(DTO):
    order_id: Identifier
    coupon_id: Identifier | None = None


def tool_parameters_hash(parameters: ToolParameters) -> str:
    payload = json.dumps(parameters.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ToolRequest(DTO):
    operation_id: Identifier
    run_id: Identifier
    question_id: Identifier
    identity: VerifiedIdentity
    tool_name: ToolName
    tool_version: Identifier
    read_only: Literal[True] = True
    parameters: ToolParameters
    parameters_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    budget: BudgetSnapshot
    timeout_seconds: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def read_only_parameters(self) -> ToolRequest:
        if self.parameters_hash != tool_parameters_hash(self.parameters):
            raise ValueError("Tool parameters hash mismatch")
        if self.tool_name == ToolName.CHECK_COUPON and self.parameters.coupon_id is None:
            raise ValueError("Coupon tool requires coupon_id")
        if self.tool_name == ToolName.GET_ORDER_BENEFITS and self.parameters.coupon_id is not None:
            raise ValueError("Order tool does not accept a coupon parameter")
        return self


class ToolStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    MISSING_SLOT = "MISSING_SLOT"
    FAILED = "FAILED"


class ToolResult(DTO):
    operation_id: Identifier
    run_id: Identifier
    tool_name: ToolName
    tool_version: Identifier
    call_id: Identifier | None = None
    upstream_request_id: Identifier | None = None
    status: ToolStatus
    facts: tuple[Fact, ...] = ()
    evidence_refs: tuple[EvidenceRef, ...] = ()
    missing_slots: tuple[EntityName, ...] = ()
    error: ErrorDetail | None = None

    @model_validator(mode="after")
    def result_evidence(self) -> ToolResult:
        validate_facts(self.facts, self.evidence_refs)
        if self.status == ToolStatus.SUCCEEDED:
            if not self.call_id or not self.evidence_refs or self.error or self.missing_slots:
                raise ValueError("Successful tool needs a call/evidence and no error/missing slots")
            if not all(
                ref.source == EvidenceSource.TOOL and ref.record_id == self.call_id
                for ref in self.evidence_refs
            ):
                raise ValueError("Tool evidence must reference this actual call")
        elif self.facts:
            raise ValueError("Failed/missing-slot tool cannot publish success facts")
        if self.status == ToolStatus.MISSING_SLOT:
            if not self.missing_slots or self.call_id is not None or self.error is not None:
                raise ValueError("Missing-slot result has no call and lists required slots")
        if self.status == ToolStatus.FAILED and (self.error is None or self.missing_slots):
            raise ValueError("Failed tool needs a classified error")
        return self


class ToolInvocationEnvelope(DTO):
    """Internal signed MCP metadata, never a model-visible tool argument."""

    request: ToolRequest
    request_id: Identifier
    trace_id: Identifier
    call_id: Identifier


class ToolInvocationRecord(DTO):
    call_id: Identifier
    operation_id: Identifier
    run_id: Identifier
    question_id: Identifier
    request_id: Identifier
    trace_id: Identifier
    identity: VerifiedIdentity
    tool_name: ToolName
    parameters: ToolParameters
    status: Literal["started", "succeeded", "failed", "cancelled"]
    error_code: ErrorCode | None = None
    evidence_ids: tuple[Identifier, ...] = ()
