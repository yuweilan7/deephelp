import asyncio
from datetime import timedelta

import pytest

from deephelp_app.cases_fake import MemoryCaseRepository
from deephelp_app.domain.models import (
    ClusterCitation,
    ClusterJudgement,
    DemandType,
    Entity,
    EntityName,
    EntitySource,
    ErrorCode,
    EventCandidate,
    EventMessage,
    LifecycleCommand,
    QuestionStatus,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError
from deephelp_app.event_cluster import (
    ClusterPolicy,
    ConstrainedUnionFind,
    EventAggregationService,
    PersistentEventAggregation,
)
from deephelp_app.event_replay import ReplayJudge, envelope, sequences
from deephelp_app.execution import ExecutionBudget


def budget() -> ExecutionBudget:
    return ExecutionBudget.start(30, 20, 0)


def close_command(q):
    return LifecycleCommand(
        session_id=q.session_id,
        expected_version=q.version,
        target=QuestionStatus.CANCELLED,
        reason="合成关闭证据",
        evidence_source="operator_cancel",
        evidence_ref="m11-synthetic",
    )


@pytest.mark.parametrize("sequence", sequences(), ids=lambda s: s["id"])
async def test_golden_sequences(sequence):
    repo, judge = MemoryCaseRepository(), ReplayJudge()
    events = PersistentEventAggregation(repo, EventAggregationService(repo, judge))
    aliases = {}
    for turn in sequence["turns"]:
        judge.target = aliases.get(turn["event"]) if turn["disposition"] == "attached" else None
        judge.relation = "correction" if turn.get("correction") else "supplement"
        response = await events.process(envelope(turn["text"], sequence["id"]), budget())
        result = response.event_cluster
        assert result and result.persisted
        assert result.disposition == turn["disposition"]
        assert len(result.events) == turn["events"]
        assert response.intent_decision is None and not response.tool_call_ids
        if turn["event"] in aliases:
            assert response.question_id == aliases[turn["event"]]
        aliases[turn["event"]] = response.question_id
        q = repo.questions[response.question_id]
        values = {e.name.value: e.value for e in q.entities}
        for name, value in turn.get("entities", {}).items():
            assert values[name] == value
        if turn.get("correction"):
            assert q.conflicts[-1].resolution == "explicit_correction"
            assert q.conflicts[-1].previous.value == "000007"
            assert q.conflicts[-1].replacement.value == "000008"
            assert any(e.reason == "correction_membership_no_equivalence" for e in result.edges)
        if turn.get("close"):
            await repo.transition(q.identity, q.question_id, close_command(q))
        if turn.get("waiting"):
            await repo.transition(
                q.identity,
                q.question_id,
                LifecycleCommand(
                    session_id=q.session_id,
                    expected_version=q.version,
                    target=QuestionStatus.WAITING_SLOT,
                    reason="缺槽合成证据",
                    evidence_source="slot_check",
                    evidence_ref="m11-synthetic",
                ),
            )
    assert judge.calls <= len(sequence["turns"])


def message(node, order=None, question=None):
    entities = (
        (
            Entity(
                name=EntityName.ORDER_ID,
                value=order,
                source=EntitySource(message_id=node, excerpt=order),
            ),
        )
        if order
        else ()
    )
    return EventMessage(
        node_id=node,
        message_id=node,
        channel="test",
        question_id=question,
        cleaned_text="合成文本",
        entities=entities,
        demand_type=DemandType.MAIN,
        end=4,
    )


def test_transitive_bridge_checks_whole_component():
    uf = ConstrainedUnionFind((message("a", "0007"), message("ambiguous"), message("b", "0008")))
    assert uf.union("a", "ambiguous")
    assert not uf.union("ambiguous", "b")
    assert uf.find("a") != uf.find("b")


def test_same_order_independent_persisted_anchors_never_union():
    uf = ConstrainedUnionFind((message("a", "0007", "first"), message("b", "0007", "second")))
    assert not uf.union("a", "b")


@pytest.mark.parametrize(
    "changes", [{"top_k": 0}, {"judge_timeout": 70}, {"window_count": 200}, {"summary_chars": 9000}]
)
def test_invalid_bounds(changes):
    with pytest.raises(ValueError):
        ClusterPolicy(**changes)


async def seed(repo, text="订单000007优惠没到账", session="s"):
    processor = PersistentEventAggregation(repo, EventAggregationService(repo, ReplayJudge()))
    response = await processor.process(envelope(text, session), budget())
    return processor, repo.questions[response.question_id]


async def test_duplicate_replays_without_window_judge_or_extraction():
    repo = MemoryCaseRepository()
    events = PersistentEventAggregation(repo, EventAggregationService(repo, ReplayJudge()))
    request = envelope("订单000007优惠没到账", "s")
    first = await events.process(request, budget())

    async def fail(*args, **kwargs):
        raise AssertionError("replay must not aggregate")

    events.service.aggregate = fail
    second = await events.process(request, budget())
    assert second.replayed and second.run_id == first.run_id
    assert second.budget_used.attempts == 0
    with pytest.raises(AppError) as error:
        await events.process(request.model_copy(update={"raw_text": "changed"}), budget())
    assert error.value.code == ErrorCode.IDEMPOTENCY_CONFLICT


async def test_cross_user_and_session_do_not_see_candidates():
    repo = MemoryCaseRepository()
    _, q = await seed(repo)
    service = EventAggregationService(repo, ReplayJudge())
    for changes in (
        {"identity": VerifiedIdentity(tenant_id="other", user_id="synthetic-user-a")},
        {"identity": VerifiedIdentity(tenant_id="synthetic-tenant", user_id="other")},
        {"session_id": "other"},
    ):
        result = await service.aggregate(envelope("补充订单000007", "s", **changes), budget())
        assert result.disposition == "clarify"
        assert not result.candidates and all(m.question_id != q.question_id for m in result.window)


async def test_database_failure_closes_stateful_path_preview_explicitly_stays_stateless():
    repo = MemoryCaseRepository()

    async def fail(*args, **kwargs):
        raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "database down")

    repo.snapshot = fail
    service = EventAggregationService(repo, ReplayJudge())
    with pytest.raises(AppError):
        await service.aggregate(envelope("订单000007优惠没到账", "s"), budget())
    result = await service.aggregate(
        envelope("订单000007优惠没到账", "s"), budget(), stateless_preview=True
    )
    assert not result.persisted and result.diagnostics == ("stateless_current_message_only",)


