import copy
import hashlib

import pytest
from mcp.types import CallToolResult, TextContent

from deephelp_app.domain.models import ErrorCode, ToolInvocationEnvelope
from deephelp_app.errors import AppError
from deephelp_app.execution import ExecutionBudget
from deephelp_app.mcp_mock import MockBackend, MockConfig
from deephelp_app.mcp_protocol import (
    canonical,
    registered_tools,
    sign_metadata,
    validate_arguments,
    verify_metadata,
)
from deephelp_app.mcp_smoke import load_fault_config, synthetic_request
from deephelp_app.samples import load_business_fixtures
from deephelp_app.tool_gateway import ToolGateway

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "name,args",
    [
        ("refund_order", {"order_id": "000031"}),
        ("get_order_benefits", {"order_id": 31}),
        ("get_order_benefits", {"order_id": " "}),
        ("get_order_benefits", {}),
        ("get_order_benefits", {"order_id": "000031", "user_id": "synthetic-user-b"}),
        ("get_order_benefits", {"order_id": "000031", "coupon_id": None}),
        ("check_coupon", {"order_id": "000042"}),
        ("check_coupon", {"order_id": "000042", "coupon_id": None}),
        ("check_coupon", {"order_id": "000042", "coupon_id": 9}),
        ("check_coupon", ["000042", "000009"]),
    ],
)
def test_registry_rejects_unknown_write_tool_identity_and_bad_arguments(name, args):
    with pytest.raises(AppError) as exc:
        validate_arguments(name, args)
    assert exc.value.code == ErrorCode.INVALID_ARGUMENT


def test_registry_is_readonly_no_identity_credentials_or_fault_switches():
    for t in registered_tools():
        assert set(t.input_schema["properties"]) <= {"order_id", "coupon_id"}
        assert t.input_schema["additionalProperties"] is False
        assert t.annotations.read_only_hint is True
        assert t.annotations.destructive_hint is False
        assert "signature" not in str(t.model_dump())
    assert (
        validate_arguments("check_coupon", {"order_id": "000042", "coupon_id": "000009"}).coupon_id
        == "000009"
    )


def test_signed_context_tampering_missing_credentials_and_size_fail_closed():
    secret = "s" * 64
    payload = {"action": "call_tool", "identity": {"user_id": "a"}}
    meta = sign_metadata(secret, payload)
    assert verify_metadata(secret, meta) == payload
    for bad in [
        None,
        {},
        sign_metadata("wrong", payload),
        sign_metadata(secret, {"blob": "x" * 33000}),
    ]:
        with pytest.raises(AppError) as exc:
            verify_metadata(secret, bad)
        assert exc.value.code == ErrorCode.FORBIDDEN
    tampered = copy.deepcopy(meta)
    tampered["deephelp/internal"]["payload"]["identity"]["user_id"] = "b"
    with pytest.raises(AppError):
        verify_metadata(secret, tampered)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_result_bytes": 1},
        {"startup_timeout": 0},
        {"child_timeout": 0},
        {"concurrency": 0},
        {"startup_timeout": float("inf")},
        {"child_timeout": float("nan")},
    ],
)
def test_gateway_limits_are_finite_and_positive(limits):
    with pytest.raises(ValueError):
        ToolGateway(**limits)


def test_fault_fixture_only_references_synthetic_orders():
    config = load_fault_config()
    assert set(config.faults) <= {o.order_id for o in load_business_fixtures().orders}
    assert len(config.faults) == 7


@pytest.mark.parametrize(
    "mutation",
    [
        "operation",
        "call",
        "version",
        "order",
        "missing",
        "duplicate_fact",
        "hash",
        "amount",
        "status",
        "extra_instruction",
        "evidence",
        "foreign_evidence",
    ],
)
async def test_gateway_rejects_uncorrelated_or_semantically_bad_results(mutation):
    budget = ExecutionBudget.start(10, 5, 0)
    req, _, ctx = synthetic_request(budget)
    envelope = ToolInvocationEnvelope(
        request=req, request_id=ctx.request_id, trace_id=ctx.trace_id, call_id="call-unit"
    )
    good = await MockBackend("s" * 64, MockConfig()).query(envelope)
    data = good.model_dump(mode="json")
    if mutation == "operation":
        data["operation_id"] = "foreign-op"
    elif mutation == "call":
        data["call_id"] = "foreign-call"
    elif mutation == "version":
        data["tool_version"] = "old"
    elif mutation == "order":
        data["facts"][0]["value"] = "foreign-order"
    elif mutation == "missing":
        data["facts"] = []
    elif mutation == "duplicate_fact":
        data["facts"].append(data["facts"][0])
    elif mutation == "hash":
        data["evidence_refs"][0]["content_hash"] = "0" * 64
    elif mutation == "amount":
        data["facts"][2]["value"]["amount"] = "1000.00"
    elif mutation == "status":
        data["facts"][3]["value"] = "unknown"
    elif mutation == "extra_instruction":
        data["execute_next"] = "refund_order"
    elif mutation == "evidence":
        data["facts"][0]["evidence_ids"] = ["forged-evidence"]
    else:
        data["evidence_refs"][0]["locator"] = "foreign-order"
    if mutation in {"amount", "status"}:
        values = {f["name"]: f["value"] for f in data["facts"]}
        data["evidence_refs"][0]["content_hash"] = hashlib.sha256(canonical(values)).hexdigest()
    wire = CallToolResult(
        content=[TextContent(text=canonical(data).decode())], structured_content=data
    )
    with pytest.raises(AppError) as exc:
        ToolGateway()._parse(req, "call-unit", wire)
    assert exc.value.code == ErrorCode.MODEL_OUTPUT_INVALID


async def test_closed_gateway_does_not_spend_budget_or_publish_facts():
    budget = ExecutionBudget.start(5, 1, 0)
    req, question, ctx = synthetic_request(budget)
    result = await ToolGateway().execute(req, question, budget, context=ctx)
    assert result.error.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert not result.facts and budget.attempts_used == 0


async def test_coupon_order_relationship_and_not_found():
    budget = ExecutionBudget.start(10, 3, 0)
    backend = MockBackend("s" * 64, MockConfig())
    for order, coupon, code in [
        ("000031", "000009", ErrorCode.INVALID_ARGUMENT),
        ("000042", "ABSENT", ErrorCode.NOT_FOUND),
    ]:
        req, _, ctx = synthetic_request(budget, order_id=order, coupon_id=coupon)
        env = ToolInvocationEnvelope(
            request=req, request_id=ctx.request_id, trace_id=ctx.trace_id, call_id="unit-call"
        )
        with pytest.raises(AppError) as exc:
            await backend.query(env)
        assert exc.value.code == code


async def test_wrong_caller_missing_slot_and_versions_reject_before_dispatch():
    budget = ExecutionBudget.start(10, 5, 0)
    req, question, ctx = synthetic_request(budget)
    _, _, foreign_ctx = synthetic_request(budget, user="synthetic-user-b")
    for changed_req, changed_question, context, code in [
        (req, question, foreign_ctx, ErrorCode.FORBIDDEN),
        (req, question.model_copy(update={"entities": ()}), ctx, ErrorCode.MISSING_SLOT),
        (req.model_copy(update={"tool_version": "old"}), question, ctx, ErrorCode.VERSION_CONFLICT),
    ]:
        result = await ToolGateway().execute(changed_req, changed_question, budget, context=context)
        assert result.error.code == code and result.call_id is None
    assert budget.attempts_used == 0
