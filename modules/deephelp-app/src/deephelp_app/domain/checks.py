"""Pure contract guards; downstream services still need authenticated, durable reads."""

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models import (
    BudgetUsed,
    Entity,
    ErrorCode,
    IntentCode,
    IntentDecision,
    Outcome,
    Question,
    QuestionStatus,
    RequestEnvelope,
    ResponseEnvelope,
    RunStatus,
    SOPResult,
    SOPStatus,
    ToolName,
    ToolRequest,
    ToolResult,
    VerifiedIdentity,
    VersionManifest,
)


def require_owner(identity: VerifiedIdentity, owner: VerifiedIdentity) -> None:
    if identity != owner:
        raise AppError(ErrorCode.FORBIDDEN, "Object access denied")


def validate_question_access(request: RequestEnvelope, question: Question) -> None:
    require_owner(request.identity, question.identity)
    if request.session_id != question.session_id:
        raise AppError(ErrorCode.FORBIDDEN, "Object access denied")
    if request.question_hint is not None and request.question_hint != question.question_id:
        raise AppError(ErrorCode.FORBIDDEN, "Object access denied")


def validate_intent_entities(decision: IntentDecision, entities: tuple[Entity, ...]) -> None:
    if set(decision.available_slots) != {entity.name for entity in entities}:
        raise AppError(
            ErrorCode.MODEL_OUTPUT_INVALID, "Intent slots do not match verified entities"
        )
    if decision.override is not None:
        messages = {entity.source.message_id for entity in entities}
        if not {ref.record_id for ref in decision.override.evidence_refs} <= messages:
            raise AppError(
                ErrorCode.MODEL_OUTPUT_INVALID, "Override evidence does not match entities"
            )


def validate_tool_context(request: ToolRequest, question: Question) -> None:
    require_owner(request.identity, question.identity)
    if request.question_id != question.question_id:
        raise AppError(ErrorCode.FORBIDDEN, "Object access denied")
    if question.status != QuestionStatus.ACTIVE or question.active_intent is None:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Question is not ready for tools")
    if any(conflict.resolution == "unresolved" for conflict in question.conflicts):
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Resolve entity conflicts before tools")
    values = {entity.name.value: entity.value for entity in question.entities}
    if not {slot.value for slot in question.active_intent.required_slots} <= set(values):
        raise AppError(ErrorCode.MISSING_SLOT, "Required tool slots are missing")
    expected_tool = (
        ToolName.CHECK_COUPON
        if question.active_intent == IntentCode.COUPON_UNUSABLE
        else ToolName.GET_ORDER_BENEFITS
    )
    governed_comparison = (
        question.active_intent == IntentCode.COUPON_UNUSABLE
        and question.versions.sop_registry is not None
        and request.tool_name == ToolName.GET_ORDER_BENEFITS
    )
    if request.tool_name != expected_tool and not governed_comparison:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Tool does not match active intent")
    if (
        request.budget.stop_reason is not None
        or request.budget.remaining_seconds <= 0
        or request.budget.remaining_attempts == 0
    ):
        raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Tool budget is exhausted")
    if values.get("order_id") != request.parameters.order_id:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Tool order does not match question")
    if request.parameters.coupon_id is not None:
        if values.get("coupon_id") != request.parameters.coupon_id:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Tool coupon does not match question")


def validate_tool_result(request: ToolRequest, result: ToolResult) -> None:
    if (request.operation_id, request.run_id, request.tool_name, request.tool_version) != (
        result.operation_id,
        result.run_id,
        result.tool_name,
        result.tool_version,
    ):
        raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Tool result does not match request")


def response_from_sop(
    request: RequestEnvelope,
    *,
    run_id: str,
    question_id: str,
    result: SOPResult,
    reply: str,
    versions: VersionManifest,
    budget_used: BudgetUsed,
) -> ResponseEnvelope:
    """A pure state mapping, not a SOP executor or a durable run transition."""
    if versions.sop is not None and versions.sop != result.sop_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, "SOP version does not match pinned manifest")
    outcome, status = {
        SOPStatus.RESOLVED: (Outcome.ANSWERED, QuestionStatus.RESOLVED),
        SOPStatus.WAITING_SLOT: (Outcome.CLARIFY, QuestionStatus.WAITING_SLOT),
        SOPStatus.HANDED_OFF: (Outcome.HANDOFF, QuestionStatus.HANDED_OFF),
        SOPStatus.FAILED: (Outcome.ERROR, QuestionStatus.ACTIVE),
        SOPStatus.NEEDS_APPROVAL: (Outcome.HANDOFF, QuestionStatus.HANDED_OFF),
    }[result.status]
    return ResponseEnvelope(
        request_id=request.request_id,
        trace_id=request.trace_id,
        run_id=run_id,
        run_status=RunStatus.FAILED if result.status == SOPStatus.FAILED else RunStatus.SUCCEEDED,
        question_id=question_id,
        outcome=outcome,
        question_status=status,
        reply=reply,
        facts=list(result.facts),
        evidence_refs=list(result.evidence_refs),
        tool_call_ids=result.tool_call_ids,
        missing_slots=result.missing_slots,
        next_action=result.next_action,
        error=result.error,
        versions=versions.model_copy(update={"sop": result.sop_version}),
        budget_used=budget_used,
        sop_plan=result.plan,
        sop_node_path=result.node_path,
    )
