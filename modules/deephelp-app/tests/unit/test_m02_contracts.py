import json
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files

import pytest
from pydantic import ValidationError

from deephelp_app.domain.checks import (
    response_from_sop,
    validate_intent_entities,
    validate_question_access,
    validate_tool_context,
    validate_tool_result,
)
from deephelp_app.domain.models import (
    BudgetSnapshot,
    BudgetUsed,
    ConverseInput,
    Entity,
    EntityConflict,
    EntityName,
    EntitySource,
    ErrorCode,
    ErrorDetail,
    EvidenceRef,
    Fact,
    IntentCandidate,
    IntentDecision,
    Money,
    Outcome,
    Question,
    QuestionStatus,
    RequestEnvelope,
    ResponseEnvelope,
    SOPResult,
    ToolParameters,
    ToolRequest,
    ToolResult,
    VerifiedIdentity,
    VersionManifest,
    tool_parameters_hash,
)
from deephelp_app.domain.registry import IntentRegistry, complaint_registry
from deephelp_app.errors import HTTP_STATUS, AppError
from deephelp_app.execution import ExecutionBudget
from deephelp_app.learning.fakes import FakeRepository

pytestmark = pytest.mark.unit
NOW = datetime(2026, 10, 2, tzinfo=UTC)


@pytest.fixture
def identity():
    return VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a")


@pytest.fixture
def envelope(message, identity):
    return RequestEnvelope(
        **message, identity=identity, request_id="req-a", trace_id="trace-a", received_at=NOW
    )


def entity(value="000001", message_id="message-1", name=EntityName.ORDER_ID):
    return Entity(name=name, value=value, source=EntitySource(message_id=message_id, excerpt=value))


def question(identity, **changes):
    data = dict(
        question_id="q-a",
        identity=identity,
        session_id="session-1",
        status="ACTIVE",
        version=1,
        entities=(entity(),),
        member_message_ids=("message-1",),
        active_intent="DISCOUNT_MISSING",
        versions=VersionManifest(registry="complaints-v1", sop="discount-missing-v1"),
        created_at=NOW,
        updated_at=NOW,
    )
    return Question(**(data | changes))


def evidence(**changes):
    return EvidenceRef(
        **(
            dict(evidence_id="ev-a", source="tool", record_id="call-a", version="synthetic-v1")
            | changes
        )
    )


def fact(**changes):
    return Fact(
        **(
            dict(
                name="discount",
                kind="money",
                value=Money(amount=Decimal("10.00")),
                evidence_ids=("ev-a",),
            )
            | changes
        )
    )


def tool_request(identity, **changes):
    parameters = ToolParameters(order_id="000001")
    data = dict(
        operation_id="op-a",
        run_id="run-a",
        question_id="q-a",
        identity=identity,
        tool_name="get_order_benefits",
        tool_version="synthetic-v1",
        parameters=parameters,
        parameters_hash=tool_parameters_hash(parameters),
        budget=BudgetSnapshot(
            remaining_seconds=5, remaining_attempts=3, retry_remaining=1, used=BudgetUsed()
        ),
    )
    return ToolRequest(**(data | changes))


def test_m02_json_examples_and_unconfigured_versions():
    examples = json.loads(
        files("deephelp_app").joinpath("assets/learning/examples.json").read_text(encoding="utf-8")
    )
    for model, data in (
        (RequestEnvelope, examples["request"]),
        (ResponseEnvelope, examples["missing_order_response"]),
    ):
        value = model.model_validate(data)
        assert model.model_validate_json(value.model_dump_json()) == value
    response = ResponseEnvelope.model_validate(examples["missing_order_response"])
    assert (
        response.versions.model
        is response.versions.provider
        is response.versions.embedding_signature
        is None
    )
    assert response.budget_used.tokens is response.budget_used.cost is None
    assert response.schema_version == response.versions.contract == "0.2.0-m02"
    assert response.tool_call_ids == ()


def test_contract_upgrade_rejects_old_envelopes_and_new_messages_cannot_resume(envelope, message):
    with pytest.raises(ValidationError):
        RequestEnvelope.model_validate(envelope.model_dump() | {"schema_version": "0.1.2-m01"})
    ordinary = ConverseInput.model_validate(
        message | {"message_id": "new-message", "raw_text": "好的"}
    )
    assert ordinary.raw_text == "好的"
    for field in ("approval_id", "operation_id", "resume", "identity", "trace_id"):
        with pytest.raises(ValidationError):
            ConverseInput.model_validate(message | {field: "spoofed"})


@pytest.mark.parametrize("amount", [10.01, 10, "NaN", "Infinity", "-1.00", "0.001", "10.000"])
def test_money_rejects_lossy_nonfinite_or_overprecision(amount):
    with pytest.raises(ValidationError):
        Money(amount=amount)


