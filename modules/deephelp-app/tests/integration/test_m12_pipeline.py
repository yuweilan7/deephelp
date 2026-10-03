import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from deephelp_app.cascade import CascadePolicy
from deephelp_app.cases_fake import MemoryCaseRepository
from deephelp_app.conversation import STAGES
from deephelp_app.domain.models import IntentCode, Outcome, QuestionStatus
from deephelp_app.event_replay import ReplayJudge, envelope
from deephelp_app.execution import ExecutionBudget
from deephelp_app.mvp_replay import ReplayAssembly
from deephelp_app.trace import MemoryTrace

pytestmark = pytest.mark.integration


@asynccontextmanager
async def service():
    repo, judge, trace = MemoryCaseRepository(), ReplayJudge(), MemoryTrace()
    assembly = ReplayAssembly(ledger=repo, event_judge=judge, policy=CascadePolicy())
    async with httpx.AsyncClient() as client, assembly.open(client, trace) as conversation:
        yield conversation, repo, judge, assembly, trace


async def run(conversation, text, **changes):
    return await conversation.run(
        envelope(text, "m12", **changes), ExecutionBudget.start(20, 40, 0)
    )


def terminal(response):
    assert tuple(s.stage for s in response.stages) == STAGES
    assert all(s.status == "completed" for s in response.stages[-2:])
    assert sum(s.intent_calls for s in response.stages) <= 1
    assert response.event_cluster and response.event_cluster.persisted
    assert (
        response.event_cluster.current_event_context is None
        or response.event_cluster.current_event_context.question_id == response.question_id
    )


async def test_interleaved_automatic_completion_uses_right_question_and_exactly_one_600():
    async with service() as (conversation, repo, judge, assembly, trace):
        a = await run(conversation, "我的订单未享受优惠")
        b = await run(conversation, "另外订单000042券不能用")
        assert a.question_status == b.question_status == QuestionStatus.WAITING_SLOT
        judge.target, judge.relation = a.question_id, "supplement"
        request = envelope("补充订单000031", "m12")
        completed = await conversation.run(request, ExecutionBudget.start(20, 40, 0))
        terminal(completed)
        assert completed.question_id == a.question_id and completed.outcome == Outcome.ANSWERED
        assert "99.90" in completed.reply and "10.00" in completed.reply
        assert completed.intent_decision.reason_code == "confirmed_event_context"
        assert sum(s.intent_calls for s in completed.stages) == 1
        assert (
            repo.questions[b.question_id].version == 2
            and not repo.questions[b.question_id].entities[0].value == "000031"
        )
        count = len(await assembly.tools.ledger())
        calls = judge.calls
        replay = await conversation.run(request, ExecutionBudget.start(20, 40, 0))
        assert (
            replay.replayed
            and replay.budget_used.attempts == 0
            and replay.run_id == completed.run_id
        )
        assert judge.calls == calls and len(await assembly.tools.ledger()) == count
        assert replay.call_counts.model_dump() == dict(
            intent_calls=0, model_calls=0, retrieval_calls=0, tool_calls=0
        )
        events = [
            e for e in trace.events if e.run_id == completed.run_id and e.event == "stage_finished"
        ]
        assert len(events) == 13 and all(e.question_id == a.question_id for e in events)


async def test_correction_retains_old_new_sources_and_never_equates_orders():
    async with service() as (conversation, repo, judge, assembly, trace):
        a = await run(conversation, "订单000053券不能用")
        judge.target, judge.relation = a.question_id, "correction"
        corrected = await run(conversation, "更正：订单000042")
        terminal(corrected)
        q = repo.questions[a.question_id]
        assert q.entities[0].value == "000042" and q.conflicts[-1].previous.value == "000053"
        assert (
            q.conflicts[-1].previous.source.message_id
            != q.conflicts[-1].replacement.source.message_id
        )
        assert corrected.missing_slots and not corrected.tool_call_ids
        assert any(
            e.reason == "correction_membership_no_equivalence"
            for e in corrected.event_cluster.edges
        )


async def test_ambiguity_and_multiple_utterances_persist_without_classification():
    async with service() as (conversation, repo, judge, assembly, trace):
        for text in ("优惠没到账，同时券不能用", "补充订单000031"):
            response = await run(conversation, text)
            terminal(response)
            assert response.outcome == Outcome.CLARIFY and not response.tool_call_ids
            assert sum(s.intent_calls for s in response.stages) == 0
            assert repo.questions[response.question_id].aggregation_pending
        assert not await assembly.tools.ledger()


