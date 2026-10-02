"""Fixed read-only registry and authenticated internal metadata for owned stdio servers."""

import hashlib
import hmac
import json
from typing import Any, cast

from jsonschema import Draft202012Validator
from mcp.types import RequestParamsMeta, Tool, ToolAnnotations
from pydantic import ValidationError

from deephelp_app.domain.models import ErrorCode, ToolName, ToolParameters, ToolResult
from deephelp_app.errors import AppError

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
