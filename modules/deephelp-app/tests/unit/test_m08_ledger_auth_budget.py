import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from starlette.requests import Request

from deephelp_app.adapters.ledger import MemoryLedger, payload_hash
from deephelp_app.application.conversation import usage_delta
from deephelp_app.bootstrap.mvp_runtime import BudgetSession, LocalAuth, validate_control_paths
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import (
    BudgetUsed,
    ErrorCode,
    NextAction,
    Outcome,
    QuestionStatus,
    RequestEnvelope,
    ResponseEnvelope,
    RunStatus,
    VerifiedIdentity,
)

pytestmark = pytest.mark.unit


def request(**updates):
    data = dict(
        channel="test",
        session_id="s",
        message_id="msg",
        raw_text="订单 000031 未享受优惠",
        occurred_at=datetime(2026, 10, 3, tzinfo=UTC),
        identity=VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a"),
        request_id=uuid4().hex,
        trace_id=uuid4().hex,
        received_at=datetime.now(UTC),
    )
    data.update(updates)
    return RequestEnvelope(**data)


async def test_concurrent_claim_replay_and_terminal_transition():
    ledger = MemoryLedger()
    req = request()
    receipts = await asyncio.gather(*(ledger.accept(req) for _ in range(8)))
    assert sum(r.acquired for r in receipts) == 1
    assert len({r.run_id for r in receipts}) == 1
    owner = next(r for r in receipts if r.acquired)
    q = owner.question.model_copy(update={"version": 2, "status": QuestionStatus.HANDED_OFF})
    response = ResponseEnvelope(
        request_id=req.request_id,
        trace_id=req.trace_id,
        run_id=owner.run_id,
        run_status=RunStatus.SUCCEEDED,
        question_id=q.question_id,
        question_status=q.status,
        outcome=Outcome.HANDOFF,
        reply="人工",
        next_action=NextAction.CONTACT_SUPPORT,
    )
    await ledger.finish(owner, q, response)
    replay = await ledger.accept(request())
    assert replay.response == response and not replay.acquired
    with pytest.raises(AppError) as exc:
        await ledger.finish(owner, q, response)
    assert exc.value.code == ErrorCode.VERSION_CONFLICT


@pytest.mark.parametrize(
    "updates",
    [
        {"raw_text": "changed"},
        {"session_id": "other"},
        {"question_hint": "old"},
        {"occurred_at": datetime(2026, 10, 4, tzinfo=UTC)},
    ],
)
async def test_payload_conflicts_do_not_create_new_run(updates):
    ledger = MemoryLedger()
    await ledger.accept(request())
    with pytest.raises(AppError) as exc:
        await ledger.accept(request(**updates))
    assert exc.value.code == ErrorCode.IDEMPOTENCY_CONFLICT and len(ledger.rows) == 1


async def test_identity_and_session_scope():
    ledger = MemoryLedger()
    first = await ledger.accept(request())
    other = VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-b")
    second = await ledger.accept(request(identity=other))
    assert first.run_id != second.run_id
    assert await ledger.question(other, "s", first.question.question_id) is None
    assert (
        await ledger.question(first.question.identity, "other", first.question.question_id) is None
    )


def test_diagnostic_fields_are_not_idempotency_content():
    assert payload_hash(request()) == payload_hash(request())


@pytest.mark.parametrize(
    "token,accepted", [("A" * 32, True), ("", False), ("B" * 32, False), ("中文", False)]
)
def test_bearer_auth_is_a_fixed_verified_identity(tmp_path, token, accepted):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "tokens": [
                    {
                        "token": "A" * 32,
                        "identity": {
                            "tenant_id": "synthetic-tenant",
                            "user_id": "synthetic-user-a",
                        },
                    }
                ]
            }
        )
    )
    auth = LocalAuth(path)
    req = Request({"type": "http", "headers": [(b"authorization", ("Bearer " + token).encode())]})
    if accepted:
        assert auth(req).user_id == "synthetic-user-a"
    else:
        with pytest.raises(AppError) as exc:
            auth(req)
        assert exc.value.code == ErrorCode.UNAUTHENTICATED


def budget_file(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text(
        json.dumps(
            {
                "max_calls": 4,
                "max_tokens": 1000,
                "max_cost_cny": "1",
                "attempts": 0,
                "tokens": 0,
                "charged_tokens": 0,
                "cost_upper_cny": "0",
                "uncertain_attempts": 0,
            }
        )
    )
    return path


async def test_multiple_request_budgets_share_durable_reservations(tmp_path):
    path = budget_file(tmp_path)
    gate = BudgetSession(path)
    gate.open()
    with pytest.raises(ConfigurationError):
        BudgetSession(path).open()
    try:
        a, b = gate.request(), gate.request()
        a.claim_attempt(retry=False, tokens=600, cost=Decimal("0.3"))
        with pytest.raises(AppError) as exc:
            b.claim_attempt(retry=False, tokens=600, cost=Decimal("0.3"))
        assert exc.value.code == ErrorCode.BUDGET_EXHAUSTED
        assert json.loads(path.read_text())["charged_tokens"] == 1200
        with pytest.raises(AppError):
            gate.request()
    finally:
        gate.close()
    assert not path.with_suffix(".lock").exists()


async def test_settlement_refunds_only_known_reservations(tmp_path):
    gate = BudgetSession(budget_file(tmp_path))
    gate.open()
    try:
        budget = gate.request()
        budget.claim_attempt(retry=False, tokens=600, cost=Decimal("0.3"))
        budget.settle_model(600, Decimal("0.3"), 100, Decimal("0.05"))
        assert gate.state["charged_tokens"] == 100
        assert Decimal(gate.state["cost_upper_cny"]) == Decimal("0.05")
        assert gate.state["uncertain_attempts"] == 0
    finally:
        gate.close()


async def test_zero_cost_replay_preserves_decimal_contract():
    budget = ExecutionBudget.start(10, 3, 0, token_limit=1000, cost_limit=Decimal("1"))
    assert usage_delta(budget, budget.usage()) == BudgetUsed(
        tokens=0, cost=Decimal("0"), token_upper_bound=0, cost_upper_bound=Decimal("0")
    )


def test_derived_control_path_aliases_are_rejected(tmp_path):
    budget = tmp_path / "budget.json"
    with pytest.raises(ConfigurationError):
        validate_control_paths([budget, tmp_path / "budget.lock"])
    with pytest.raises(ConfigurationError):
        validate_control_paths([budget], output=budget)
    with pytest.raises(ConfigurationError):
        validate_control_paths([tmp_path / "report.trace.jsonl"], output=tmp_path / "report.json")


async def test_budget_snapshot_is_reloaded_after_writer_lock(tmp_path):
    path = budget_file(tmp_path)
    waiting = BudgetSession(path)
    first = BudgetSession(path)
    first.open()
    first.request().claim_attempt(retry=False, tokens=100, cost=Decimal("0.1"))
    first.close()
    waiting.open()
    try:
        assert waiting.state["attempts"] == 1 and waiting.state["charged_tokens"] == 100
    finally:
        waiting.close()