async def test_missing_sop_and_unknown_complete_terminal_nodes_and_do_not_invent_flow():
    async with service() as (conversation, repo, judge, assembly, trace):
        conversation.definitions.pop(IntentCode.DISCOUNT_MISSING)
        response = await run(conversation, "订单000031优惠没到账")
        terminal(response)
        assert response.error.code == "NO_SOP" and response.outcome == Outcome.HANDOFF
        assert not response.tool_call_ids
        other = await run(conversation, "另外明天天气怎么样")
        terminal(other)
        assert other.outcome == Outcome.HANDOFF and not other.tool_call_ids
        assert sum(s.retrieval_calls for s in other.stages) == 1


async def test_preparation_deadline_still_saves_terminal_after_acceptance():
    async with service() as (conversation, repo, judge, assembly, trace):

        class SlowText:
            async def process(self, *args, **kwargs):
                await asyncio.sleep(0.2)

        conversation.text = SlowText()
        request = envelope("订单000031优惠没到账", "m12")
        response = await conversation.run(request, ExecutionBudget.start(0.02, 40, 0))
        assert response.outcome == Outcome.ERROR and response.stages[-2].status == "completed"
        assert response.stages[-1].status == "completed" and not response.tool_call_ids
        assert (await repo.lookup(request)).response is not None


async def test_explicit_foreign_hint_does_not_write_business_data():
    async with service() as (conversation, repo, judge, assembly, trace):
        from deephelp_app.errors import AppError

        request = envelope("订单000031", "m12", question_hint="foreign")
        with pytest.raises(AppError):
            await conversation.run(request, ExecutionBudget.start(20, 40, 0))
        assert not repo.messages and not await assembly.tools.ledger()


async def test_duplicate_concurrency_has_one_business_execution():
    async with service() as (conversation, repo, judge, assembly, trace):
        request = envelope("订单000031优惠没到账", "m12")
        responses = await asyncio.gather(
            *(conversation.run(request, ExecutionBudget.start(20, 40, 0)) for _ in range(6))
        )
        assert sum(not r.replayed for r in responses) == 1 and len(repo.messages) == 1
        assert len(await assembly.tools.ledger()) == 1


async def test_generic_query_phrase_does_not_create_a_second_complaint():
    async with service() as (conversation, repo, judge, assembly, trace):
        response = await run(conversation, "订单000031未享受优惠，请查一下")
        terminal(response)
        assert response.outcome == Outcome.ANSWERED
        assert sum(s.intent_calls for s in response.stages) == 1


async def test_generic_order_lookup_prefix_stays_one_event():
    async with service() as (conversation, repo, judge, assembly, trace):
        response = await run(conversation, "请核查订单000031，结算优惠没有到账")
        assert response.event_cluster.disposition == "new"
        assert sum(s.intent_calls for s in response.stages) == 1
        assert not repo.questions[response.question_id].aggregation_pending


async def test_coupon_supplement_cannot_pollute_an_open_discount_question():
    async with service() as (conversation, repo, judge, assembly, trace):
        a = await run(conversation, "我的订单未享受优惠")
        b = await run(conversation, "另外订单000042券不能用")
        judge.target, judge.relation = a.question_id, "supplement"
        response = await run(conversation, "补充：券000009")
        terminal(response)
        assert response.question_id == b.question_id and response.outcome == Outcome.ANSWERED
        excluded = next(
            c for c in response.event_cluster.candidates if c.question_id == a.question_id
        )
        assert excluded.excluded_reason == "slot_not_required_by_event"
        assert "unique_confirmed_missing_slot" in response.event_cluster.diagnostics
        assert (
            repo.questions[a.question_id].version == 2
            and repo.questions[a.question_id].entities == ()
        )


async def test_unclassified_predecessor_event_can_reach_memory_cascade():
    async with service() as (conversation, repo, judge, assembly, trace):
        from deephelp_app.event_cluster import PersistentEventAggregation

        seeded = await PersistentEventAggregation(repo, conversation.events).process(
            envelope("SKU-C9 订单000053 免息活动名称查询", "m12"), ExecutionBudget.start(20, 40, 0)
        )
        assert not repo.questions[seeded.question_id].aggregation_pending
        judge.target, judge.relation = seeded.question_id, "supplement"
        response = await run(conversation, "补充：请继续查询")
        terminal(response)
        assert response.question_id == seeded.question_id
        assert sum(s.intent_calls for s in response.stages) == 1
        assert any(s.layer == "memory" for s in response.intent_decision.cascade_steps)


