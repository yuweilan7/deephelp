"""精确发布绑定长期记忆策略、DTO schema和真实内容原件；不运行模型或业务。

旧清单没有该字段时保持原身份；新清单必须验证当前字节与完整记忆场景，不能套旧PASS。
"""

import json
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from deephelp_app.adapters.releases.asset_integrity import file_digest, provenance
from deephelp_app.application.artifact_paths import resolve_artifact
from deephelp_app.application.corpus import digest
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.conversation import RequestEnvelope
from deephelp_app.domain.models.long_memory import (
    LongMemory,
    MemoryChange,
    MemoryScope,
    MemorySelection,
    MemoryUse,
)
from deephelp_app.domain.models.responses import ResponseEnvelope


def memory_policy_binding() -> dict[str, Any]:
    """显式固定策略与领域schema共同绑定；Prompt/索引明确为none，不虚构Embedding召回。"""
    resource = files("deephelp_app").joinpath("resources/memory-policy.json")
    policy = json.loads(resource.read_text())
    return {
        "policy": policy,
        "resource": "resources/memory-policy.json",
        "sha256": file_digest(Path(str(resource))),
        "schema_digest": digest(
            {
                cls.__name__: cls.model_json_schema()
                for cls in (LongMemory, MemoryChange, MemoryScope, MemorySelection, MemoryUse)
            }
        ),
    }


def require_memory_content(path: Path) -> dict[str, Any]:
    report = json.loads(resolve_artifact(path).read_text(encoding="utf-8"))
    required = {
        "enabled",
        "disabled",
        "different-order",
        "missing-current",
        "unrelated",
        "continuation",
        "corrected",
        "forgotten",
    }
    if (
        report.get("status") != "PASS"
        or report.get("memory_policy_binding") != memory_policy_binding()
        or report.get("code_digest") != provenance()["package_digest"]
        or {r["case_id"] for r in report.get("rows", [])} != required
        or not report.get("checks")
        or not all(v is True for v in report["checks"].values())
    ):
        raise ConfigurationError(
            "Memory release requires exact policy/schema and complete current content"
        )
    rows = {r["case_id"]: r for r in report["rows"]}
    responses = {
        name: ResponseEnvelope.model_validate(row["response"]) for name, row in rows.items()
    }
    for name, response in responses.items():
        request = RequestEnvelope.model_validate(rows[name]["input"])
        selection = response.memory_selection
        expected = "SELECTED" if name in {"enabled", "continuation", "corrected"} else "REJECTED"
        if (
            not request.history_memory
            or not response.run_id
            or response.error
            or not selection
            or selection.status != expected
            or response.versions.memory != "m46-explicit-v1"
            or (
                name not in {"unrelated", "missing-current"}
                and (not response.facts or not response.evidence_refs)
            )
        ):
            raise ConfigurationError(
                "Memory content has incomplete actual run/source/current facts"
            )
    if (
        responses["enabled"].facts != responses["disabled"].facts
        or responses["unrelated"].facts
        or responses["unrelated"].tool_call_ids
        or not responses["missing-current"].missing_slots
        or responses["missing-current"].tool_call_ids
        or responses["continuation"].question_id != responses["missing-current"].question_id
    ):
        raise ConfigurationError("Memory fixed negative/continuation boundaries failed")
    return cast(dict[str, Any], report)