def test_money_and_entity_leading_zero_roundtrip():
    value = Money(amount="12345678901234567890.01")
    assert json.loads(value.model_dump_json())["amount"] == "12345678901234567890.01"
    assert Money.model_validate_json(value.model_dump_json()) == value
    original = entity("000007")
    assert Entity.model_validate_json(original.model_dump_json()).value == "000007"
    with pytest.raises(ValidationError):
        Entity(name="order_id", value=7, source=original.source)


def test_entities_need_sources_and_corrections_keep_both_values(identity):
    with pytest.raises(ValidationError):
        Entity.model_validate({"name": "order_id", "value": "000007"})
    old, new = entity(), entity("000002", "message-2")
    correction = EntityConflict(previous=old, replacement=new, resolution="explicit_correction")
    corrected = question(
        identity,
        entities=(new,),
        conflicts=(correction,),
        member_message_ids=("message-1", "message-2"),
        version=2,
    )
    assert Question.model_validate_json(corrected.model_dump_json()) == corrected
    assert corrected.conflicts[0].previous.value == "000001"
    with pytest.raises(ValidationError, match="current entity"):
        question(identity, conflicts=(correction,), member_message_ids=("message-1", "message-2"))
    with pytest.raises(ValidationError, match="belong"):
        question(identity, entities=(new,))


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "CLOSED"),
        ("version", 0),
        ("active_intent", "UNKNOWN"),
        ("status", "WAITING_APPROVAL"),
    ],
)
def test_question_rejects_invalid_states_versions_and_codes(identity, field, value):
    with pytest.raises(ValidationError):
        question(identity, **{field: value})


@pytest.mark.parametrize(
    "score,kind",
    [(0.9, "classifier_probability"), (15.4, "bm25"), (-0.2, "cosine"), (0.03, "fusion")],
)
def test_scores_keep_original_meaning(score, kind):
    candidate = IntentCandidate(code="DISCOUNT_MISSING", rank=1, score=score, score_kind=kind)
    assert "confidence" not in candidate.model_dump()
    assert IntentCandidate.model_validate_json(candidate.model_dump_json()) == candidate


@pytest.mark.parametrize(
    "changes",
    [
        {"code": "UNKNOWN"},
        {"code": "SERVICE"},
        {"score_kind": "confidence"},
        {"score": 1.1},
        {"score": float("nan")},
        {"rank": 0},
    ],
)
def test_candidates_reject_unknown_code_kind_and_bad_score(changes):
    with pytest.raises(ValidationError):
        IntentCandidate.model_validate(
            dict(code="DISCOUNT_MISSING", rank=1, score=0.9, score_kind="classifier_probability")
            | changes
        )


def selection(**changes):
    data = dict(
        decision="accept",
        final_code="ORDER_ACTIVITY_QUERY",
        candidates=[
            dict(code="COUPON_UNUSABLE", rank=1, score=0.9, score_kind="classifier_probability"),
            dict(
                code="ORDER_ACTIVITY_QUERY", rank=2, score=0.8, score_kind="classifier_probability"
            ),
        ],
        available_slots=["order_id"],
        reason_code="complete_slots",
    )
    return IntentDecision.model_validate(data | changes)


def test_non_top1_requires_machine_checkable_policy_and_context():
    with pytest.raises(ValidationError, match="registered override"):
        selection()
    override = dict(
        override_rule_id="prefer_complete_slots_v1",
        reason="top1_missing_slots_selected_complete",
        evidence_refs=[evidence(source="message", record_id="message-1")],
    )
    selected = selection(override=override)
    validate_intent_entities(selected, (entity(),))
    with pytest.raises(ValidationError, match="top1 missing"):
        selection(override=override, available_slots=["order_id", "coupon_id"])
    with pytest.raises(ValidationError):
        selection(override=override | {"override_rule_id": "model_says_so"})
    with pytest.raises(AppError) as error:
        validate_intent_entities(selected, (entity(message_id="other-message"),))
    assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID
    with pytest.raises(AppError):
        validate_intent_entities(selected, ())


