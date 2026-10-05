"""工作流内Skill管理，owner沿现有catalog；浏览/候选/审核均0模型。

接口不接收路径/工具URL/代码。编辑只生成新候选，在线试运行仍走原converse与发布。
同步短文件操作没有await，因此不持锁跨模型/工具；重任务归原评测入口。
"""

from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import Field

from deephelp_app.adapters.releases.skill_store import SkillStore, skill_diff
from deephelp_app.api.workbench import BrowserAuth
from deephelp_app.application.skills import SkillRegistry
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import DTO, ErrorCode, VerifiedIdentity
from deephelp_tools.learning.workbench.evaluation import Hash, LearningRoute


class SkillEdit(DTO):
    base_digest: Hash
    skill_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    version: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,31}$")
    goal: str = Field(min_length=1, max_length=2000)
    confirmed: Literal[True]


class SkillReview(DTO):
    revision: int = Field(strict=True, ge=1)
    approve: bool
    reason: str = Field(min_length=1, max_length=1000)
    confirmed: Literal[True]


def edited_registry(base: SkillRegistry, edit: SkillEdit) -> SkillRegistry:
    """仅修改已登记叶子的版本/说明；引用者形成新版本与精确新依赖闭包。"""
    if edit.base_digest != base.digest:
        raise AppError(ErrorCode.VERSION_CONFLICT, "Published Skill base changed", 409)
    old = next((d for d in base.definitions if d.skill_id == edit.skill_id), None)
    if old is None or old.version == edit.version:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Edit requires a known leaf and a new version")
    child = old.model_copy(update={"version": edit.version, "goal": edit.goal})
    raw = base.content()
    raw["version"] = "candidate-" + uuid4().hex[:12]
    raw["definitions"] = [
        child.model_dump(mode="json") if d.skill_id == old.skill_id else d.model_dump(mode="json")
        for d in base.definitions
    ]
    for composition in raw.get("compositions", []):
        for step in composition["steps"]:
            if step["skill_id"] == old.skill_id:
                step.update(version=child.version, digest=child.digest)
                composition["version"] += "-" + child.version
    return SkillRegistry.model_validate(raw)


def skills_router(
    assembly: Any, auth: BrowserAuth, owner: VerifiedIdentity, root: Path
) -> APIRouter:
    router = APIRouter(prefix="/learning/skills", route_class=LearningRoute)
    store = SkillStore(root)

    def scoped(request: Request) -> SkillRegistry:
        if auth(request) != owner:
            raise AppError(ErrorCode.NOT_FOUND, "Skill learning scope unavailable", 404)
        if not getattr(assembly, "skills_enabled", False):
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "This release has no configured Skills", 501)
        return SkillRegistry.model_validate(assembly.skills_registry.content())

    @router.get("")
    async def details(request: Request) -> dict[str, Any]:
        base = scoped(request)
        return {
            "published": base.content(),
            "published_digest": base.digest,
            "candidates": store.list(),
            "selection": "deterministic_intent_tool_intersection",
            "model_calls": 0,
            "trial": "explicit original converse for published version",
        }

    @router.get("/{candidate}/diff")
    async def difference(candidate: Hash, request: Request) -> dict[str, Any]:
        base = scoped(request)
        value = store.get(candidate)
        return {
            "candidate": value,
            "changes": skill_diff(base, SkillRegistry.model_validate(value["registry"])),
            "model_calls": 0,
        }

    @router.post("/candidates")
    async def edit(body: SkillEdit, request: Request) -> dict[str, Any]:
        base = scoped(request)
        try:
            return store.propose(edited_registry(base, body), base)
        except ValueError as exc:
            raise AppError(ErrorCode.VERSION_CONFLICT, str(exc), 409) from exc

    @router.post("/{candidate}/review")
    async def review(candidate: Hash, body: SkillReview, request: Request) -> dict[str, Any]:
        scoped(request)
        return store.review(candidate, body.revision, owner, body.reason, body.approve)

    return router
