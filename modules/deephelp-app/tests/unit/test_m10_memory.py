import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from redis.exceptions import RedisError

from deephelp_app.cases import bounded_history, summary, validate_transition
from deephelp_app.cases_fake import MemoryCaseRepository
from deephelp_app.domain.models import (
    LifecycleCommand,
    MemoryMessage,
    NextAction,
    Outcome,
    QuestionStatus,
    RequestEnvelope,
    ResponseEnvelope,
    RunStatus,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError
from deephelp_app.execution import ExecutionBudget
from deephelp_app.ledger import payload_hash
from deephelp_app.memory import MemoryService, RedisMemory, scope_key

pytestmark = pytest.mark.unit
IDENTITY = VerifiedIdentity(tenant_id="test-tenant", user_id="a")


def request(**changes):
    now = datetime.now(UTC)
    return RequestEnvelope(
        channel="test",
        session_id="s",
        message_id=uuid4().hex,
        raw_text="订单000031",
        occurred_at=now,
        received_at=now,
        request_id=uuid4().hex,
        trace_id=uuid4().hex,
        identity=IDENTITY,
    ).model_copy(update=changes)


async def terminal(repo, receipt, status=QuestionStatus.WAITING_SLOT):
    q = receipt.question.model_copy(
        update={"version": receipt.question.version + 1, "status": status}
    )
    response = ResponseEnvelope(
        request_id="r",
        trace_id="t",
        run_id=receipt.run_id,
        question_id=q.question_id,
        question_status=status,
        run_status=RunStatus.SUCCEEDED,
        outcome=Outcome.CLARIFY if status == QuestionStatus.WAITING_SLOT else Outcome.ERROR,
        next_action=NextAction.PROVIDE_SLOTS
        if status == QuestionStatus.WAITING_SLOT
        else NextAction.CONTACT_SUPPORT,
        reply="test",
        missing_slots=("order_id",) if status == QuestionStatus.WAITING_SLOT else (),
    )
    await repo.finish(receipt, q, response)
    return q


def command(q, target, source="slot_check"):
    return LifecycleCommand(
        session_id=q.session_id,
        expected_version=q.version,
        target=target,
        reason="synthetic evidence",
        evidence_source=source,
        evidence_ref="test-ref",
    )


async def test_two_questions_and_other_user_same_order_are_distinct():
    repo = MemoryCaseRepository()
    a = await repo.accept(request())
    b = await repo.accept(request())
    other = IDENTITY.model_copy(update={"user_id": "b"})
    c = await repo.accept(request(identity=other))
    assert len({r.question.question_id for r in (a, b, c)}) == 3
    window = await repo.snapshot(IDENTITY, "s")
    assert {q.question_id for q in window.active_questions} == {
        a.question.question_id,
        b.question.question_id,
    }
    assert len(window.history) == 2
    assert await repo.question(other, "s", a.question.question_id) is None
    assert scope_key(IDENTITY, "s") != scope_key(other, "s")


async def test_hint_busy_version_replay_and_out_of_order():
    repo = MemoryCaseRepository()
    original = request()
    receipt = await repo.accept(original)
    with pytest.raises(AppError, match="unfinished"):
        await repo.accept(request(question_hint=receipt.question.question_id))
    q = await terminal(repo, receipt)
    replay = await repo.accept(original)
    assert not replay.acquired and replay.response and len(repo.events) == 2
    with pytest.raises(AppError, match="payload"):
        await repo.accept(original.model_copy(update={"raw_text": "changed"}))
    with pytest.raises(AppError, match="changed"):
        await repo.accept(request(question_hint=q.question_id, expected_question_version=1))
    with pytest.raises(AppError, match="Out-of-order"):
        await repo.accept(
            request(
                question_hint=q.question_id, occurred_at=original.occurred_at - timedelta(seconds=1)
            )
        )
    next_receipt = await repo.accept(
        request(question_hint=q.question_id, expected_question_version=q.version)
    )
    assert next_receipt.question.version == 3 and len(next_receipt.question.member_message_ids) == 2


async def test_concurrent_continuations_have_one_owner_no_lost_members():
    repo = MemoryCaseRepository()
    q = await terminal(repo, await repo.accept(request()))
    rows = await asyncio.gather(
        *(
            repo.accept(request(question_hint=q.question_id, expected_question_version=q.version))
            for _ in range(8)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in rows) == 1
    assert len(repo.questions[q.question_id].member_message_ids) == 2
    assert len(repo.messages) == 2


@pytest.mark.parametrize(
    "target,source",
    [
        (QuestionStatus.ACTIVE, "slot_check"),
        (QuestionStatus.CANCELLED, "operator_cancel"),
        (QuestionStatus.HANDED_OFF, "operator_handoff"),
        (QuestionStatus.RESOLVED, "user_confirmation"),
    ],
)
async def test_lifecycle_evidence_and_cas(target, source):
    repo = MemoryCaseRepository()
    q = await terminal(repo, await repo.accept(request()))
    changed = await repo.transition(IDENTITY, q.question_id, command(q, target, source))
    assert changed.version == q.version + 1 and repo.events[-1] == changed
    with pytest.raises(AppError, match="version"):
        await repo.transition(IDENTITY, q.question_id, command(q, target, source))


@pytest.mark.parametrize("source", ["slot_check", "explicit_reopen", "operator_cancel"])
async def test_model_or_unrelated_evidence_cannot_resolve(source):
    repo = MemoryCaseRepository()
    q = await terminal(repo, await repo.accept(request()))
    with pytest.raises(AppError, match="evidence"):
        validate_transition(q, command(q, QuestionStatus.RESOLVED, source))


async def test_closed_hint_rejected_new_similar_case_and_explicit_reopen():
    repo = MemoryCaseRepository()
    q = await terminal(repo, await repo.accept(request()), QuestionStatus.CANCELLED)
    with pytest.raises(AppError, match="reopen"):
        await repo.accept(request(question_hint=q.question_id))
    fresh = await repo.accept(request())
    assert fresh.question.question_id != q.question_id
    reopened = await repo.transition(
        IDENTITY, q.question_id, command(q, QuestionStatus.ACTIVE, "explicit_reopen")
    )
    assert reopened.version == 3 and reopened.member_message_ids == q.member_message_ids
    with pytest.raises(AppError):
        await repo.transition(
            IDENTITY, q.question_id, command(reopened, QuestionStatus.WAITING_APPROVAL)
        )


async def test_time_count_and_byte_token_bounds_keep_sources():
    repo = MemoryCaseRepository()
    for _ in range(36):
        await repo.accept(request(raw_text="合成历史" * 100))
    await repo.accept(request(occurred_at=datetime.now(UTC) - timedelta(days=2)))
    window = await repo.snapshot(IDENTITY, "s", count=4, token_limit=1300, case_limit=2)
    assert window.trimmed and len(window.history) == 2 and window.history[0].truncated
    assert sum(len(r.text.encode()) for r in window.history) <= 1300
    assert len(window.active_questions) == 2
    assert all(row.message_id and row.channel and row.question_id for row in window.history)
    assert len(repo.messages) == 37


@pytest.mark.parametrize("limit", [1, 2, 3, 4, 5, 7, 100])
def test_multibyte_trimming_never_corrupts_text(limit):
    row = MemoryMessage(
        channel="c",
        message_id="m",
        question_id="q",
        occurred_at=datetime.now(UTC),
        text="中文文本abcdefgh",
    )
    history, trimmed = bounded_history([row], count=1, token_limit=limit)
    assert sum(len(r.text.encode()) for r in history) <= limit
    assert trimmed == (len(row.text.encode()) > limit)


async def test_cached_or_vector_payload_cannot_authorize_old_or_foreign_case():
    repo = MemoryCaseRepository()
    q = await terminal(repo, await repo.accept(request()), QuestionStatus.CANCELLED)
    other = await terminal(
        repo,
        await repo.accept(request(identity=IDENTITY.model_copy(update={"user_id": "b"}))),
        QuestionStatus.CANCELLED,
    )

    class Index:
        async def search(self, *args):
            return [
                summary(q.model_copy(update={"version": 1, "status": QuestionStatus.ACTIVE})),
                summary(other),
                summary(q),
            ]

    service = MemoryService(repo, index=Index())
    window = await service.load(IDENTITY, "s", ExecutionBudget.start(5, 1, 0), query="订单")
    assert window.closed_summaries == (summary(q),)


async def test_cache_unavailable_preserves_facts_and_has_diagnostic():
    repo = MemoryCaseRepository()
    receipt = await repo.accept(request())

    class Cache:
        async def put(self, *args):
            raise RedisError("synthetic unavailable")

    memory = MemoryService(repo, cache=Cache())
    window = await memory.load(IDENTITY, "s", ExecutionBudget.start(5, 1, 0))
    assert window.active_questions[0] == receipt.question
    assert memory.last_cache_error == "redis_unavailable"
    assert RedisMemory(None).key(IDENTITY, "s", 1) != RedisMemory(None).key(IDENTITY, "s", 2)


def test_expected_version_requires_hint_and_hash_preserves_old_messages():
    r = request()
    with pytest.raises(ValidationError):
        RequestEnvelope.model_validate(
            r.model_copy(update={"expected_question_version": 1}).model_dump()
        )
    assert payload_hash(r) != payload_hash(
        r.model_copy(update={"question_hint": "q", "expected_question_version": 1})
    )
