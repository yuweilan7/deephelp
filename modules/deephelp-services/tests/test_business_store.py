"""Deterministic time/amount/CAS boundaries; remote persistence has its explicit probe."""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import (
    BusinessCouponInput,
    BusinessOrderInput,
    BusinessRecord,
    BusinessRelations,
    ToolInvocationEnvelope,
)
from deephelp_services.business_store import BusinessRepository
from deephelp_tools.probes.business_probe import Processes, port, reclaim_owned_lock
from deephelp_tools.probes.mcp_smoke import synthetic_request

pytestmark = pytest.mark.unit


def store_with_cursor():
    cursor = AsyncMock()
    cursor.fetchall.return_value = []
    store = object.__new__(BusinessRepository)

    @asynccontextmanager
    async def transaction():
        yield cursor

    store.transaction = transaction
    return store, cursor


@pytest.mark.parametrize(
    "status,paid,start,end,expected",
    [
        ("usable", "50", -1, 1, "threshold_not_met"),
        ("usable", "100", -1, 1, "usable"),
        ("usable", "100", 1, 2, "not_started"),
        ("usable", "100", -2, -1, "expired"),
        ("revoked", "100", -1, 1, "revoked"),
        ("frozen", "100", -1, 1, "frozen"),
    ],
)
async def test_coupon_current_amount_period_and_explicit_state(status, paid, start, end, expected):
    budget = ExecutionBudget.start(10, 2, 0)
    req, _, ctx = synthetic_request(budget, coupon_id="C1")
    now = datetime.now(UTC)
    order = BusinessOrderInput(
        order_id="000031",
        paid={"amount": paid},
        discount={"amount": "0"},
        discount_status="missing",
        sku_ids=("S1",),
    )
    coupon = BusinessCouponInput(
        coupon_id="C1",
        order_id="000031",
        status=status,
        minimum_spend={"amount": "100"},
        valid_from=now + timedelta(days=start),
        valid_until=now + timedelta(days=end),
    )
    store, cursor = store_with_cursor()
    store._get = AsyncMock(
        side_effect=[
            BusinessRecord(identity=req.identity, version=2, data=order),
            BusinessRecord(identity=req.identity, version=3, data=coupon),
        ]
    )
    result = await store.query(
        ToolInvocationEnvelope(
            request=req, request_id=ctx.request_id, trace_id=ctx.trace_id, call_id=uuid4().hex
        )
    )
    assert next(f.value for f in result.facts if f.name == "coupon_status") == expected
    assert next(f.value for f in result.facts if f.name == "usable") == (expected == "usable")
    # Evidence binds both authoritative record versions, created inside the same transaction.
    args = cursor.execute.await_args.args[1]
    assert '"version": 2' in args[-2] and '"version": 3' in args[-2]


async def test_relation_stale_version_cannot_delete_or_insert():
    req, _, _ = synthetic_request(ExecutionBudget.start(10, 2, 0))
    order = BusinessOrderInput(
        order_id="000031",
        paid={"amount": "100"},
        discount={"amount": "0"},
        discount_status="missing",
        sku_ids=("S1",),
    )
    store, cursor = store_with_cursor()
    store._get = AsyncMock(
        return_value=BusinessRecord(identity=req.identity, version=2, data=order)
    )
    with pytest.raises(AppError):
        await store.relations(
            req.identity, "000031", BusinessRelations(expected_version=1, activity_ids=())
        )
    cursor.execute.assert_not_awaited()


async def test_application_process_tree_closes_budget_file_handles(tmp_path):

    process = Processes(tmp_path)
    listener_port = port()
    lock = tmp_path / "owned.lock"
    code = (
        "import sys; from http.server import HTTPServer,SimpleHTTPRequestHandler; "
        "handle=open(sys.argv[1], 'x'); "
        "HTTPServer(('127.0.0.1',int(sys.argv[2])),SimpleHTTPRequestHandler).serve_forever()"
    )
    try:
        await process.start(
            "application",
            ["-c", code, str(lock), str(listener_port)],
            f"http://127.0.0.1:{listener_port}/",
        )
        assert lock.exists()
        await process.stop("application")
        await reclaim_owned_lock(lock)
        assert not lock.exists() and not process.running
    finally:
        await process.close()
