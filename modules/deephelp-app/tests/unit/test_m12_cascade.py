import asyncio

import pytest
from pydantic import ValidationError

from deephelp_app.cascade import CascadePolicy, ScoreGate, StructuredFallback
from deephelp_app.domain.models import (
    ChatResult,
    Decision,
    DenseCandidate,
    DenseHit,
    DenseResult,
    EntityName,
    ErrorCode,
    FallbackResult,
    IntentCode,
    ModelUsage,
)
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.evaluation.cascade_tune import select_gate
from deephelp_app.execution import ExecutionBudget
from deephelp_app.intent import IntentService
from deephelp_app.learning.event_replay import envelope
from deephelp_app.learning.mvp_replay import REPLAY_SCOPE
from deephelp_app.text_entity import TextEntityProcessor

pytestmark = pytest.mark.unit


def result(
    score=0.9, second=0.4, *, kind="cosine", constraints=None, code=IntentCode.DISCOUNT_MISSING
):
    return DenseResult(
        scope=REPLAY_SCOPE,
        hits=(
            DenseHit(
                doc_id="a",
                intent_code=code,
                content="示例优惠未到账",
                raw_score=score,
                score_kind=kind,
                scope=REPLAY_SCOPE,
                fusion_score=score if kind == "fusion" else None,
                matched_sources=("dense", "bm25") if kind == "fusion" else (),
                metadata={"entity_constraints": constraints or {}},
            ),
        ),
        candidates=(
            DenseCandidate(
                intent_code=code, rank=1, raw_score=score, score_kind=kind, evidence_doc_id="a"
            ),
            DenseCandidate(
                intent_code=IntentCode.COUPON_UNUSABLE,
                rank=2,
                raw_score=second,
                score_kind=kind,
                evidence_doc_id="b",
            ),
        ),
    )


class Retrieval:
    def __init__(self, *rows, fail=None, delay=0):
        self.rows, self.fail, self.delay, self.queries = list(rows), fail, delay, []

    async def retrieve(self, text, scope, budget, *, top_k=3):
        self.queries.append(text)
        budget.claim_attempt(retry=False)
        await asyncio.sleep(self.delay)
        budget.remaining_seconds()
        if self.fail:
            raise self.fail
        return self.rows.pop(0)


class Fallback:
    def __init__(self, row=None):
        self.row, self.calls, self.context = (
            row or FallbackResult(code=IntentCode.DISCOUNT_MISSING, reason="supported"),
            0,
            None,
        )

    async def decide(self, text, retrievals, budget, *, context=None):
        self.calls += 1
        self.context = context
        budget.claim_attempt(retry=False)
        return self.row


async def recognize(
    text="订单000031价格一分没便宜",
    *,
    rows=None,
    policy=None,
    fallback=None,
    memory=None,
    budget=None,
    context=None,
):
    retrieval = Retrieval(*(rows or [result()]))
    fallback = fallback or Fallback()
    service = IntentService(
        None,
        retrieval,
        REPLAY_SCOPE,
        policy=policy or CascadePolicy(cosine=ScoreGate(0.8, 0.2)),
        fallback=fallback,
    )
    budget = budget or ExecutionBudget.start(10, 10, 0)
    extracted = await TextEntityProcessor().process(envelope(text, "test"), budget)
    decision = await service.recognize(
        extracted, budget, memory_query=memory, context_intent=context
    )
    return decision, retrieval, fallback


async def test_rule_layer_and_missing_slot_are_model_free():
    decision, retrieval, fallback = await recognize("我的优惠没到账")
    assert (
        decision.final_code == IntentCode.DISCOUNT_MISSING and decision.decision == Decision.CLARIFY
    )
    assert not retrieval.queries and not fallback.calls
    assert [s.layer for s in decision.cascade_steps] == ["rule"]