@pytest.mark.parametrize("failure", ["target", "quote", "low", "correction"])
async def test_untrusted_judge_never_merges_invalid_or_low_confidence(failure):
    repo = MemoryCaseRepository()
    _, q = await seed(repo)

    class Judge:
        async def judge(self, current, candidates, messages, budget):
            return ClusterJudgement(
                decision="attach",
                target_id="unknown" if failure == "target" else q.question_id,
                confidence="low" if failure == "low" else "high",
                relation="correction" if failure == "correction" else "supplement",
                citations=(
                    ClusterCitation(
                        message_id=current.message_id,
                        quote="invented" if failure == "quote" else current.cleaned_text,
                    ),
                    ClusterCitation(
                        message_id=messages[0].message_id, quote=messages[0].cleaned_text
                    ),
                ),
            )

    result = await EventAggregationService(repo, Judge()).aggregate(
        envelope("补充订单000007", "s"), budget()
    )
    assert result.disposition == "clarify" and result.target_question_id is None


async def test_semantic_failure_creates_no_similarity_edge():
    repo, judge = MemoryCaseRepository(), ReplayJudge()
    _, q = await seed(repo)
    judge.target, judge.relation = q.question_id, "supplement"

    class Similarity:
        async def candidates(self, *args, **kwargs):
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "index unavailable")

    result = await EventAggregationService(repo, judge, similarity=Similarity()).aggregate(
        envelope("补充订单000007", "s"), budget()
    )
    assert "similarity_unavailable_no_prelinks" in result.diagnostics
    assert all(c.raw_score is None for c in result.candidates)


