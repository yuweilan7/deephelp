"""The single serializable contract for the current read-only core.

Validation proves structure and local invariants, never upstream execution or authorization.
Trusted entry points inject identity; business consumers must check ownership and evidence.
"""

import hashlib
import json
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    model_validator,
)

Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r"^\S+$")]
RawText = Annotated[str, Field(strict=True, min_length=1, max_length=16000, pattern=r"\S")]
Count = Annotated[int, Field(strict=True, ge=0)]
PositiveCount = Annotated[int, Field(strict=True, ge=1)]
CONTRACT_VERSION: Final = "0.2.0-m02"
REGISTRY_VERSION: Final = "complaints-v1"
POLICY_VERSION: Final = "slot-policy-v1"


def decimal_input(value: object) -> object:
    if not isinstance(value, (str, Decimal)):
        raise ValueError("Use a decimal string or Decimal, never binary float")
    return value


DecimalValue = Annotated[Decimal, BeforeValidator(decimal_input), Field(ge=0, allow_inf_nan=False)]


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
    schema_version: Literal["0.2.0-m02"] = CONTRACT_VERSION
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


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
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
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    UNKNOWN_INTENT = "UNKNOWN_INTENT"
    MISSING_SLOT = "MISSING_SLOT"
    NO_SOP = "NO_SOP"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    APPROVAL_INVALID = "APPROVAL_INVALID"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    OPERATION_UNKNOWN = "OPERATION_UNKNOWN"


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


class VersionManifest(DTO):
    contract: Literal["0.2.0-m02"] = CONTRACT_VERSION
    registry: Identifier | None = None
    dataset: Identifier | None = None
    split: Identifier | None = None
    embedding_signature: Identifier | None = None
    model: Identifier | None = None
    provider: Identifier | None = None
    prompt: Identifier | None = None
    sop: Identifier | None = None
    policy: Identifier | None = None
    code_commit: Identifier | None = None


class BudgetUsed(DTO):
    attempts: Count = 0
    retries: Count = 0
    tool_steps: Count = 0
    tokens: Count | None = None
    cost: DecimalValue | None = None

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


class EntityName(StrEnum):
    ORDER_ID = "order_id"
    COUPON_ID = "coupon_id"
    SKU_ID = "sku_id"
    ACTIVITY_ID = "activity_id"


class EntitySource(DTO):
    message_id: Identifier
    excerpt: RawText


class Entity(DTO):
    name: EntityName
    value: Identifier
    source: EntitySource


class EntityConflict(DTO):
    previous: Entity
    replacement: Entity
    resolution: Literal["unresolved", "explicit_correction"]

    @model_validator(mode="after")
    def distinct_values(self) -> EntityConflict:
        if self.previous.name != self.replacement.name:
            raise ValueError("A conflict concerns one entity slot")
        if self.previous.value == self.replacement.value:
            raise ValueError("A conflict must retain different values")
        if (
            self.resolution == "explicit_correction"
            and self.previous.source.message_id == self.replacement.source.message_id
        ):
            raise ValueError("A correction must retain the original and new message sources")
        return self


class IntentCode(StrEnum):
    SERVICE = "SERVICE"
    BENEFIT_ISSUE = "BENEFIT_ISSUE"
    ORDER_QUERY = "ORDER_QUERY"
    DISCOUNT_MISSING = "DISCOUNT_MISSING"
    COUPON_UNUSABLE = "COUPON_UNUSABLE"
    ORDER_ACTIVITY_QUERY = "ORDER_ACTIVITY_QUERY"

    @property
    def required_slots(self) -> tuple[EntityName, ...]:
        if self == IntentCode.COUPON_UNUSABLE:
            return (EntityName.ORDER_ID, EntityName.COUPON_ID)
        if self in {IntentCode.DISCOUNT_MISSING, IntentCode.ORDER_ACTIVITY_QUERY}:
            return (EntityName.ORDER_ID,)
        return ()

    @property
    def actionable(self) -> bool:
        return bool(self.required_slots)


class IntentDefinition(DTO):
    code: IntentCode
    label: RawText
    l1: Identifier
    l2: Identifier | None = None
    l3: Identifier | None = None
    l4: Identifier | None = None
    parent_code: IntentCode | None = None
    is_actionable: bool
    required_slots: tuple[EntityName, ...] = ()
    sop_id: Identifier | None = None
    version: Literal["complaints-v1"] = REGISTRY_VERSION

    @model_validator(mode="after")
    def current_catalog(self) -> IntentDefinition:
        levels = (self.l1, self.l2, self.l3, self.l4)
        seen_null = False
        for level in levels:
            if level is None:
                seen_null = True
            elif seen_null:
                raise ValueError("Explicit hierarchy cannot have gaps")
        if self.is_actionable != self.code.actionable:
            raise ValueError("Actionability must agree with the published code")
        if self.required_slots != self.code.required_slots:
            raise ValueError("Slots must agree with the published code")
        if self.is_actionable != (self.sop_id is not None):
            raise ValueError("Only actionable intents bind a SOP")
        return self


