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
    expected_question_version: PositiveCount | None = None

    @model_validator(mode="after")
    def continuation_version(self) -> ConverseInput:
        if self.expected_question_version is not None and self.question_hint is None:
            raise ValueError("Question version requires a question hint")
        return self


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


class TextSegment(DTO):
    start: Count
    end: PositiveCount
    text: RawText

    @model_validator(mode="after")
    def valid_span(self) -> TextSegment:
        if self.end <= self.start or self.end - self.start != len(self.text):
            raise ValueError("Segment offsets must cover its text")
        return self


class TextCleanResult(DTO):
    raw_text: RawText
    cleaned_text: RawText
    segments: list[TextSegment] = Field(min_length=1, max_length=125)
    normalization_version: Literal["width-whitespace-v1"] = "width-whitespace-v1"
    truncated: Literal[False] = False

    @model_validator(mode="after")
    def complete_segments(self) -> TextCleanResult:
        position = 0
        for segment in self.segments:
            if segment.start != position:
                raise ValueError("Clean segments must be contiguous")
            position = segment.end
        if (
            position != len(self.cleaned_text)
            or "".join(segment.text for segment in self.segments) != self.cleaned_text
        ):
            raise ValueError("Clean segments must retain the whole cleaned text")
        return self


class ExtractedEntity(Entity):
    """A current-message observation; offsets address raw_text, never cleaned_text."""

    start: Count
    end: PositiveCount
    layer: Literal["regex", "api", "strong"]
    confidence_kind: Literal["deterministic", "model_grounded"]
    disposition: Literal["observed", "negated", "correction"] = "observed"

    @model_validator(mode="after")
    def valid_observation(self) -> ExtractedEntity:
        if self.end <= self.start:
            raise ValueError("Entity requires a nonempty raw span")
        if (self.layer == "regex") != (self.confidence_kind == "deterministic"):
            raise ValueError("Confidence kind must describe the extraction method")
        normalized = "".join(
            " " if c == "\u3000" else chr(ord(c) - 0xFEE0) if "\uff01" <= c <= "\uff5e" else c
            for c in self.source.excerpt
        )
        if normalized != self.value or self.end - self.start != len(self.source.excerpt):
            raise ValueError("Entity value must equal its width-normalized raw evidence")
        return self

    def as_entity(self) -> Entity:
        return Entity(name=self.name, value=self.value, source=self.source)


class DemandType(StrEnum):
    MAIN = "main"
    SUPPLEMENT = "supplement"
    NEW_TOPIC = "new_topic"
    UNKNOWN = "unknown"


class ExtractionLayerStat(DTO):
    layer: Literal["regex", "api", "strong"]
    elapsed_ms: float = Field(ge=0, allow_inf_nan=False)
    model_calls: Count = 0
    accepted: Count = 0
    rejected: Count = 0
    error_code: ErrorCode | None = None
    provider_request_id: Identifier | None = None
    input_start: Count | None = None
    input_end: PositiveCount | None = None


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


class RuleMatch(DTO):
    rule_id: Identifier
    rule_version: Identifier
    candidate_code: IntentCode
    priority: Count
    evidence: EntitySource
    start: Count
    end: PositiveCount

    @model_validator(mode="after")
    def candidate_only(self) -> RuleMatch:
        if not self.candidate_code.actionable or self.end <= self.start:
            raise ValueError("Rule output requires a leaf candidate and raw evidence")
        return self


