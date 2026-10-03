import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest

from deephelp_app.app import create_app
from deephelp_app.conversation import STAGES
from deephelp_app.domain.models import ErrorCode, IntentCode, VerifiedIdentity
from deephelp_app.errors import AppError
from deephelp_app.evaluation.samples import load_corpus
from deephelp_app.execution import ExecutionBudget
from deephelp_app.learning.mvp_replay import ReplayAssembly
from deephelp_app.settings import Settings
from deephelp_app.trace import MemoryTrace

pytestmark = pytest.mark.integration


@pytest.fixture
def identity():
    return VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a")


@pytest.fixture
def body():
    return dict(
        channel="test",
        session_id="s",
        message_id=uuid4().hex,
        raw_text="订单 000031 未享受优惠",
        occurred_at=datetime.now(UTC).isoformat(),
    )


@asynccontextmanager
async def app_client(identity, *, tweak=None, budget_factory=None):
    assembly = ReplayAssembly()
    trace = MemoryTrace()

    @asynccontextmanager
    async def factory(client, sink):
        async with assembly.open(client, sink) as service:
            if tweak:
                tweak(service)
            yield service

    app = create_app(
        Settings(mode="test", request_timeout=60, max_attempts=40),
        trace=trace,
        identity_provider=lambda request: identity,
        conversation_factory=factory,
        budget_factory=budget_factory,
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            yield client, assembly, trace, app


async def test_normal_response_replay_and_13_stages(identity, body):
    async with app_client(
        identity,
        budget_factory=lambda: ExecutionBudget.start(
            60, 40, 0, token_limit=10000, cost_limit=Decimal("1")
        ),
    ) as (client, assembly, trace, app):
        first = (await client.post("/converse", json=body)).json()
        assert first["outcome"] == "ANSWERED" and first["run_status"] == "SUCCEEDED"
        assert first["question_status"] == "RESOLVED" and first["question_id"]
        assert [s["stage"] for s in first["stages"]] == list(STAGES)
        assert sum(s["model_calls"] for s in first["stages"]) == 2
        assert len(first["tool_call_ids"]) == 1
        assert "99.90" in first["reply"] and "10.00" in first["reply"]
        again = (await client.post("/converse", json=body)).json()
        assert again["replayed"] and again["run_id"] == first["run_id"]
        assert again["request_id"] != first["request_id"] and again["budget_used"]["attempts"] == 0
        assert len(await assembly.tools.ledger()) == 1
        assert len([e for e in trace.events if e.event == "stage_finished"]) == 13
        assert (await client.get("/health")).json()["module"] == "M08"
        page = (await client.get("/")).text
        assert "textContent" in page and "innerHTML" not in page


@pytest.mark.parametrize("case_id", [c.case_id for c in load_corpus().cases])
async def test_all_frozen_samples_run_full_api_and_preserve_safety(case_id):
    case = next(c for c in load_corpus().cases if c.case_id == case_id)
    async with app_client(case.identity) as (client, assembly, trace, app):
        response = await client.post("/converse", json=case.message.model_dump(mode="json"))
        result = response.json()
        assert response.status_code == 200
        assert result["error"] is None or result["error"]["code"] != "INTERNAL_ERROR"
        assert result["run_id"] and result["question_id"] and len(result["stages"]) == 13
        ledger = await assembly.tools.ledger()
        assert set(result["tool_call_ids"]) == {r.call_id for r in ledger}
        if result["missing_slots"] or result["intent_decision"]["final_code"] is None:
            assert not ledger and not result["facts"]
        stored = next(iter(assembly.ledger.rows.values()))[1]
        assert stored.response is not None and stored.question.version == 2
        assert stored.question.status == result["question_status"]


@pytest.mark.parametrize(
    "text",
    [
        "我的订单未享受优惠",
        "今天天气怎么样",
        "不用查订单活动，我只是问候一下",
    ],
)
async def test_missing_unknown_and_declined_do_not_call_tools(identity, body, text):
    body["raw_text"] = text
    async with app_client(identity) as (client, assembly, trace, app):
        result = (await client.post("/converse", json=body)).json()
        assert result["outcome"] in {"HANDOFF", "CLARIFY"}
        assert not await assembly.tools.ledger()
        if result["missing_slots"]:
            assert "完整问题" in result["reply"]


async def test_no_sop_is_normal_contract_and_zero_tools(identity, body):
    def tweak(s):
        s.definitions.pop(IntentCode.DISCOUNT_MISSING)

    async with app_client(identity, tweak=tweak) as (client, assembly, trace, app):
        result = (await client.post("/converse", json=body)).json()
        assert result["outcome"] == "HANDOFF" and result["error"]["code"] == "NO_SOP"
        assert not await assembly.tools.ledger()


async def test_model_unavailable_is_failed_run_and_durable_response(identity, body):
    class Broken:
        async def chat(self, request, budget):
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "synthetic unavailable")

    async with app_client(identity, tweak=lambda s: setattr(s.sop, "model", Broken())) as (
        client,
        assembly,
        trace,
        app,
    ):
        result = (await client.post("/converse", json=body)).json()
        assert result["outcome"] == "ERROR" and result["run_status"] == "FAILED"
        assert result["stages"][9]["status"] == "failed"
        assert not result["facts"] and not await assembly.tools.ledger()