def test_missing_order_maps_sop_to_clarify_and_no_tool(envelope):
    result = SOPResult(
        status="WAITING_SLOT",
        sop_id="discount-missing-v1",
        sop_version="synthetic-v1",
        missing_slots=(EntityName.ORDER_ID,),
        next_action="provide_slots",
    )
    response = response_from_sop(
        envelope,
        run_id="run-a",
        question_id="q-a",
        result=result,
        reply="请提供订单号。",
        versions=VersionManifest(registry="complaints-v1"),
        budget_used=BudgetUsed(),
    )
    assert response.outcome == Outcome.CLARIFY
    assert response.question_status == QuestionStatus.WAITING_SLOT
    assert response.run_status == "SUCCEEDED"
    assert response.versions.sop == result.sop_version
    assert response.tool_call_ids == () and response.facts == []
    with pytest.raises(AppError) as error:
        response_from_sop(
            envelope,
            run_id="run-a",
            question_id="q-a",
            result=result,
            reply="Synthetic",
            versions=VersionManifest(sop="other-version"),
            budget_used=BudgetUsed(),
        )
    assert error.value.code == ErrorCode.VERSION_CONFLICT
    with pytest.raises(ValidationError):
        SOPResult.model_validate(result.model_dump() | {"tool_call_ids": ["invented-call"]})
    with pytest.raises(ValidationError):
        ResponseEnvelope.model_validate(response.model_dump() | {"outcome": "ANSWERED"})
    with pytest.raises(ValidationError, match="Missing required slots"):
        IntentDecision(
            decision="accept",
            final_code="DISCOUNT_MISSING",
            candidates=(
                IntentCandidate(code="DISCOUNT_MISSING", rank=1, score=1, score_kind="rule"),
            ),
            reason_code="rule",
        )


def test_tool_sop_response_roundtrip_requires_matching_evidence(envelope, identity):
    tool = ToolResult(
        operation_id="op-a",
        run_id="run-a",
        tool_name="get_order_benefits",
        tool_version="synthetic-v1",
        call_id="call-a",
        status="SUCCEEDED",
        facts=(fact(),),
        evidence_refs=(evidence(),),
    )
    assert ToolResult.model_validate_json(tool.model_dump_json()) == tool
    validate_tool_result(tool_request(identity), tool)
    with pytest.raises(AppError):
        validate_tool_result(
            tool_request(identity), tool.model_copy(update={"run_id": "other-run"})
        )
    sop = SOPResult(
        status="RESOLVED",
        sop_id="discount-missing-v1",
        sop_version="synthetic-v1",
        facts=tool.facts,
        evidence_refs=tool.evidence_refs,
        tool_call_ids=("call-a",),
        next_action="none",
    )
    response = response_from_sop(
        envelope,
        run_id="run-a",
        question_id="q-a",
        result=sop,
        reply="合成 fixture 显示优惠为 10.00 元。",
        versions=VersionManifest(),
        budget_used=BudgetUsed(tool_steps=1),
    )
    assert ResponseEnvelope.model_validate_json(response.model_dump_json()) == response
    for changes in (
        {"evidence_refs": []},
        {"call_id": "made-up"},
        {"status": "FAILED", "error": ErrorDetail(code="TIMEOUT", message="Timeout")},
        {"evidence_refs": [evidence(source="message")]},
    ):
        with pytest.raises(ValidationError):
            ToolResult.model_validate(tool.model_dump() | changes)
    with pytest.raises(ValidationError):
        ResponseEnvelope.model_validate(response.model_dump() | {"tool_call_ids": []})
    with pytest.raises(ValidationError):
        fact(kind="text")


@pytest.mark.parametrize(
    "owner",
    [
        VerifiedIdentity(tenant_id="other-tenant", user_id="synthetic-user-a"),
        VerifiedIdentity(tenant_id="synthetic-tenant", user_id="other-user"),
    ],
)
def test_cross_ownership_is_denied_without_object_details(envelope, owner):
    with pytest.raises(AppError) as error:
        validate_question_access(envelope, question(owner))
    assert error.value.code == ErrorCode.FORBIDDEN and error.value.status_code == 403
    assert "q-a" not in error.value.safe_message


def test_same_session_and_same_order_can_have_two_distinct_questions(envelope, identity):
    first = question(identity)
    second = question(identity, question_id="q-b", active_intent="ORDER_ACTIVITY_QUERY")
    for value in (first, second):
        validate_question_access(envelope, value)
    assert first.question_id != second.question_id
    assert first.session_id == second.session_id and first.entities == second.entities
    with pytest.raises(AppError):
        validate_question_access(envelope.model_copy(update={"question_hint": "q-b"}), first)
    with pytest.raises(AppError):
        validate_question_access(envelope.model_copy(update={"session_id": "other"}), first)


def test_tool_arguments_hash_owner_and_read_only_allowlist(identity):
    tool = tool_request(identity)
    validate_tool_context(tool, question(identity))
    assert ToolRequest.model_validate_json(tool.model_dump_json()) == tool
    for changes in (
        {"read_only": False},
        {"tool_name": "issue_refund"},
        {"parameters_hash": "0" * 64},
        {"tool_name": "check_coupon"},
    ):
        with pytest.raises(ValidationError):
            tool_request(identity, **changes)
    changed = question(identity, entities=(entity("000002"),))
    with pytest.raises(AppError) as error:
        validate_tool_context(tool, changed)
    assert error.value.code == ErrorCode.INVALID_ARGUMENT
    with pytest.raises(AppError):
        validate_tool_context(
            tool, question(VerifiedIdentity(tenant_id="other", user_id="synthetic-user-a"))
        )


