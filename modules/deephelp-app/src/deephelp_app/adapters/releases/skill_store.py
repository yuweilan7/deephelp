"""本地单writer管理Skill候选与审核，不改业务active或已运行对象。

候选registry不可变，review使用revision CAS；审核生成另一个不可变工件供完整发布绑定。
所有文件都在显式配置根内，短文件锁不跨await/模型；生产只消费ReleaseManifest。
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deephelp_app.adapters.mcp.business.mcp_protocol import canonical
from deephelp_app.application.corpus import digest
from deephelp_app.application.dense import atomic_json
from deephelp_app.application.skills import SkillRegistry, register_skills
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity


def read_skill_review(path: Path) -> tuple[SkillRegistry, dict[str, Any]]:
    """只读审核原件；完整发布另将实际文件hash纳入files，不接受未审核候选。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    registry = SkillRegistry.model_validate(value["registry"])
    if (
        value.get("status") != "APPROVED"
        or value.get("candidate_digest") != registry.digest
        or not value.get("reason")
    ):
        raise ValueError("Skill release requires matching explicit approval")
    VerifiedIdentity.model_validate(value["reviewer"])
    return registry, value


class SkillStore:
    """候选单writer，业务发布仍由原ReleaseStore登记/CAS；不自动激活。"""

    def __init__(self, root: Path) -> None:
        self.root = root

    @contextmanager
    def writer(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "writer.lock"
        try:
            stream = path.open("x", encoding="utf-8")
        except FileExistsError:
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Skill candidate writer is busy", 409
            ) from None
        try:
            with stream:
                yield
        finally:
            path.unlink()

    def path(self, candidate: str) -> Path:
        if len(candidate) != 64 or any(c not in "0123456789abcdef" for c in candidate):
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Invalid Skill candidate digest")
        return self.root / "candidates" / (candidate + ".json")

    def get(self, candidate: str) -> dict[str, Any]:
        path = self.path(candidate)
        if not path.exists():
            raise AppError(ErrorCode.NOT_FOUND, "Skill candidate unavailable", 404)
        value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        if (
            SkillRegistry.model_validate(value["registry"]).digest != candidate
            or value.get("candidate_digest") != candidate
        ):
            raise AppError(ErrorCode.VERSION_CONFLICT, "Skill candidate bytes drifted", 409)
        return value

    def list(self) -> list[dict[str, Any]]:
        paths = sorted((self.root / "candidates").glob("*.json"))
        if len(paths) > 100:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Skill candidate bound exceeded")
        return [self.get(path.stem) for path in paths]

    def propose(self, registry: SkillRegistry, base: SkillRegistry) -> dict[str, Any]:
        """新候选不影响运行；预登记原/新版本阻止跨候选的同版本覆盖。"""
        registry = SkillRegistry.model_validate(registry.content())
        with self.writer():
            path = self.path(registry.digest)
            if path.exists():
                return self.get(registry.digest)
            if len(self.list()) >= 100:
                raise AppError(ErrorCode.INVALID_ARGUMENT, "Skill candidate bound exceeded")
            register_skills(self.root / "versions", base)
            register_skills(self.root / "versions", registry)
            value = dict(
                candidate_digest=registry.digest,
                base_digest=base.digest,
                registry=registry.content(),
                revision=1,
                status="CANDIDATE",
                created_at=datetime.now(UTC).isoformat(),
            )
            path.parent.mkdir(exist_ok=True)
            atomic_json(path, value)
            return value

    def review(
        self, candidate: str, revision: int, reviewer: VerifiedIdentity, reason: str, approved: bool
    ) -> dict[str, Any]:
        """短锁内CAS审核，生成不可变原件；审批副作用与业务发布均未发生。"""
        if not reason.strip() or len(reason) > 1000:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Explicit bounded review reason required")
        with self.writer():
            value = self.get(candidate)
            if value["revision"] != revision or value["status"] != "CANDIDATE":
                raise AppError(ErrorCode.VERSION_CONFLICT, "Skill candidate review changed", 409)
            value.update(
                status="APPROVED" if approved else "REJECTED",
                revision=revision + 1,
                reviewer=reviewer.model_dump(mode="json"),
                reason=reason,
                reviewed_at=datetime.now(UTC).isoformat(),
            )
            fingerprint = digest(value)
            directory = self.root / "reviews"
            directory.mkdir(exist_ok=True)
            with (directory / (fingerprint + ".json")).open("x", encoding="utf-8") as stream:
                stream.write(canonical(value).decode())
            value["review_digest"] = fingerprint
            atomic_json(self.path(candidate), value)
            return value

    def approved(self, candidate: str) -> Path:
        value = self.get(candidate)
        if value["status"] != "APPROVED":
            raise AppError(ErrorCode.VERSION_CONFLICT, "Skill candidate has not been approved", 409)
        review_digest = str(value["review_digest"])
        # candidate元数据漂移也不能把审核读取引向根外。
        self.path(review_digest)
        path = self.root / "reviews" / (review_digest + ".json")
        _, reviewed = read_skill_review(path)
        if digest(reviewed) != review_digest or reviewed != {
            k: v for k, v in value.items() if k != "review_digest"
        }:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Skill review bytes drifted", 409)
        return path


def skill_diff(before: SkillRegistry, after: SkillRegistry) -> list[dict[str, Any]]:
    """版本差异只读机器字段，比较目标/许可/schema/步骤与精确摘要，无模型。"""
    old = {d.skill_id: d.model_dump(mode="json") for d in before.entries()}
    new = {d.skill_id: d.model_dump(mode="json") for d in after.entries()}
    return [
        {"skill_id": key, "before": old.get(key), "after": new.get(key)}
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    ]
