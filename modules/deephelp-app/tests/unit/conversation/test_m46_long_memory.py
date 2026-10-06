"""固定来源/授权/幂等反例与原主链实验；实库协议另由显式probe验证。

独立断言检查source已结束、当前槽位不继承、外owner不泄露及版本漂移，非复述算法。
"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from deephelp_app.adapters.observability.trace import MemoryTrace
from deephelp_app.adapters.persistence.ledger import new_receipt, payload_hash
from deephelp_app.application.long_memory import LongMemoryService
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import QuestionStatus, RunStatus
from deephelp_app.domain.models.long_memory import MemoryReference, MemoryScope, MemoryUse
from deephelp_tools.learning.course.long_memory import long_memory_demo
from deephelp_tools.learning.fakes.cases_fake import MemoryCaseRepository
from deephelp_tools.learning.fakes.long_memory import OfflineLongMemory
from deephelp_tools.learning.replay.event_replay import ReplayJudge, envelope
from deephelp_tools.learning.replay.mvp_replay import ReplayAssembly

pytestmark = pytest.mark.unit


async def fixture():
    request = envelope("订单000031", "source")
    question = new_receipt(request).question.model_copy(
        update={"status": QuestionStatus.HANDED_OFF}
    )
    cases = MemoryCaseRepository()
    cases.questions[question.question_id] = question
    store = OfflineLongMemory(cases)
    service = LongMemoryService(store)
    memory = await service.preserve(request.identity, "source", question.question_id)
    scope = await service.configure(request.identity, "target", 0, True, ("source",))
    use = MemoryUse(
        scope_revision=scope.revision,
        references=(
            MemoryReference(
                memory_id=memory.memory_id,
                revision=memory.revision,
            ),
        ),
    )
    return request, question, store, service, memory, use


async def test_original_two_session_conversation_source_and_current_facts():
    result = await long_memory_demo()
    assert result["history_did_not_fill_slots"] and result["source_stayed_ended"]
    assert result["remote_model_calls"] == 0


async def test_authorized_explicit_selection_and_disabled_default():
    request, _, _, service, memory, use = await fixture()
    selected = await service.select(request.identity, "target", use)
    assert selected.decisions[0].memory == memory
    assert (await service.select(request.identity, "target", None)).status == "DISABLED"
    assert (await service.select(request.identity, "other-session", use)).decisions[
        0
    ].reason == "scope_disabled"


async def test_foreign_owner_forged_source_and_cas():
    request, question, store, service, memory, use = await fixture()
    foreign = request.identity.model_copy(update={"user_id": "foreign"})
    assert await store.get(foreign, memory.memory_id) is None
    with pytest.raises(AppError, match="not found"):
        await service.preserve(foreign, "source", question.question_id)
    with pytest.raises(AppError, match="not found"):
        await service.configure(request.identity, "target", 1, True, ("forged",))
    with pytest.raises(AppError, match="changed"):
        await service.configure(request.identity, "target", 0, True, ("source",))
    assert (await service.select(foreign, "target", use)).decisions[0].memory is None


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"version": 2}, "source_changed"),
        ({"status": QuestionStatus.ACTIVE}, "source_not_ended"),
    ],
)
async def test_source_drift_and_ended_boundary(change, reason):
    request, question, store, service, _, use = await fixture()
    store.cases.questions[question.question_id] = question.model_copy(update=change)
    decision = (await service.select(request.identity, "target", use)).decisions[0]
    assert decision.reason == reason and decision.memory is None


async def test_deleted_expired_and_revoked_content_is_not_returned():
    request, question, store, service, memory, use = await fixture()
    store.memories[memory.memory_id] = memory.model_copy(
        update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
    )
    assert (await service.select(request.identity, "target", use)).decisions[0].reason == "expired"
    store.memories[memory.memory_id] = memory.model_copy(update={"state": "REVOKED"})
    assert (await service.select(request.identity, "target", use)).decisions[
        0
    ].reason == "memory_revoked"
    store.memories[memory.memory_id] = memory
    del store.cases.questions[question.question_id]
    assert (await service.select(request.identity, "target", use)).decisions[
        0
    ].reason == "source_unavailable"


async def test_use_is_bounded_and_hash_binds_exact_selection():
    request, _, _, _, _, use = await fixture()
    with pytest.raises(ValidationError):
        MemoryUse(scope_revision=1, references=use.references * 2)
    with pytest.raises(ValidationError):
        MemoryScope(
            owner=request.identity,
            target_session="source",
            enabled=True,
            source_sessions=("source",),
        )
    old_hash = payload_hash(request)
    assert old_hash == payload_hash(request.model_copy(update={"history_memory": None}))
    assert old_hash != payload_hash(request.model_copy(update={"history_memory": use}))


async def test_cancellation_during_history_read_persists_cancelled_run(monkeypatch):
    request, _, store, service, _, use = await fixture()
    started = asyncio.Event()

    async def blocked(*args):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "select", blocked)
    assembly = ReplayAssembly(ledger=store.cases, event_judge=ReplayJudge())
    from deephelp_app.domain.execution import ExecutionBudget

    current = envelope("订单000031未享受优惠", "target").model_copy(update={"history_memory": use})
    async with httpx.AsyncClient() as client, assembly.open(client, MemoryTrace()) as conversation:
        conversation.long_memory = service
        task = asyncio.create_task(conversation.run(current, ExecutionBudget.start(20, 40, 0)))
        await asyncio.wait_for(started.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        receipt = await store.cases.lookup(current)
        assert receipt and receipt.response
        assert receipt.response.run_status == RunStatus.CANCELLED
        assert receipt.response.memory_selection.status == "CANCELLED"
        assert not receipt.response.facts
        assert receipt.response.tool_call_ids
