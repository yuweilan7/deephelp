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
from deephelp_app.application.long_memory import LongMemoryService, current_relevance
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import QuestionStatus, RunStatus
from deephelp_app.domain.models.long_memory import (
    MemoryChange,
    MemoryReference,
    MemoryScope,
    MemoryUse,
)
from deephelp_tools.learning.course.long_memory import long_memory_demo
from deephelp_tools.learning.fakes.cases_fake import MemoryCaseRepository
from deephelp_tools.learning.fakes.long_memory import OfflineLongMemory
from deephelp_tools.learning.replay.event_replay import ReplayJudge, envelope
from deephelp_tools.learning.replay.mvp_replay import ReplayAssembly

pytestmark = pytest.mark.unit


async def fixture():
    request = envelope("订单000031", "source")
    question = new_receipt(request).question.model_copy(update={"status": QuestionStatus.RESOLVED})
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


async def test_explicit_correction_and_forget_keep_original_business_and_version_audit():
    request, question, store, service, memory, use = await fixture()
    note = await service.change(
        request.identity,
        memory.memory_id,
        MemoryChange(expected_revision=1, action="retain", edit_id="user-retain", text="先列依据"),
    )
    corrected = await service.change(
        request.identity,
        memory.memory_id,
        MemoryChange(
            expected_revision=2, action="correct", edit_id="user-correct", text="先给结果"
        ),
    )
    assert corrected.source_hash == memory.source_hash and corrected.edit_id == "user-correct"
    assert (await service.select(request.identity, "target", use)).decisions[
        0
    ].reason == "memory_changed"
    with pytest.raises(AppError, match="changed"):
        await service.change(
            request.identity,
            memory.memory_id,
            MemoryChange(expected_revision=2, action="correct", edit_id="stale", text="覆盖"),
        )
    forgotten = await service.change(
        request.identity,
        memory.memory_id,
        MemoryChange(expected_revision=3, action="forget", edit_id="user-forget"),
    )
    assert forgotten.text == "[forgotten]"
    assert store.cases.questions[question.question_id] == question
    assert (
        await service.preserve(request.identity, "source", question.question_id)
    ).state == "FORGOTTEN"
    detail = await service.detail(request.identity, memory.memory_id)
    assert "先给结果" not in str(detail) and "先列依据" not in str(detail)
    assert [r.revision for r in await store.versions(request.identity, memory.memory_id)] == [
        4,
        3,
        2,
        1,
    ]
    assert note.text == "先列依据"
    with pytest.raises(AppError, match="cannot be revived"):
        await service.change(
            request.identity,
            memory.memory_id,
            MemoryChange(expected_revision=4, action="retain", edit_id="late", text="重新投影"),
        )


async def test_low_confidence_and_revoke_are_not_injected():
    request, _, _, service, memory, use = await fixture()
    note = await service.change(
        request.identity,
        memory.memory_id,
        MemoryChange(
            expected_revision=1,
            action="retain",
            edit_id="uncertain",
            text="可能偏好",
            confidence="LOW",
        ),
    )
    use = use.model_copy(
        update={"references": (MemoryReference(memory_id=note.memory_id, revision=2),)}
    )
    assert (await service.select(request.identity, "target", use)).decisions[
        0
    ].reason == "low_confidence"
    revoked = await service.change(
        request.identity,
        memory.memory_id,
        MemoryChange(expected_revision=2, action="revoke", edit_id="revoke"),
    )
    use = use.model_copy(
        update={
            "references": (MemoryReference(memory_id=note.memory_id, revision=revoked.revision),)
        }
    )
    assert (await service.select(request.identity, "target", use)).decisions[0].memory is None


async def test_late_revoke_during_source_read_is_caught(monkeypatch):
    request, _, store, service, memory, use = await fixture()
    original = store.source

    async def late(owner, session, question):
        result = await original(owner, session, question)
        await service.change(
            owner,
            memory.memory_id,
            MemoryChange(expected_revision=1, action="revoke", edit_id="late-revoke"),
        )
        return result

    monkeypatch.setattr(store, "source", late)
    selected = await service.select(request.identity, "target", use)
    assert selected.decisions[0].reason == "authorization_changed"
    assert selected.context_bytes == 0


async def test_context_limit_counts_utf8_and_candidates_are_bounded():
    request, question, store, service, memory, use = await fixture()
    refs = []
    for index in range(4):
        copy = memory.model_copy(update={"memory_id": f"copy-{index}", "text": "字" * 1000})
        store.memories[copy.memory_id] = copy
        refs.append(MemoryReference(memory_id=copy.memory_id, revision=1))
    use = MemoryUse(scope_revision=1, references=tuple(refs))
    selected = await service.select(request.identity, "target", use)
    assert selected.context_bytes == 6000
    assert [d.reason for d in selected.decisions][-2:] == ["context_limit"] * 2
    for index in range(33):
        store.memories[f"many-{index}"] = memory.model_copy(update={"memory_id": f"many-{index}"})
    with pytest.raises(AppError, match="limit 32"):
        await service.candidates(request.identity, "target")