class TextEntityResult(DTO):
    message_id: Identifier
    clean: TextCleanResult
    entities: list[Entity] = Field(default_factory=list)
    observations: list[ExtractedEntity] = Field(default_factory=list)
    conflicts: list[EntityConflict] = Field(default_factory=list)
    unresolved_fields: list[EntityName] = Field(default_factory=list)
    demand_type: DemandType
    rule_matches: list[RuleMatch] = Field(default_factory=list)
    layers: list[ExtractionLayerStat] = Field(min_length=1, max_length=17)
    model_coverage_complete: bool | None = None
    extraction_version: Literal["text-entity-v1"] = "text-entity-v1"
    prompt_version: Literal["grounded-entities-v1"] = "grounded-entities-v1"

    @model_validator(mode="after")
    def validate_evidence(self) -> TextEntityResult:
        if len({entity.name for entity in self.entities}) != len(self.entities):
            raise ValueError("Resolved entity slots must be unique")
        if len(set(self.unresolved_fields)) != len(self.unresolved_fields):
            raise ValueError("Unresolved fields must be unique")
        for observation in self.observations:
            if (
                observation.source.message_id != self.message_id
                or self.clean.raw_text[observation.start : observation.end]
                != observation.source.excerpt
            ):
                raise ValueError("Observation must reference the exact raw message span")
        for match in self.rule_matches:
            if (
                match.evidence.message_id != self.message_id
                or self.clean.raw_text[match.start : match.end] != match.evidence.excerpt
            ):
                raise ValueError("Rule evidence must reference the exact raw message span")
        return self


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
    MODEL_CHOICE = "model_choice"


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
    retrieval: DenseResult | None = None

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
    unresolved_fields: tuple[EntityName, ...] = ()
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
        for index, conflict in enumerate(self.conflicts):
            sources.extend(
                [conflict.previous.source.message_id, conflict.replacement.source.message_id]
            )
            if (
                conflict.resolution == "explicit_correction"
                and not any(
                    current.name == conflict.replacement.name
                    and current.value == conflict.replacement.value
                    for current in self.entities
                )
                and not any(
                    later.previous == conflict.replacement for later in self.conflicts[index + 1 :]
                )
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


class MemoryMessage(DTO):
    channel: Identifier
    message_id: Identifier
    question_id: Identifier
    occurred_at: AwareDatetime
    text: RawText
    truncated: bool = False


class CaseSummary(DTO):
    question_id: Identifier
    version: PositiveCount
    status: QuestionStatus
    text: RawText
    message_ids: tuple[Identifier, ...]


class MemoryWindow(DTO):
    history: tuple[MemoryMessage, ...] = ()
    active_questions: tuple[Question, ...] = ()
    closed_summaries: tuple[CaseSummary, ...] = ()
    generation: Count = 0
    trimmed: bool = False


class LifecycleCommand(DTO):
    session_id: Identifier
    expected_version: PositiveCount
    target: QuestionStatus
    reason: RawText
    evidence_source: Literal[
        "user_confirmation",
        "process_result",
        "explicit_reopen",
        "operator_cancel",
        "operator_handoff",
        "slot_check",
    ]
    evidence_ref: Identifier


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
        return self


class StageReport(DTO):
    stage: Identifier
    status: Literal["completed", "skipped", "disabled", "failed"]
    elapsed_ms: float = Field(default=0, ge=0, allow_inf_nan=False)
    model_calls: Count = 0
    tool_calls: Count = 0
    error_code: ErrorCode | None = None


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

    @model_validator(mode="after")
    def closeout(self) -> ResponseEnvelope:
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
            rejected = self.outcome == Outcome.REJECTED and not self.tool_call_ids
            query_denied = (
                self.outcome == Outcome.ERROR
                and self.run_status == RunStatus.FAILED
                and bool(self.tool_call_ids)
            )
            if self.facts or not (rejected or query_denied):
                raise ValueError("Forbidden entry/query cannot publish object facts")
        return self


class ModelUsage(DTO):
    input_tokens: Count = 0
    output_tokens: Count = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ModelToolCall(DTO):
    call_id: Identifier
    name: Identifier
    arguments: dict[str, object]


class ChatMessage(DTO):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    tool_call_id: Identifier | None = None

    @model_validator(mode="after")
    def valid_message(self) -> ChatMessage:
        if self.role == "tool" and (not self.tool_call_id or self.content is None):
            raise ValueError("Tool result requires call ID and content")
        if self.role != "tool" and self.tool_call_id is not None:
            raise ValueError("Only tool results have a tool call ID")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("Only assistant messages contain tool calls")
        if self.content is None and not self.tool_calls:
            raise ValueError("Message requires content or structured calls")
        return self


class ModelTool(DTO):
    name: Identifier
    description: str = ""
    parameters: dict[str, object]


class ChatRequest(DTO):
    messages: list[ChatMessage] = Field(min_length=1)
    max_output_tokens: PositiveCount = 2048
    enable_thinking: StrictBool | None = None
    thinking_budget: PositiveCount | None = None
    response_format: Literal["text", "json_schema", "json_object"] = "text"
    output_schema: dict[str, object] | None = None
    tools: list[ModelTool] = Field(default_factory=list)
    tool_choice: Identifier | None = None
    repair_once: bool = False

    @model_validator(mode="after")
    def valid_request(self) -> ChatRequest:
        if self.enable_thinking is False and self.thinking_budget is not None:
            raise ValueError("Thinking budget requires thinking mode")
        if (self.response_format != "text") != (self.output_schema is not None):
            raise ValueError("Structured output requires an explicit schema")
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names) or (self.tool_choice and self.tool_choice not in names):
            raise ValueError("Tool names must be unique and choice must be declared")
        if self.tools and self.output_schema is not None:
            raise ValueError("Tool and output schemas are separate capabilities")
        return self


class ChatResult(DTO):
    content: str | None = None
    structured: dict[str, object] | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    usage: ModelUsage
    finish_reason: Identifier
    provider_request_id: Identifier | None = None
    model: Identifier


class EmbeddingSignature(DTO):
    provider: Identifier
    model: Identifier
    revision: Identifier | None = None
    dimension: PositiveCount
    normalization: Literal["l2", "none"] = "l2"
    text_normalization: Literal["nfc-strip-v1"] = "nfc-strip-v1"

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "emb-sha256:" + hashlib.sha256(payload.encode()).hexdigest()