async def test_current_layer_preserves_actual_ranked_scores():
    decision, retrieval, fallback = await recognize()
    assert (
        decision.reason_code == "current_retrieval_selected"
        and decision.decision == Decision.ACCEPT
    )
    assert decision.candidates[0].score == 0.9 and decision.candidates[1].score == 0.4
    assert len(retrieval.queries) == 1 and not fallback.calls


async def test_memory_layer_only_after_current_declines():
    decision, retrieval, fallback = await recognize(
        rows=[result(0.6), result(0.95)],
        policy=CascadePolicy(cosine=ScoreGate(0.8, 0.2), memory_cosine=ScoreGate(0.9, 0.3)),
        memory="有出处的优惠事件，补充订单000031",
    )
    assert decision.reason_code == "memory_retrieval_selected" and len(retrieval.queries) == 2
    assert [s.layer for s in decision.cascade_steps] == ["rule", "current", "memory"]
    assert not fallback.calls


async def test_fallback_after_both_retrievals_decline_and_context_is_explicit():
    decision, retrieval, fallback = await recognize(
        rows=[result(0.5), result(0.5)], memory="已确认当前事件优惠原文"
    )
    assert decision.reason_code == "fallback_supported" and len(retrieval.queries) == 2
    assert fallback.calls == 1 and fallback.context == "已确认当前事件优惠原文"
    assert decision.cascade_steps[-1].model_calls == 1


@pytest.mark.parametrize(
    "row,reason",
    [
        (result(0.7), "below_threshold"),
        (result(0.9, 0.85), "close_candidates"),
        (result(constraints={"order_id": "999999"}), "entity_counterevidence"),
    ],
)
async def test_counterevidence_prevents_score_takeover(row, reason):
    decision, _, fallback = await recognize(rows=[row])
    assert fallback.calls == 1 and decision.cascade_steps[1].reason == reason


async def test_source_scores_cannot_share_a_threshold():
    decision, _, fallback = await recognize(rows=[result(kind="fusion")])
    assert fallback.calls == 1 and decision.cascade_steps[1].reason == "score_takeover_disabled"


async def test_unresolved_entities_are_never_available_to_tool_decision():
    decision, _, fallback = await recognize("订单000031、000042 价格一分没便宜")
    assert (
        EntityName.ORDER_ID not in decision.available_slots
        and decision.decision == Decision.CLARIFY
    )
    assert fallback.calls == 1


@pytest.mark.parametrize(
    "row",
    [
        FallbackResult(code=None, reason="unknown"),
        FallbackResult.model_construct(code="INVENTED", reason="supported"),
        FallbackResult.model_construct(code=IntentCode.DISCOUNT_MISSING, reason="unknown"),
    ],
)
async def test_all_layers_reject_or_invalid_registry_code_handoff(row):
    decision, _, fallback = await recognize(rows=[result(0.5)], fallback=Fallback(row))
    assert decision.final_code is None and decision.decision == Decision.HANDOFF
    assert fallback.calls == 1


async def test_multiple_intents_clarify_without_retrieval_or_fallback():
    decision, retrieval, fallback = await recognize("优惠没到账，同时券不能用")
    assert decision.decision == Decision.CLARIFY and decision.final_code is None
    assert not retrieval.queries and not fallback.calls


async def test_declined_query_stops_all_layers():
    decision, retrieval, fallback = await recognize("不用查订单000031优惠")
    assert (
        decision.reason_code == "user_declined_query"
        and not retrieval.queries
        and not fallback.calls
    )


async def test_pinned_context_is_single_decision_without_reclassification():
    decision, retrieval, fallback = await recognize(
        "订单000031", context=IntentCode.DISCOUNT_MISSING
    )
    assert (
        decision.reason_code == "confirmed_event_context" and decision.decision == Decision.ACCEPT
    )
    assert not retrieval.queries and not fallback.calls


async def test_wrong_pinned_topic_fails_before_tools():
    for text in ("订单000031券不能用", "新问题，订单000031优惠没到账"):
        with pytest.raises(AppError) as error:
            await recognize(text, context=IntentCode.DISCOUNT_MISSING)
        assert error.value.code == ErrorCode.INVALID_ARGUMENT


