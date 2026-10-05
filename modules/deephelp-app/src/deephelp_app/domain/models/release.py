"""问题固定版本、完整发布清单与active指针的值。

清单绑定不可变资产和runtime/tooling精确字节，不持有客户端或锁。
ReleasePointer的revision用于持久CAS，切换清单不自动回滚业务事实或预算。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    Field,
)

from .common import CONTRACT_VERSION, DTO, Count, Identifier


class VersionManifest(DTO):
    contract: Literal["0.2.0-m02"] = CONTRACT_VERSION
    registry: Identifier | None = None
    dataset: Identifier | None = None
    split: Identifier | None = None
    embedding_signature: Identifier | None = None
    model: Identifier | None = None
    provider: Identifier | None = None
    prompt: Identifier | None = None
    sop: Identifier | None = None
    policy: Identifier | None = None
    code_commit: Identifier | None = None
    fasttext_model: Identifier | None = None
    fasttext_preprocessing: Identifier | None = None
    fasttext_policy: Identifier | None = None
    sop_registry: Identifier | None = None
    sop_snapshot: Identifier | None = None
    sop_prompt: Identifier | None = None
    tool_schema: Identifier | None = None
    release_manifest: Identifier | None = None
    collaboration: Identifier | None = None
    skills: Identifier | None = None


class ReleaseManifest(DTO):
    """Serializable content identity; no clients, connections or runtime locks."""

    format: Literal["m18-release-v1"] = "m18-release-v1"
    version: Identifier
    assets_path: str = Field(min_length=1, max_length=1024)
    assets_digest: Identifier
    files: dict[str, str] = Field(min_length=1)
    corpus_digest: Identifier
    embedding_signature: dict[str, Any]
    fasttext: dict[str, Any]
    rules_digest: Identifier
    sop_registry: dict[str, Any]
    sop_snapshot: Identifier
    prompts: dict[str, str] = Field(min_length=1)
    providers_path: str = Field(min_length=1, max_length=1024)
    providers_digest: Identifier
    judge_providers_path: str
    judge_providers_digest: Identifier
    code_digest: Identifier
    code_commit: Identifier
    code_dirty: bool = False
    review_snapshot: dict[str, Any]
    validation_path: str
    validation_digest: Identifier
    skill_registry: dict[str, Any] | None = None
    skill_review_path: str | None = None


class ReleasePointer(DTO):
    channel: Identifier
    revision: Count
    active: Identifier | None = None
    previous: Identifier | None = None