async def test_cancellation_finalizes_without_claiming_success(identity, body):
    started = asyncio.Event()

    class Waiting:
        async def chat(self, request, budget):
            started.set()
            await asyncio.Event().wait()

    async with app_client(identity, tweak=lambda s: setattr(s.sop, "model", Waiting())) as (
        client,
        assembly,
        trace,
        app,
    ):
        task = asyncio.create_task(client.post("/converse", json=body))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        receipt = next(iter(assembly.ledger.rows.values()))[1]
        assert receipt.response.run_status == "CANCELLED"
        assert receipt.question.status == "CANCELLED" and not receipt.response.facts
        replay = (await client.post("/converse", json=body)).json()
        assert replay["run_id"] == receipt.run_id and replay["replayed"]


async def test_hint_requires_ownership_and_cannot_resume(identity, body):
    async with app_client(identity) as (client, assembly, trace, app):
        result = (await client.post("/converse", json=body)).json()
        changed = {**body, "message_id": uuid4().hex, "question_hint": result["question_id"]}
        assert (await client.post("/converse", json=changed)).status_code == 422
        changed["question_hint"] = "someone-else-question"
        assert (await client.post("/converse", json=changed)).status_code == 403
        assert len(assembly.ledger.rows) == 1


async def test_budget_failure_is_contract_error_before_dispatch(identity, body):
    def exhausted():
        raise AppError(ErrorCode.BUDGET_EXHAUSTED, "exhausted")

    async with app_client(identity, budget_factory=exhausted) as (client, assembly, trace, app):
        response = await client.post("/converse", json=body)
        assert response.json()["error"]["code"] == "BUDGET_EXHAUSTED"
        assert not assembly.ledger.rows and not await assembly.tools.ledger()


async def test_cancel_after_tool_keeps_dispatched_id_without_success_facts(identity, body):
    observed = asyncio.Event()

    def tweak(s):
        original = s.sop.model

        class PauseAfterLookup:
            async def chat(self, request, budget):
                if request.tool_choice == "finish_sop":
                    observed.set()
                    await asyncio.Event().wait()
                return await original.chat(request, budget)

        s.sop.model = PauseAfterLookup()

    async with app_client(identity, tweak=tweak) as (client, assembly, trace, app):
        task = asyncio.create_task(client.post("/converse", json=body))
        await asyncio.wait_for(observed.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        receipt = next(iter(assembly.ledger.rows.values()))[1]
        records = await assembly.tools.ledger()
        assert receipt.response.run_status == "CANCELLED"
        assert receipt.response.tool_call_ids == tuple(r.call_id for r in records)
        assert (
            len(records) == 1 and not receipt.response.facts and not receipt.response.evidence_refs
        )


async def test_deadline_and_terminal_storage_failure_do_not_publish_success(identity, body):
    class Waiting:
        async def chat(self, request, budget):
            await asyncio.Event().wait()

    async with app_client(
        identity,
        tweak=lambda s: setattr(s.sop, "model", Waiting()),
        budget_factory=lambda: ExecutionBudget.start(0.05, 10, 0),
    ) as (client, assembly, trace, app):
        result = (await client.post("/converse", json=body)).json()
        assert result["outcome"] == "ERROR" and result["run_status"] == "FAILED"
        assert not result["facts"]
        assert next(iter(assembly.ledger.rows.values()))[1].response is not None

    def tweak(s):
        async def broken(*args):
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "storage unavailable")

        s.ledger.finish = broken

    async with app_client(identity, tweak=tweak) as (client, assembly, trace, app):
        response = await client.post("/converse", json=body)
        assert response.status_code == 503 and response.json()["outcome"] == "ERROR"
        assert not response.json()["facts"]
        assert next(iter(assembly.ledger.rows.values()))[1].response is None