async def test_current_confirmed_order_intent_and_conflict_override_history():
    from deephelp_app.domain.models.entities import Entity, EntityConflict, EntityName, EntitySource
    from deephelp_app.domain.models.intents import IntentCode

    request, question, _, _, _, _ = await fixture()
    entity = Entity(
        name=EntityName.ORDER_ID,
        value="000031",
        source=EntitySource(message_id=request.message_id, excerpt="订单000031"),
    )
    source = question.model_copy(
        update={"entities": (entity,), "active_intent": IntentCode.DISCOUNT_MISSING}
    )
    different = entity.model_copy(update={"value": "000032"})
    assert (
        current_relevance(source, source.model_copy(update={"entities": (different,)}))
        == "order_mismatch"
    )
    assert (
        current_relevance(source, source.model_copy(update={"active_intent": None}))
        == "current_unconfirmed"
    )
    assert (
        current_relevance(
            source, source.model_copy(update={"active_intent": IntentCode.ORDER_ACTIVITY_QUERY})
        )
        == "intent_mismatch"
    )
    conflict = EntityConflict(previous=entity, replacement=different, resolution="unresolved")
    assert (
        current_relevance(source, source.model_copy(update={"conflicts": (conflict,)}))
        == "current_conflict"
    )
    assert current_relevance(source, source) is None


async def test_browser_lifecycle_requires_original_origin_and_csrf():
    from types import SimpleNamespace

    from deephelp_app.api.app import create_app
    from deephelp_app.api.workbench import BrowserAuth, workbench_router
    from deephelp_app.bootstrap.settings import Settings
    from deephelp_tools.learning.workbench.long_memory import long_memory_router

    request, _, _, service, memory, _ = await fixture()
    auth = BrowserAuth(lambda r: request.identity, identity=request.identity)
    app = create_app(Settings(mode="test"), trace=MemoryTrace(), identity_provider=auth)
    app.include_router(workbench_router(auth, lambda: {"mode": "offline"}))
    app.include_router(long_memory_router(auth))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
        ) as client,
    ):
        app.state.resources.conversation = SimpleNamespace(long_memory=service)
        csrf = (await client.get("/auth/session")).json()["csrf"]
        url = "/learning/long-memory/records/" + memory.memory_id
        body = {"expected_revision": 1, "action": "forget", "edit_id": "browser-forget"}
        assert (await client.put(url, json=body)).status_code == 403
        assert (
            await client.put(
                url, json=body, headers={"X-CSRF-Token": csrf, "Origin": "https://foreign.example"}
            )
        ).status_code == 403
        assert (await client.put(url, json=body, headers={"X-CSRF-Token": csrf})).status_code == 200
        detail = (await client.get(url)).json()
        assert detail["memory"]["state"] == "FORGOTTEN" and detail["model_calls"] == 0


async def test_original_projection_worker_late_delivery_does_not_revive_memory():
    from deephelp_app.application.memory import MemoryService, ProjectionWorker
    from deephelp_app.domain.execution import ExecutionBudget

    request, question, store, service, memory, _ = await fixture()
    await service.change(
        request.identity,
        memory.memory_id,
        MemoryChange(expected_revision=1, action="forget", edit_id="before-projection"),
    )

    class Outbox:
        async def claim_event(self, **kwargs):
            return 1, "lease", question

        async def acknowledge(self, *args):
            return True

    class Index:
        def __init__(self):
            self.rows = []

        async def put(self, current, budget):
            self.rows.append(current)

    index = Index()
    worker = ProjectionWorker(Outbox(), MemoryService(store.cases, long_term=service), index)
    assert await worker.once(ExecutionBudget.start(10, 1, 0)) == "projected"
    assert await worker.once(ExecutionBudget.start(10, 1, 0)) == "projected"
    assert len(index.rows) == 2
    assert (await store.get(request.identity, memory.memory_id)).state == "FORGOTTEN"


async def test_forgotten_source_cannot_return_as_new_hash_after_version_change():
    request, question, store, service, memory, _ = await fixture()
    await service.change(
        request.identity,
        memory.memory_id,
        MemoryChange(expected_revision=1, action="forget", edit_id="forget-source"),
    )
    store.cases.questions[question.question_id] = question.model_copy(update={"version": 7})
    assert (
        await service.preserve(request.identity, "source", question.question_id)
    ).state == "FORGOTTEN"


async def test_new_optional_memory_version_preserves_legacy_source_hash():
    import hashlib
    import json

    from deephelp_app.application.long_memory import source_hash

    _, question, _, _, _, _ = await fixture()
    original = json.loads(question.model_dump_json())
    original["versions"].pop("memory")
    original_bytes = json.dumps(original, ensure_ascii=False, separators=(",", ":")).encode()
    assert source_hash(question) == hashlib.sha256(original_bytes).hexdigest()


def test_release_memory_binding_rejects_old_pass_and_policy_drift(tmp_path):
    import json

    from deephelp_app.adapters.releases.memory_binding import (
        memory_policy_binding,
        require_memory_content,
    )
    from deephelp_app.domain.errors import ConfigurationError

    report = tmp_path / "memory.json"
    report.write_text(
        json.dumps(
            {
                "status": "PASS",
                "checks": {"fake": True},
                "rows": [],
                "memory_policy_binding": memory_policy_binding(),
            }
        )
    )
    with pytest.raises(ConfigurationError, match="complete current content"):
        require_memory_content(report)


def test_independent_query_evidence_ids_do_not_hide_fact_value_changes():
    from types import SimpleNamespace

    from deephelp_app.adapters.releases.memory_binding import fact_values
    from deephelp_app.domain.models.evidence import Fact

    def response(amount, evidence):
        return SimpleNamespace(
            facts=(
                Fact(
                    name="paid",
                    kind="money",
                    value={"amount": amount, "currency": "CNY"},
                    evidence_ids=(evidence,),
                ),
            )
        )

    first = response("173.45", "original-current-evidence")
    assert fact_values(first) == fact_values(response("173.45", "independent-current-evidence"))
    assert fact_values(first) != fact_values(response("71.23", "independent-current-evidence"))