async def test_budget_after_retrieval_prevents_fallback_dispatch():
    fallback = Fallback()
    with pytest.raises(AppError) as error:
        await recognize(
            rows=[result(0.5)], fallback=fallback, budget=ExecutionBudget.start(10, 1, 0)
        )
    assert error.value.code == ErrorCode.BUDGET_EXHAUSTED
    assert fallback.calls == 0


async def test_timeout_and_cancellation_stop_downstream():
    retrieval, fallback = Retrieval(result(0.5), delay=0.2), Fallback()
    text = await TextEntityProcessor().process(
        envelope("价格一分没便宜", "s"), ExecutionBudget.start(1, 5, 0)
    )
    service = IntentService(
        None, retrieval, REPLAY_SCOPE, policy=CascadePolicy(), fallback=fallback
    )
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(0.02):
            await service.recognize(text, ExecutionBudget.start(1, 5, 0))
    assert not fallback.calls
    task = asyncio.create_task(service.recognize(text, ExecutionBudget.start(1, 5, 0)))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not fallback.calls


async def test_service_failure_does_not_loop_through_model():
    retrieval, fallback = (
        Retrieval(fail=AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "synthetic")),
        Fallback(),
    )
    text = await TextEntityProcessor().process(
        envelope("价格一分没便宜", "s"), ExecutionBudget.start(1, 5, 0)
    )
    service = IntentService(
        None, retrieval, REPLAY_SCOPE, policy=CascadePolicy(), fallback=fallback
    )
    with pytest.raises(AppError):
        await service.recognize(text, ExecutionBudget.start(1, 5, 0))
    assert len(retrieval.queries) == 1 and not fallback.calls


async def test_identical_policy_replay_has_identical_decision():
    first, _, _ = await recognize()
    second, _, _ = await recognize()
    assert first.model_dump() == second.model_dump()


def test_dev_grid_never_accepts_a_wrong_or_unrelated_example():
    rows = [
        dict(
            eligible=True, score=0.95, margin=0.3, gold="DISCOUNT_MISSING", top1="DISCOUNT_MISSING"
        ),
        dict(eligible=True, score=0.8, margin=0.1, gold=None, top1="DISCOUNT_MISSING"),
    ]
    gate, choices = select_gate(rows)
    assert gate is not None and gate.threshold > 0.8
    assert any(c["wrong"] for c in choices)
    assert select_gate([rows[1]])[0] is None


def test_policy_scope_mismatch_requires_recalibration():
    with pytest.raises(ConfigurationError):
        IntentService(
            None, Retrieval(), REPLAY_SCOPE, policy=CascadePolicy(scope_fingerprint="different")
        )


@pytest.mark.parametrize(
    "code,reason",
    [(None, "supported"), ("INVENTED", "supported"), (IntentCode.DISCOUNT_MISSING, "unknown")],
)
def test_fallback_dto_enforces_consistency(code, reason):
    with pytest.raises(ValidationError):
        FallbackResult(code=code, reason=reason)


async def test_structured_fallback_preserves_current_and_confirmed_context():
    class Model:
        async def chat(self, request, budget):
            import json

            data = json.loads(request.messages[-1].content)
            assert data["text"] == "订单000031" and data["confirmed_event_context"] == "优惠原文"
            assert request.max_output_tokens == 2048 and not request.repair_once
            budget.claim_attempt(retry=False)
            return ChatResult(
                model="synthetic",
                structured={"code": "DISCOUNT_MISSING", "reason": "supported"},
                usage=ModelUsage(),
                finish_reason="stop",
            )

    text = await TextEntityProcessor().process(
        envelope("订单000031", "s"), ExecutionBudget.start(1, 5, 0)
    )
    row = await StructuredFallback(Model()).decide(
        text, (), ExecutionBudget.start(1, 5, 0), context="优惠原文"
    )
    assert row.code == IntentCode.DISCOUNT_MISSING