async def test_stale_closed_hits_and_low_score_are_excluded():
    repo = MemoryCaseRepository()
    _, q = await seed(repo)
    old = q
    q = await repo.transition(q.identity, q.question_id, close_command(q))

    class Similarity:
        async def candidates(self, *args, **kwargs):
            return (
                EventCandidate(
                    question_id=old.question_id,
                    version=old.version,
                    message_ids=old.member_message_ids,
                    text="旧ACTIVE",
                    raw_score=0.99,
                    score_kind="cosine",
                ),
            )

    # Add another eligible event so that actual retrieval takes place.
    await seed(repo, "订单000008参加的哪个活动")
    result = await EventAggregationService(repo, ReplayJudge(), similarity=Similarity()).aggregate(
        envelope("补充订单000007", "s"), budget()
    )
    assert result.disposition == "clarify"
    assert any(c.excluded_reason == "stale_closed_or_scope" for c in result.candidates)


async def test_cas_race_does_not_mutate_membership():
    repo, judge = MemoryCaseRepository(), ReplayJudge()
    _, q = await seed(repo)
    judge.target, judge.relation = q.question_id, "supplement"
    service = EventAggregationService(repo, judge)
    result = await service.aggregate(envelope("补充订单000007", "s"), budget())
    await repo.transition(
        q.identity,
        q.question_id,
        LifecycleCommand(
            session_id=q.session_id,
            expected_version=q.version,
            target=QuestionStatus.WAITING_SLOT,
            reason="合成版本变更",
            evidence_source="slot_check",
            evidence_ref="m11-synthetic",
        ),
    )
    with pytest.raises(AppError) as error:
        await repo.accept(
            envelope("补充订单000007", "s"),
            attribution=(result.target_question_id, result.expected_version),
        )
    assert error.value.code == ErrorCode.VERSION_CONFLICT


async def test_ordered_acceptance_and_original_payload_hash():
    repo = MemoryCaseRepository()
    _, q = await seed(repo)
    request = envelope("补充订单000007", "s")
    receipt = await repo.accept(request, attribution=(q.question_id, q.version))
    assert (await repo.lookup(request)).run_id == receipt.run_id
    assert request.question_hint is None
    with pytest.raises(AppError):
        await repo.accept(
            envelope("补充订单000007", "s", occurred_at=request.occurred_at - timedelta(days=1)),
            attribution=(q.question_id, receipt.question.version),
        )


async def test_judge_cancellation_is_propagated_without_acceptance():
    repo = MemoryCaseRepository()
    await seed(repo)

    class Judge:
        async def judge(self, *args):
            raise asyncio.CancelledError

    before = len(repo.rows)
    processor = PersistentEventAggregation(repo, EventAggregationService(repo, Judge()))
    with pytest.raises(asyncio.CancelledError):
        await processor.process(envelope("补充订单000007", "s"), budget())
    assert len(repo.rows) == before


async def test_topk_and_window_bounds():
    repo, judge = MemoryCaseRepository(), ReplayJudge()
    for index in range(8):
        await seed(repo, f"另外订单{index:06}优惠没到账")
    result = await EventAggregationService(
        repo, judge, policy=ClusterPolicy(top_k=2, window_count=3)
    ).aggregate(envelope("补充优惠", "s"), budget())
    assert len(result.window) <= 4
    assert judge.calls <= 1
    assert "bounded_window_trimmed" in result.diagnostics
    assert "candidate_topk_trimmed" in result.diagnostics