async def test_rejected_decision_does_not_dispatch_tools_but_saves_terminal():
    async with service() as (conversation, repo, judge, assembly, trace):
        from deephelp_app.domain.models import Decision, IntentDecision

        async def reject(*args, **kwargs):
            return IntentDecision(decision=Decision.REJECT, reason_code="synthetic_safety_reject")

        conversation.intent.recognize = reject
        response = await run(conversation, "订单000031未享受优惠")
        terminal(response)
        assert response.outcome == Outcome.REJECTED and not response.tool_call_ids
        assert sum(s.intent_calls for s in response.stages) == 1
        assert not await assembly.tools.ledger()


async def test_handoff_with_registered_code_still_never_executes_sop():
    async with service() as (conversation, repo, judge, assembly, trace):
        from deephelp_app.domain.models import Decision, IntentCandidate, IntentDecision, ScoreKind

        async def handoff(*args, **kwargs):
            return IntentDecision(
                decision=Decision.HANDOFF,
                final_code=IntentCode.DISCOUNT_MISSING,
                candidates=(
                    IntentCandidate(
                        code=IntentCode.DISCOUNT_MISSING, rank=1, score=1, score_kind=ScoreKind.RULE
                    ),
                ),
                reason_code="explicit_handoff",
            )

        conversation.intent.recognize = handoff
        response = await run(conversation, "订单000031未享受优惠")
        terminal(response)
        assert response.outcome == Outcome.HANDOFF and not await assembly.tools.ledger()


async def test_classifier_ambiguity_is_saved_pending_and_excluded_from_auto_candidates():
    async with service() as (conversation, repo, judge, assembly, trace):
        from deephelp_app.domain.models import Decision, IntentCandidate, IntentDecision, ScoreKind

        async def clarify(*args, **kwargs):
            return IntentDecision(
                decision=Decision.CLARIFY,
                final_code=IntentCode.DISCOUNT_MISSING,
                candidates=(
                    IntentCandidate(
                        code=IntentCode.DISCOUNT_MISSING, rank=1, score=1, score_kind=ScoreKind.RULE
                    ),
                ),
                reason_code="intent_needs_confirmation",
            )

        conversation.intent.recognize = clarify
        response = await run(conversation, "订单000031未享受优惠")
        terminal(response)
        assert (
            response.outcome == Outcome.CLARIFY
            and repo.questions[response.question_id].aggregation_pending
        )
        assert not await assembly.tools.ledger()
        assert response.stages[6].status == "completed" and response.stages[6].error_code is None


async def test_cancellation_after_dispatch_preserves_call_id_and_terminal():
    async with service() as (conversation, repo, judge, assembly, trace):
        executor = conversation.sop

        class CancelAfterTool:
            async def execute(self, *args, **kwargs):
                await executor.execute(*args, **kwargs)
                raise asyncio.CancelledError

        conversation.sop = CancelAfterTool()
        request = envelope("订单000031未享受优惠", "m12")
        with pytest.raises(asyncio.CancelledError):
            await conversation.run(request, ExecutionBudget.start(20, 40, 0))
        saved = await repo.lookup(request)
        assert saved.response.run_status == "CANCELLED" and saved.response.tool_call_ids
        assert saved.question.status == QuestionStatus.CANCELLED and not saved.response.facts
        assert all(s.status == "completed" for s in saved.response.stages[-2:])


async def test_preparation_service_failure_saves_error_without_running_business_nodes():
    async with service() as (conversation, repo, judge, assembly, trace):
        from deephelp_app.domain.models import ErrorCode
        from deephelp_app.errors import AppError

        class FailedMemory:
            async def load(self, *args, **kwargs):
                raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "synthetic memory failure")

        conversation.memory = FailedMemory()
        response = await run(conversation, "订单000031未享受优惠")
        assert (
            response.outcome == Outcome.ERROR
            and response.error.code == ErrorCode.UPSTREAM_UNAVAILABLE
        )
        assert all(s.status == "completed" for s in response.stages[-2:])
        assert sum(s.intent_calls for s in response.stages) == 0 and not response.tool_call_ids
        assert not await assembly.tools.ledger()