class EvidenceSource(StrEnum):
    MESSAGE = "message"
    TOOL = "tool"
    DATASET = "dataset"


class EvidenceRef(DTO):
    evidence_id: Identifier
    source: EvidenceSource
    record_id: Identifier
    version: Identifier | None = None
    locator: Identifier | None = None
    summary: RawText | None = None
    content_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None


class FactKind(StrEnum):
    TEXT = "text"
    MONEY = "money"
    FLAG = "flag"


class Fact(DTO):
    name: Identifier
    kind: FactKind
    value: StrictStr | StrictBool | Money
    evidence_ids: tuple[Identifier, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def typed_value(self) -> Fact:
        expected = {FactKind.TEXT: str, FactKind.MONEY: Money, FactKind.FLAG: bool}[self.kind]
        if type(self.value) is not expected:
            raise ValueError("Fact value must match its declared kind")
        return self


class ScoreKind(StrEnum):
    COSINE = "cosine"
    BM25 = "bm25"
    FUSION = "fusion"
    CLASSIFIER_PROBABILITY = "classifier_probability"
    RULE = "rule"


class IntentCandidate(DTO):
    code: IntentCode
    rank: PositiveCount
    score: float = Field(allow_inf_nan=False)
    score_kind: ScoreKind

    @model_validator(mode="after")
    def valid_candidate(self) -> IntentCandidate:
        if not self.code.actionable:
            raise ValueError("Candidates must be actionable codes")
        if self.score_kind == ScoreKind.COSINE and not -1 <= self.score <= 1:
            raise ValueError("Cosine score must be in [-1, 1]")
        if self.score_kind in {ScoreKind.CLASSIFIER_PROBABILITY, ScoreKind.RULE}:
            if not 0 <= self.score <= 1:
                raise ValueError("Probability/rule score must be in [0, 1]")
        elif self.score_kind != ScoreKind.COSINE and self.score < 0:
            raise ValueError("BM25/fusion scores must be nonnegative")
        return self


class Decision(StrEnum):
    ACCEPT = "accept"
    CLARIFY = "clarify"
    HANDOFF = "handoff"
    REJECT = "reject"


class IntentOverride(DTO):
    override_rule_id: Literal["prefer_complete_slots_v1"]
    reason: Literal["top1_missing_slots_selected_complete"]
    evidence_refs: tuple[EvidenceRef, ...] = Field(min_length=1)


class IntentDecision(DTO):
    decision: Decision
    final_code: IntentCode | None = None
    candidates: tuple[IntentCandidate, ...] = ()
    available_slots: tuple[EntityName, ...] = ()
    stage_evidence: tuple[EvidenceRef, ...] = ()
    reason_code: Identifier
    registry_version: Literal["complaints-v1"] = REGISTRY_VERSION
    policy_version: Literal["slot-policy-v1"] = POLICY_VERSION
    override: IntentOverride | None = None

    @model_validator(mode="after")
    def selection_policy(self) -> IntentDecision:
        codes = [candidate.code for candidate in self.candidates]
        if len(codes) != len(set(codes)):
            raise ValueError("Duplicate candidate code")
        if [candidate.rank for candidate in self.candidates] != list(range(1, len(codes) + 1)):
            raise ValueError("Candidates must be explicitly ranked starting at 1")
        if len(self.available_slots) != len(set(self.available_slots)):
            raise ValueError("Duplicate available slot")
        if self.decision == Decision.ACCEPT and self.final_code is None:
            raise ValueError("Accept needs a final code")
        if self.final_code is None:
            if self.override is not None:
                raise ValueError("Override needs a selection")
            return self
        if self.final_code not in codes:
            raise ValueError("Final selection must be an actionable candidate")
        slots = set(self.available_slots)
        complete = set(self.final_code.required_slots) <= slots
        if self.decision == Decision.ACCEPT and not complete:
            raise ValueError("Missing required slots must clarify, not accept")
        if self.final_code != codes[0]:
            if self.override is None:
                raise ValueError("Non-top1 selection requires a registered override")
            if set(codes[0].required_slots) <= slots or not complete:
                raise ValueError("Override must prove top1 missing slots and selected complete")
            if not all(ref.source == EvidenceSource.MESSAGE for ref in self.override.evidence_refs):
                raise ValueError("Slot override needs message evidence")
        elif self.override is not None:
            raise ValueError("Top1 selection does not use an override")
        return self


class Question(DTO):
    question_id: Identifier
    identity: VerifiedIdentity
    session_id: Identifier
    status: QuestionStatus
    version: PositiveCount
    entities: tuple[Entity, ...] = ()
    conflicts: tuple[EntityConflict, ...] = ()
    member_message_ids: tuple[Identifier, ...] = Field(min_length=1)
    active_intent: IntentCode | None = None
    versions: VersionManifest
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def provenance_and_versions(self) -> Question:
        names = [entity.name for entity in self.entities]
        if len(names) != len(set(names)):
            raise ValueError(
                "Current entities have one value per slot; retain conflicts separately"
            )
        sources = [entity.source.message_id for entity in self.entities]
        for conflict in self.conflicts:
            sources.extend(
                [conflict.previous.source.message_id, conflict.replacement.source.message_id]
            )
            if (
                conflict.resolution == "explicit_correction"
                and conflict.replacement not in self.entities
            ):
                raise ValueError("Corrected value must be the current entity")
        if not set(sources) <= set(self.member_message_ids):
            raise ValueError("Entity source must belong to this question")
        if self.updated_at < self.created_at:
            raise ValueError("Update cannot precede creation")
        if self.active_intent is not None:
            if (
                not self.active_intent.actionable
                or self.versions.registry != REGISTRY_VERSION
                or self.versions.sop is None
            ):
                raise ValueError("Active intent needs pinned compatible registry/SOP versions")
        if self.status == QuestionStatus.WAITING_APPROVAL:
            raise ValueError("Approval execution is disabled until M15")
        return self


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

    @model_validator(mode="after")
    def read_only_parameters(self) -> ToolRequest:
        if self.parameters_hash != tool_parameters_hash(self.parameters):
            raise ValueError("Tool parameters hash mismatch")
        if self.tool_name == ToolName.CHECK_COUPON and self.parameters.coupon_id is None:
            raise ValueError("Coupon tool requires coupon_id")
        if self.tool_name == ToolName.GET_ORDER_BENEFITS and self.parameters.coupon_id is not None:
            raise ValueError("Order tool does not accept a coupon parameter")
        return self


def validate_facts(
    facts: tuple[Fact, ...] | list[Fact], refs: tuple[EvidenceRef, ...] | list[EvidenceRef]
) -> None:
    ids = [ref.evidence_id for ref in refs]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate evidence id")
    if any(not set(fact.evidence_ids) <= set(ids) for fact in facts):
        raise ValueError("Facts must reference supplied evidence")


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


class SOPStatus(StrEnum):
    RESOLVED = "RESOLVED"
    WAITING_SLOT = "WAITING_SLOT"
    HANDED_OFF = "HANDED_OFF"
    FAILED = "FAILED"


class NextAction(StrEnum):
    NONE = "none"
    PROVIDE_SLOTS = "provide_slots"
    CHOOSE_QUESTION = "choose_question"
    CONTACT_SUPPORT = "contact_support"
    RETRY_LATER = "retry_later"
    FIX_REQUEST = "fix_request"


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

    @model_validator(mode="after")
    def sop_closeout(self) -> SOPResult:
        validate_facts(self.facts, self.evidence_refs)
        calls = {ref.record_id for ref in self.evidence_refs if ref.source == EvidenceSource.TOOL}
        if calls != set(self.tool_call_ids):
            raise ValueError("SOP tool calls and evidence must match")
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
        return self


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

    @model_validator(mode="after")
    def closeout(self) -> ResponseEnvelope:
        validate_facts(self.facts, self.evidence_refs)
        calls = {ref.record_id for ref in self.evidence_refs if ref.source == EvidenceSource.TOOL}
        if calls != set(self.tool_call_ids):
            raise ValueError("Response tool calls and evidence must match")
        if (
            self.outcome == Outcome.PENDING_APPROVAL
            or self.question_status == QuestionStatus.WAITING_APPROVAL
            or self.run_status == RunStatus.WAITING_APPROVAL
        ):
            raise ValueError("Approval execution is disabled until M15")
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
            if self.outcome != Outcome.REJECTED or self.tool_call_ids or self.facts:
                raise ValueError("Forbidden request is rejected without tool calls or facts")
        return self
