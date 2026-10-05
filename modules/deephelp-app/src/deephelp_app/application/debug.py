"""同一个授权、脱敏快照的三个视图，供工作台与诊断导出读取。

sanitize先一致替换身份/业务编号，再移除敏感和隐藏字段；它不收集隐藏推理。
project只换表示方式，未知用量保留未知；导出额外防表格公式，不执行任何业务请求。
"""

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Literal

from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_app.domain.models.responses import DebugSnapshot

DebugView = Literal["intent", "turns", "flow"]
SENSITIVE_KEYS = {"password", "token", "authorization", "api_key", "secret", "reasoning_content"}
IDENTITY_KEYS = {"tenant_id", "user_id", "session_id", "order_id", "coupon_id", "message_id"}
SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+[^\s\"',}]+|sk-[A-Za-z0-9_-]+|"
    r"(?:password|api[_-]?key|token|secret|密码|密钥|令牌)[\"']?\s*[:=：]\s*[\"']?[^\s\"',}]+)"
)


def scope_hash(key: bytes, identity: VerifiedIdentity, session_id: str) -> str:
    value = json.dumps([identity.tenant_id, identity.user_id, session_id], ensure_ascii=False)
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def pseudonym(key: bytes, value: str) -> str:
    return "id_" + hmac.new(key, value.encode(), hashlib.sha256).hexdigest()[:16]


def sanitize(data: Mapping[str, object], key: bytes) -> dict[str, object]:
    # 脱敏建立新展示值，不改原模型请求/业务结果；原文span偏移不能套用到替换后文本。
    replacements: dict[str, str] = {}

    def collect(value: object, name: str = "") -> None:
        if isinstance(value, dict):
            if value.get("name") in {"order_id", "coupon_id"} and isinstance(
                value.get("value"), str
            ):
                original = value["value"]
                if original:
                    replacements[original] = pseudonym(key, original)
            for field, item in value.items():
                collect(item, str(field))
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item, name)
        elif name in IDENTITY_KEYS and isinstance(value, str) and value:
            replacements[value] = pseudonym(key, value)

    collect(dict(data))
    # Free text may have a rejected/conflicting ID absent from confirmed entities.
    text_pattern = re.compile(
        r"(?i)(?:订单|券(?:号)?|order[_ ]?id|coupon[_ ]?id)\s*[:：]?\s*([0-9]{3,})"
    )

    def discover(value: object) -> None:
        if isinstance(value, dict):
            for item in value.values():
                discover(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                discover(item)
        elif isinstance(value, str):
            for match in text_pattern.finditer(value):
                original = match.group(1)
                replacements[original] = pseudonym(key, original)

    discover(dict(data))
    pattern = (
        re.compile(
            r"(?<![A-Za-z0-9_-])(?:"
            + "|".join(re.escape(v) for v in sorted(replacements, key=len, reverse=True))
            + r")(?![A-Za-z0-9_-])"
        )
        if replacements
        else None
    )

    def walk(value: object, name: str = "") -> object:
        if name.lower() in SENSITIVE_KEYS:
            return "[redacted]"
        if isinstance(value, dict):
            return {str(k): walk(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [walk(v) for v in value]
        if isinstance(value, str):
            value = SECRET_PATTERN.sub("[redacted]", value)
            return pattern.sub(lambda m: replacements[m.group()], value) if pattern else value
        return value

    result = walk(dict(data))
    assert isinstance(result, dict)
    return result


def project(snapshot: DebugSnapshot, view: DebugView, *, dropped: int = 0) -> dict[str, object]:
    # 三视图选择同一snapshot字段，不重新执行模型/检索；缺失保留None/incomplete。
    data = snapshot.data
    keys = {
        "intent": ("input", "text", "intent", "versions"),
        "turns": (
            "input",
            "memory",
            "memory_activity",
            "event",
            "question",
            "versions",
            "approval",
        ),
        "flow": (
            "stages",
            "tools",
            "retrievals",
            "sop",
            "sop_nodes",
            "response",
            "budget",
            "versions",
            "approval",
        ),
    }[view]
    return {
        "schema_version": snapshot.schema_version,
        "view": view,
        "run_id": snapshot.run_id,
        "question_id": snapshot.question_id,
        "trace_id": snapshot.trace_id,
        "otel_trace_id": snapshot.otel_trace_id,
        "incomplete": list(snapshot.incomplete),
        "dropped_events": dropped,
        "data": {key: data.get(key) for key in keys},
        "approval_recovery": "postgresql_ledger"
        if data.get("approval")
        else "unavailable_until_m15",
        "redaction": "stable_identifiers; span_offsets_reference_original_before_redaction",
    }


def export_json(value: object) -> str:
    def safe(item: object) -> object:
        if isinstance(item, dict):
            return {str(k): safe(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [safe(v) for v in item]
        if isinstance(item, str) and item.lstrip().startswith(("=", "+", "-", "@")):
            return "'" + item
        return item

    return json.dumps(safe(value), ensure_ascii=False, indent=2)
