"""Fixed read-only registry and authenticated internal metadata for owned stdio servers."""

import hashlib
import hmac
import json
from typing import Any, cast

from jsonschema import Draft202012Validator
from mcp.types import RequestParamsMeta, Tool, ToolAnnotations
from pydantic import ValidationError

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models import (
    DTO,
    ErrorCode,
    FactKind,
    Money,
    ToolName,
    ToolParameters,
    ToolRequest,
    ToolResult,
)

TOOL_VERSION = "m06-readonly-v1"
META_KEY = "deephelp/internal"
LEDGER_URI = "deephelp://m06/ledger"
HEALTH_URI = "deephelp://m06/health"


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def tool_schema(name: ToolName) -> dict[str, Any]:
    schema = ToolParameters.model_json_schema()
    if name == ToolName.GET_ORDER_BENEFITS:
        del schema["properties"]["coupon_id"]
    else:
        schema["properties"]["coupon_id"] = schema["properties"]["coupon_id"]["anyOf"][0]
        schema["required"] = ["order_id", "coupon_id"]
    return schema


def registered_tools() -> list[Tool]:
    descriptions = {
        ToolName.GET_ORDER_BENEFITS: "查询合成订单实付、优惠和活动证据。只读，服务端检查归属。",
        ToolName.CHECK_COUPON: "查询合成订单对应优惠券的状态、门槛和可用性证据。只读。",
    }
    return [
        Tool(
            name=name.value,
            description=descriptions[name],
            input_schema=tool_schema(name),
            output_schema=ToolResult.model_json_schema(),
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
            _meta={"deephelp/tool_version": TOOL_VERSION},
        )
        for name in ToolName
    ]


def validate_arguments(name: str, arguments: object) -> ToolParameters:
    try:
        tool = ToolName(name)
    except ValueError:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Tool is not allowed") from None
    if not isinstance(arguments, dict) or list(
        Draft202012Validator(tool_schema(tool)).iter_errors(arguments)
    ):
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Invalid tool arguments")
    try:
        return ToolParameters.model_validate(arguments)
    except ValidationError:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Invalid tool arguments") from None


def sign_metadata(secret: str, payload: dict[str, Any]) -> RequestParamsMeta:
    # SDK v2 declares an open TypedDict; current mypy does not implement extra_items.
    return cast(
        RequestParamsMeta,
        {
            "deephelp/internal": {
                "payload": payload,
                "signature": hmac.new(
                    secret.encode(), canonical(payload), hashlib.sha256
                ).hexdigest(),
            }
        },
    )


def verify_metadata(secret: str, meta: RequestParamsMeta | None) -> dict[str, Any]:
    try:
        proof = (meta or {}).get(META_KEY)
        if not isinstance(proof, dict) or set(proof) != {"payload", "signature"}:
            raise ValueError
        payload, signature = proof["payload"], proof["signature"]
        if not isinstance(payload, dict) or not isinstance(signature, str):
            raise ValueError
        data = canonical(payload)
        if len(data) > 32768 or not hmac.compare_digest(
            signature, hmac.new(secret.encode(), data, hashlib.sha256).hexdigest()
        ):
            raise ValueError
        return payload
    except ValueError, TypeError:
        raise AppError(ErrorCode.FORBIDDEN, "Trusted execution context required") from None


def validate_observation(request: ToolRequest, result: ToolResult) -> None:
    facts = {f.name: f for f in result.facts}
    expected = (
        {
            "order_id": FactKind.TEXT,
            "paid": FactKind.MONEY,
            "discount": FactKind.MONEY,
            "discount_status": FactKind.TEXT,
            "activity_ids": FactKind.TEXT,
            "activity_labels": FactKind.TEXT,
        }
        if request.tool_name == ToolName.GET_ORDER_BENEFITS
        else {
            "order_id": FactKind.TEXT,
            "coupon_id": FactKind.TEXT,
            "coupon_status": FactKind.TEXT,
            "minimum_spend": FactKind.MONEY,
            "usable": FactKind.FLAG,
        }
    )
    if (
        len(facts) != len(result.facts)
        or set(facts) != set(expected)
        or any(facts[k].kind != kind for k, kind in expected.items())
        or facts["order_id"].value != request.parameters.order_id
        or len(result.evidence_refs) != 1
    ):
        raise ValueError
    ref = result.evidence_refs[0]
    values = {
        k: (f.value.model_dump(mode="json") if isinstance(f.value, DTO) else f.value)
        for k, f in facts.items()
    }
    if (
        ref.version != "business-fixtures-v1"
        or ref.locator != request.parameters.order_id
        or ref.content_hash != hashlib.sha256(canonical(values)).hexdigest()
        or any(f.evidence_ids != (ref.evidence_id,) for f in result.facts)
    ):
        raise ValueError
    if request.tool_name == ToolName.GET_ORDER_BENEFITS:
        paid, discount = facts["paid"].value, facts["discount"].value
        status = facts["discount_status"].value
        if (
            not isinstance(paid, Money)
            or not isinstance(discount, Money)
            or discount.amount > paid.amount
            or status not in {"applied", "not_eligible", "missing"}
            or (status != "applied" and discount.amount != 0)
        ):
            raise ValueError
    elif (
        facts["coupon_id"].value != request.parameters.coupon_id
        or facts["coupon_status"].value
        not in {
            "usable",
            "expired",
            "threshold_not_met",
            "already_used",
            "not_started",
            "scope_mismatch",
            "frozen",
            "revoked",
        }
        or facts["usable"].value != (facts["coupon_status"].value == "usable")
    ):
        raise ValueError
