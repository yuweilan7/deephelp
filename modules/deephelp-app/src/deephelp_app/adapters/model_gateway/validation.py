"""模型响应的纯校验与安全诊断，网关请求前/返回后共用。

validate_schema限制可支持的JSON schema，invalid_output只暴露静态错误与安全请求ID。
diagnostic_usage保留已知结构而不编造缺失计数；这里不发送HTTP或修改业务事实。
"""

import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode


def diagnostic_usage(value: object) -> dict[str, int | None]:
    """只保留API实际返回的非负整数；bool、负数、缺失或格式错误记为未知。

    reasoning是output的细项，只展示，不在这里再次加到total或计算费用。
    """
    raw = value if isinstance(value, dict) else {}
    details = raw.get("completion_tokens_details")
    details = details if isinstance(details, dict) else {}
    counts = {
        "input_tokens": raw.get("prompt_tokens"),
        "output_tokens": raw.get("completion_tokens"),
        "total_tokens": raw.get("total_tokens"),
        "reasoning_tokens": details.get("reasoning_tokens"),
    }
    return {key: n if type(n) is int and n >= 0 else None for key, n in counts.items()}


def safe_identifier(value: object) -> str | None:
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        return value
    return None


def validate_schema(schema: dict[str, object]) -> Draft202012Validator:
    """递归检查本地schema并返回校验器，远程$ref在解析前拒绝。

    schema来自请求契约；若允许URL引用，纯校验可能意外联网或读取不可信来源。
    本地引用只能以#开头，非法schema统一收口为INVALID_ARGUMENT。
    """

    def check_refs(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise AppError(
                        ErrorCode.INVALID_ARGUMENT, "External schema references disabled"
                    )
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    check_refs(schema)
    try:
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)
    except SchemaError:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Invalid local JSON schema") from None


def invalid_output(request_id: str | None = None) -> AppError:
    return AppError(
        ErrorCode.MODEL_OUTPUT_INVALID,
        "Model response failed validation",
        provider_request_id=request_id,
    )
