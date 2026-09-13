"""M00-compatible minimal subset, not the published M02 contract."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r"^\S+$")]
RawText = Annotated[str, Field(strict=True, min_length=1, max_length=16000, pattern=r"\S")]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerifiedIdentity(DTO):
    tenant_id: Identifier
    user_id: Identifier


class ConverseInput(DTO):
    """Untrusted message content; identity/request/trace IDs come from the entry point."""

    channel: Identifier
    session_id: Identifier
    message_id: Identifier
    raw_text: RawText
    occurred_at: AwareDatetime
    question_hint: Identifier | None = None


class RequestEnvelope(ConverseInput):
    schema_version: Literal["0.1.2-m01"] = "0.1.2-m01"
    identity: VerifiedIdentity
    request_id: Identifier
    trace_id: Identifier
    received_at: AwareDatetime


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


class ErrorCode(StrEnum):
    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    TIMEOUT = "TIMEOUT"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    RATE_LIMITED = "RATE_LIMITED"


class ErrorDetail(DTO):
    code: ErrorCode
    message: str
    retryable: bool = False


class VersionManifest(DTO):
    contract: Literal["0.1.2-m01"] = "0.1.2-m01"
    registry: str | None = None
    dataset: str | None = None
    embedding_signature: str | None = None
    model: str | None = None
    provider: str | None = None
    prompt: str | None = None
    sop: str | None = None
    policy: str | None = None
    code_commit: str | None = None


class BudgetUsed(DTO):
    attempts: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    tokens: int | None = None
    cost: str | None = None


class ResponseEnvelope(DTO):
    schema_version: Literal["0.1.2-m01"] = "0.1.2-m01"
    request_id: Identifier
    trace_id: Identifier
    run_id: Identifier | None = None
    question_id: Identifier | None = None
    outcome: Outcome
    question_status: QuestionStatus | None = None
    reply: str
    # M01 emits no business facts/evidence. M02 will publish their formal structures here.
    facts: list[dict[str, str]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    next_action: str | None = None
    error: ErrorDetail | None = None
    versions: VersionManifest = Field(default_factory=VersionManifest)
    budget_used: BudgetUsed = Field(default_factory=BudgetUsed)
