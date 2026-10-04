"""Persistent application reservations, refunds, and invalid remote usage boundaries."""

import json
from decimal import Decimal

import pytest

from deephelp_app.bootstrap.mvp_runtime import BudgetSession
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import BudgetUsed


async def test_receipt_refund_updates_existing_cumulative_writer_without_reset(tmp_path):
    path = tmp_path / "budget.json"
    path.write_text(
        json.dumps(
            {
                "max_calls": 20,
                "max_tokens": 20000,
                "max_cost_cny": "2",
                "attempts": 3,
                "tokens": 9,
                "charged_tokens": 9,
                "cost_upper_cny": "0.001",
                "uncertain_attempts": 0,
                "stages": [],
            }
        )
    )
    gate = BudgetSession(path)
    gate.open()
    try:
        budget = gate.request()
        budget.claim_attempt(retry=False, attempts=6, tokens=80, cost=Decimal("0.01"))
        reserved = json.loads(path.read_text())
        assert reserved["attempts"] == 9 and reserved["charged_tokens"] == 89
        assert reserved["uncertain_attempts"] == 1
        usage = BudgetUsed(
            attempts=2, tokens=6, token_upper_bound=6, cost="0.000048", cost_upper_bound="0.000048"
        )
        budget.settle_remote(6, 80, Decimal("0.01"), usage)
        settled = json.loads(path.read_text())
        assert settled["attempts"] == 5 and settled["tokens"] == settled["charged_tokens"] == 15
        assert settled["cost_upper_cny"] == "0.001048" and settled["uncertain_attempts"] == 0
    finally:
        gate.close()


@pytest.mark.parametrize(
    "change",
    [
        {"attempts": 7},
        {"tokens": 81, "token_upper_bound": 81},
        {"cost": "0.02", "cost_upper_bound": "0.02"},
        {"tokens": None},
        {"retries": 1},
        {"uncertain_attempts": 2},
        {"tool_steps": 1},
    ],
)
async def test_invalid_receipt_does_not_release_any_original_reservation(change):
    budget = ExecutionBudget.start(20, 10, 0, token_limit=1000, cost_limit=Decimal("1"))
    budget.claim_attempt(retry=False, attempts=6, tokens=80, cost=Decimal("0.01"))
    before = budget.usage()
    usage = BudgetUsed.model_validate(
        {
            "attempts": 2,
            "tokens": 6,
            "token_upper_bound": 6,
            "cost": "0.000048",
            "cost_upper_bound": "0.000048",
            **change,
        }
    )
    with pytest.raises(AppError):
        budget.settle_remote(6, 80, Decimal("0.01"), usage)
    assert budget.usage() == before and budget.remaining_attempts == 4
