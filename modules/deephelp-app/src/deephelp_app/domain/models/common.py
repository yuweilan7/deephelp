"""通用不可变值：身份、状态、错误、预算快照与精确金额。

DTO禁止额外字段并冻结赋值，Money拒绝float输入以避免二进制舍入进入账本。
BudgetSnapshot是相对时限的值副本，不包含事件循环、连接或可变ExecutionBudget。
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

# Annotated附加校验元数据；只有Pydantic等消费者读取它时才执行规则。
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r"^\S+$")]


RawText = Annotated[str, Field(strict=True, min_length=1, max_length=16000, pattern=r"\S")]


Count = Annotated[int, Field(strict=True, ge=0)]


PositiveCount = Annotated[int, Field(strict=True, ge=1)]


CONTRACT_VERSION: Final = "0.2.0-m02"


REGISTRY_VERSION: Final = "complaints-v1"


POLICY_VERSION: Final = "slot-policy-v1"


def decimal_input(value: object) -> object:
    # 先拒绝float，避免把二进制近似值当业务金额；不静默舍入或修正。
    if not isinstance(value, (str, Decimal)):
        raise ValueError("Use a decimal string or Decimal, never binary float")
    return value


DecimalValue = Annotated[Decimal, BeforeValidator(decimal_input), Field(ge=0, allow_inf_nan=False)]


class DTO(BaseModel):
    # frozen拒绝字段重赋值，不递归冻结嵌套集合；连接/锁属于运行对象，不属于DTO。
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerifiedIdentity(DTO):
    tenant_id: Identifier
    user_id: Identifier


class Outcome(StrEnum):
    ANSWERED = "ANSWERED"
    CLARIFY = "CLARIFY"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    HANDOFF = "HANDOFF"
    REJECTED = "REJECTED"
    ERROR = "ERROR"


class QuestionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WAITING_SLOT = "WAITING_SLOT"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    RESOLVED = "RESOLVED"
    HANDED_OFF = "HANDED_OFF"
    CANCELLED = "CANCELLED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class OperationStatus(StrEnum):
    PREPARED = "PREPARED"
    IN_FLIGHT = "IN_FLIGHT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    CANCELLED = "CANCELLED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    TIMEOUT = "TIMEOUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    UNKNOWN_INTENT = "UNKNOWN_INTENT"
    MISSING_SLOT = "MISSING_SLOT"
    NO_SOP = "NO_SOP"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    OPERATION_UNKNOWN = "OPERATION_UNKNOWN"
    PROVIDER_QUOTA_EXHAUSTED = "PROVIDER_QUOTA_EXHAUSTED"
    MODEL_CAPABILITY_UNAVAILABLE = "MODEL_CAPABILITY_UNAVAILABLE"


class ErrorDetail(DTO):
    code: ErrorCode
    message: str
    retryable: bool = False

    @model_validator(mode="after")
    def retry_policy(self) -> ErrorDetail:
        if self.retryable and self.code not in {
            ErrorCode.TIMEOUT,
            ErrorCode.UPSTREAM_UNAVAILABLE,
            ErrorCode.RATE_LIMITED,
            ErrorCode.MODEL_OUTPUT_INVALID,
            ErrorCode.VERSION_CONFLICT,
        }:
            raise ValueError("This error cannot be retried automatically")
        return self


class BudgetUsed(DTO):
    attempts: Count = 0
    retries: Count = 0
    tool_steps: Count = 0
    tokens: Count | None = None
    cost: DecimalValue | None = None
    token_upper_bound: Count | None = None
    cost_upper_bound: DecimalValue | None = None
    uncertain_attempts: Count = 0

    @model_validator(mode="after")
    def retry_count(self) -> BudgetUsed:
        if self.retries > self.attempts:
            raise ValueError("Retries are included in attempts")
        return self


class BudgetSnapshot(DTO):
    """Relative deadline snapshot; contains no event-loop clock or mutable runtime object."""

    remaining_seconds: float = Field(ge=0, allow_inf_nan=False)
    remaining_attempts: Count
    retry_remaining: Count
    used: BudgetUsed
    tool_step_limit: Count | None = None
    token_limit: Count | None = None
    cost_limit: DecimalValue | None = None
    stop_reason: ErrorCode | None = None


class Money(DTO):
    amount: DecimalValue
    currency: Literal["CNY"] = "CNY"

    @model_validator(mode="after")
    def precision(self) -> Money:
        exponent = self.amount.as_tuple().exponent
        if not isinstance(exponent, int) or exponent < -2:
            raise ValueError("CNY uses at most two fractional digits; do not silently round")
        return self


class NextAction(StrEnum):
    NONE = "none"
    PROVIDE_SLOTS = "provide_slots"
    CHOOSE_QUESTION = "choose_question"
    CONTACT_SUPPORT = "contact_support"
    RETRY_LATER = "retry_later"
    FIX_REQUEST = "fix_request"
    REQUEST_APPROVAL = "request_approval"