async def test_explicit_hint_conflict_preserves_confirmed_entity_and_unrelated_supplement():
    repo = MemoryCaseRepository()
    events, q = await seed(repo)
    response = await events.process(
        envelope("订单000008", "s", question_hint=q.question_id), budget()
    )
    assert response.question_id == q.question_id
    q = repo.questions[q.question_id]
    assert q.entities[0].value == "000007" and q.unresolved_fields == (EntityName.ORDER_ID,)
    await events.process(envelope("补充券C001", "s", question_hint=q.question_id), budget())
    q = repo.questions[q.question_id]
    assert q.unresolved_fields == (EntityName.ORDER_ID,)


async def test_explicit_hint_can_clarify_an_ambiguous_pending_event():
    repo = MemoryCaseRepository()
    events = PersistentEventAggregation(repo, EventAggregationService(repo, ReplayJudge()))
    response = await events.process(envelope("还是不行", "s"), budget())
    q = repo.questions[response.question_id]
    assert q.aggregation_pending
    response = await events.process(
        envelope("订单000007优惠没到账", "s", question_hint=q.question_id), budget()
    )
    assert response.event_cluster.disposition == "attached"
    assert not repo.questions[q.question_id].aggregation_pending


@pytest.mark.parametrize(
    "text",
    [
        "订单000007优惠没到账，订单000008参加的哪个活动",
        "订单000007优惠没到账；订单000007参加的哪个活动",
    ],
)
async def test_two_independent_complaints_even_on_same_order(text):
    repo = MemoryCaseRepository()
    result = await EventAggregationService(repo, ReplayJudge()).aggregate(
        envelope(text, "s"), budget()
    )
    assert result.disposition == "clarify" and len(result.events) == 2
    assert result.current_event_context is None


async def test_low_similarity_cannot_attach_without_entity_evidence():
    repo, judge = MemoryCaseRepository(), ReplayJudge()
    _, q = await seed(repo)
    judge.target, judge.relation = q.question_id, "supplement"

    class Similarity:
        async def candidates(self, *args, **kwargs):
            return (
                EventCandidate(
                    question_id=q.question_id,
                    version=q.version,
                    message_ids=q.member_message_ids,
                    text="合成候选",
                    raw_score=0.01,
                    score_kind="cosine",
                ),
            )

    result = await EventAggregationService(repo, judge, similarity=Similarity()).aggregate(
        envelope("补充优惠问题", "s"),
        budget(),
    )
    assert result.disposition == "clarify" and judge.calls == 0
    assert any(c.excluded_reason == "low_similarity" for c in result.candidates)


async def test_judge_timeout_does_not_accept_message():
    repo = MemoryCaseRepository()
    await seed(repo)

    class Judge:
        async def judge(self, *args):
            await asyncio.Event().wait()

    events = PersistentEventAggregation(
        repo,
        EventAggregationService(
            repo,
            Judge(),
            policy=ClusterPolicy(judge_timeout=0.01),
        ),
    )
    before = len(repo.rows)
    with pytest.raises(AppError) as error:
        await events.process(envelope("补充订单000007", "s"), budget())
    assert error.value.code == ErrorCode.TIMEOUT and len(repo.rows) == before


@pytest.mark.parametrize("decision,relation", [("new", "correction"), ("uncertain", "supplement")])
def test_judgement_cannot_combine_inconsistent_fields(decision, relation):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ClusterJudgement(
            decision=decision, target_id=None, confidence="high", relation=relation, citations=()
        )


def test_bounded_summary_retains_current_message_and_full_source_references():
    service = EventAggregationService(
        MemoryCaseRepository(), ReplayJudge(), policy=ClusterPolicy(summary_chars=256)
    )
    old = message("old").model_copy(update={"cleaned_text": "旧消息" * 200})
    current = message("current").model_copy(update={"cleaned_text": "补充订单000008"})
    context = service.context("event", None, [old, current], (), (), ())
    assert "[current] 补充订单000008" in context.summary and len(context.summary) <= 256
    assert context.message_ids == ("old", "current")