def test_tool_context_refuses_incomplete_conflicting_or_exhausted_questions(identity):
    tool = tool_request(identity)
    incomplete = question(identity, active_intent="COUPON_UNUSABLE")
    with pytest.raises(AppError) as error:
        validate_tool_context(tool, incomplete)
    assert error.value.code == ErrorCode.MISSING_SLOT
    with pytest.raises(AppError):
        validate_tool_context(tool, question(identity, status="WAITING_SLOT"))
    conflict = EntityConflict(
        previous=entity(), replacement=entity("000002", "message-2"), resolution="unresolved"
    )
    with pytest.raises(AppError):
        validate_tool_context(
            tool,
            question(
                identity, conflicts=(conflict,), member_message_ids=("message-1", "message-2")
            ),
        )
    exhausted = BudgetSnapshot(
        remaining_seconds=0, remaining_attempts=1, retry_remaining=0, used=BudgetUsed()
    )
    with pytest.raises(AppError) as error:
        validate_tool_context(tool_request(identity, budget=exhausted), question(identity))
    assert error.value.code == ErrorCode.BUDGET_EXHAUSTED


async def test_budget_snapshot_is_serializable_without_clock_or_runtime_objects():
    budget = ExecutionBudget.start(5, 3, 1)
    budget.claim_attempt(retry=False)
    budget.claim_attempt(retry=True)
    snapshot = budget.snapshot()
    assert 0 < snapshot.remaining_seconds <= 5
    assert snapshot.remaining_attempts == 1 and snapshot.used.attempts == 2
    assert snapshot.used.retries == 1 and snapshot.retry_remaining == 0
    assert BudgetSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    assert snapshot.token_limit is snapshot.cost_limit is snapshot.used.tokens is None
    assert "deadline" not in snapshot.model_dump()
    budget.deadline = 0
    assert budget.snapshot().stop_reason == ErrorCode.BUDGET_EXHAUSTED


async def test_duplicate_message_replays_existing_result_but_conflicting_payload_is_409(envelope):
    repository = FakeRepository()
    original = ResponseEnvelope(
        request_id=envelope.request_id,
        trace_id=envelope.trace_id,
        run_id="run-original",
        outcome="ERROR",
        reply="Synthetic",
    )
    await repository.put(envelope, original)
    retry = envelope.model_copy(
        update={"request_id": "retry", "trace_id": "retry-trace", "received_at": datetime.now(UTC)}
    )
    await repository.put(retry, original.model_copy(update={"run_id": "must-not-replace"}))
    assert (
        await repository.get(envelope.identity, envelope.channel, envelope.message_id)
    ).run_id == "run-original"
    for changes in (
        {"raw_text": "Different payload"},
        {"session_id": "other-session"},
        {"question_hint": "other-question"},
    ):
        with pytest.raises(AppError) as error:
            await repository.put(envelope.model_copy(update=changes), original)
        assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT and error.value.status_code == 409


def test_registry_explicit_hierarchy_and_unique_codes():
    registry = complaint_registry()
    assert len(registry.actionable_codes) == 3
    assert IntentRegistry.model_validate_json(registry.model_dump_json()) == registry
    data = registry.model_dump()
    data["intents"] = [*data["intents"], data["intents"][0]]
    with pytest.raises(ValidationError, match="exactly once"):
        IntentRegistry.model_validate(data)
    data = registry.model_dump(mode="json")
    data["intents"][-1]["parent_code"] = "BENEFIT_ISSUE"
    with pytest.raises(ValidationError, match="explicit"):
        IntentRegistry.model_validate(data)


def test_error_mapping_and_unbound_approval_are_rejected():
    assert set(HTTP_STATUS) == set(ErrorCode)
    assert (
        HTTP_STATUS[ErrorCode.IDEMPOTENCY_CONFLICT]
        == HTTP_STATUS[ErrorCode.VERSION_CONFLICT]
        == 409
    )
    with pytest.raises(ValidationError):
        ErrorDetail(code="OPERATION_UNKNOWN", message="Reconcile", retryable=True)
    with pytest.raises(ValidationError, match="bound"):
        ResponseEnvelope(
            request_id="r",
            trace_id="t",
            outcome="PENDING_APPROVAL",
            question_status="WAITING_APPROVAL",
            reply="Future",
        )
    with pytest.raises(ValidationError):
        ResponseEnvelope(request_id="r", trace_id="t", outcome="MADE_UP", reply="Invalid")
