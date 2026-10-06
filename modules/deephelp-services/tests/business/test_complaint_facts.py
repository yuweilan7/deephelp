"""固定来源矛盾与等待组合的独立断言，真实PG/CAS另由内容探针验收。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from deephelp_app.adapters.mcp.business.mcp_protocol import validate_observation
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.business import (
    BusinessCouponInput,
    BusinessCouponReturnInput,
    BusinessOrderInput,
    BusinessRecord,
    BusinessRefundInput,
)
from deephelp_app.domain.models.common import ErrorCode
from deephelp_app.domain.models.tools import (
    ToolInvocationEnvelope,
    ToolName,
    ToolParameters,
    tool_parameters_hash,
)
from deephelp_services.business.repository import BusinessRepository
from deephelp_tools.probes.protocol.mcp_smoke import synthetic_request

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "refund_state,return_state,return_coupon,expected",
    [
        ("pending", None, None, "unknown"),
        ("completed", None, None, "unknown"),
        ("completed", "returned", "CP-42", "returned"),
        ("pending", "returned", "CP-42", "conflict"),
        ("completed", "returned", "CP-99", "conflict"),
        ("completed", "pending", "CP-42", "pending"),
        ("failed", "failed", "CP-42", "failed"),
    ],
)
async def test_return_claim_requires_matching_coupon_and_completed_refund(
    refund_state, return_state, return_coupon, expected
):
    req, _, ctx = synthetic_request(ExecutionBudget.start(10, 4, 0))
    params = ToolParameters(refund_id="REF-42", coupon_id="CP-42")
    req = type(req).model_validate(
        req.model_copy(
            update={
                "tool_name": ToolName.CHECK_REFUND_COUPON_RETURN,
                "parameters": params,
                "parameters_hash": tool_parameters_hash(params),
            }
        ).model_dump()
    )
    now = datetime.now(UTC)
    data = [
        BusinessRefundInput(
            refund_id="REF-42",
            order_id="ORDER-42",
            coupon_id="CP-42",
            status=refund_state,
            reason="退款来源",
        ),
        BusinessCouponInput(
            coupon_id="CP-42",
            order_id="ORDER-42",
            status="already_used",
            minimum_spend={"amount": "0"},
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=1),
        ),
        BusinessOrderInput(
            order_id="ORDER-42",
            paid={"amount": "100"},
            discount={"amount": "0"},
            discount_status="not_eligible",
            sku_ids=("SKU-42",),
        ),
    ]
    sources = [BusinessRecord(identity=req.identity, version=3, data=item) for item in data]
    if return_state:
        sources.append(
            BusinessRecord(
                identity=req.identity,
                version=7,
                data=BusinessCouponReturnInput(
                    refund_id="REF-42",
                    coupon_id=return_coupon,
                    status=return_state,
                    reason="券来源",
                ),
            )
        )
    else:
        sources.append(AppError(ErrorCode.NOT_FOUND, "Missing return record"))
    cursor = AsyncMock()
    store = object.__new__(BusinessRepository)

    @asynccontextmanager
    async def transaction(**kwargs):
        assert kwargs["snapshot"] is True
        yield cursor

    store.transaction = transaction
    store._get = AsyncMock(side_effect=sources)
    result = await store.query(
        ToolInvocationEnvelope(
            request=req, request_id=ctx.request_id, trace_id=ctx.trace_id, call_id="real-shape-call"
        )
    )
    assert {f.name: f.value for f in result.facts}["return_status"] == expected
    validate_observation(req, result, evidence_version="business-postgres-v1")
    assert all(
        "UPDATE" not in call.args[0] and "DELETE" not in call.args[0]
        for call in cursor.execute.await_args_list
    )
    snapshot = cursor.execute.await_args.args[1][-2]
    assert '"version": 3' in snapshot
    if return_state:
        assert '"version": 7' in snapshot