class EmbeddingResult(DTO):
    vectors: list[list[float]]
    signature: EmbeddingSignature
    usage: ModelUsage
    cache_hits: Count = 0
    provider_request_ids: list[Identifier] = Field(default_factory=list)
    position_index_fallback: bool = False

    @model_validator(mode="after")
    def valid_vectors(self) -> EmbeddingResult:
        import math

        if not self.vectors or any(
            len(row) != self.signature.dimension or not all(math.isfinite(v) for v in row)
            for row in self.vectors
        ):
            raise ValueError("Embedding vectors require finite values and the signed dimension")
        return self


class CorpusRecord(DTO):
    doc_id: Identifier
    content: Annotated[str, Field(strict=True, min_length=1, max_length=2000, pattern=r"\S")]
    intent_code: IntentCode
    split: Literal["reference", "train", "dev", "test", "regression"]
    source_group: Identifier
    variant_group: Identifier
    synthetic: Literal[True]
    label_path: tuple[Identifier, ...] | None = None
    metadata: dict[str, str] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def registered_label(self) -> CorpusRecord:
        from deephelp_app.domain.registry import complaint_registry

        definition = complaint_registry().get(self.intent_code)
        levels = tuple(x for x in (definition.l1, definition.l2, definition.l3, definition.l4) if x)
        if len(self.doc_id.encode("utf-8")) > 128:
            raise ValueError("doc_id exceeds Milvus byte limit")
        if not definition.is_actionable or (
            self.label_path is not None and self.label_path != levels
        ):
            raise ValueError("Unknown/non-actionable code or conflicting label hierarchy")
        if any(len(k) > 128 or len(v) > 512 for k, v in self.metadata.items()):
            raise ValueError("Metadata exceeds bounded string sizes")
        return self


class DenseScope(DTO):
    namespace: Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    dataset_version: Identifier
    signature: EmbeddingSignature
    registry_version: Literal["complaints-v1"] = REGISTRY_VERSION

    @model_validator(mode="after")
    def milvus_byte_limits(self) -> DenseScope:
        if len(self.dataset_version.encode("utf-8")) > 128:
            raise ValueError("dataset_version exceeds Milvus byte limit")
        return self

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()

    @property
    def collection_name(self) -> str:
        return "m05_intent_" + self.fingerprint[:32]


class HybridScope(DenseScope):
    """Immutable intent corpus; future event/history indexes need a separate scope/Port."""

    analyzer_version: Literal["jieba-search-cn-lower-v1"] = "jieba-search-cn-lower-v1"
    index_kind: Literal["intent_hybrid"]

    @property
    def collection_name(self) -> str:
        return "m09_intent_" + self.fingerprint[:32]


class DenseHit(DTO):
    doc_id: Identifier
    intent_code: IntentCode
    content: str
    raw_score: Annotated[float, Field(allow_inf_nan=False)]
    score_kind: Literal["cosine", "bm25", "fusion"] = "cosine"
    metadata: dict[str, object]
    scope: HybridScope | DenseScope
    raw_dense: Annotated[float, Field(allow_inf_nan=False)] | None = None
    raw_sparse: Annotated[float, Field(allow_inf_nan=False, ge=0)] | None = None
    fusion_score: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1.001)] | None = None
    rank: PositiveCount | None = None
    matched_sources: tuple[Literal["dense", "bm25"], ...] = ()

    @model_validator(mode="after")
    def score_range(self) -> DenseHit:
        if self.score_kind == "cosine" and not -1.001 <= self.raw_score <= 1.001:
            raise ValueError("Cosine score outside range")
        if self.score_kind == "bm25" and self.raw_score < 0:
            raise ValueError("BM25 score must be nonnegative")
        if self.score_kind == "fusion" and (
            self.fusion_score != self.raw_score or not self.matched_sources
        ):
            raise ValueError("Fusion requires score and actual route provenance")
        return self


class DenseCandidate(DTO):
    intent_code: IntentCode
    raw_score: Annotated[float, Field(allow_inf_nan=False)]
    score_kind: Literal["cosine", "bm25", "fusion"] = "cosine"
    rank: PositiveCount
    evidence_doc_id: Identifier
    aggregation: Literal["per_intent_max_v1"] = "per_intent_max_v1"


class DenseResult(DTO):
    scope: HybridScope | DenseScope
    hits: tuple[DenseHit, ...]
    candidates: tuple[DenseCandidate, ...]
    policy_version: Literal["dense-baseline-v1", "hybrid-weighted-v1"] = "dense-baseline-v1"
    retrieval_mode: Literal["dense", "bm25", "hybrid"] = "dense"
    candidate_budget: PositiveCount | None = None
    dense_weight: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None
    elapsed_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None


# Resolve the incremental evidence reference without a second family of retrieval DTOs.
IntentDecision.model_rebuild()
ResponseEnvelope.model_rebuild()
