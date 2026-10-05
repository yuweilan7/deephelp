"""Bounded QianWen adapter for Chat, Embedding and native text Rerank."""

import re

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode


def diagnostic_usage(value: object) -> dict[str, int | None]:
    """Only API-provided counts; absent or malformed counts stay unknown."""
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
    """Schemas are local only: remote references must never fetch untrusted URLs."""

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
