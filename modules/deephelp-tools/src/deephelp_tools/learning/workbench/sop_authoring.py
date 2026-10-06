"""原工作流页面的SOP作者API，复用BrowserAuth/同源保护和服务端图契约。

catalog决定根路径与唯一学习owner；保存只写草稿，运行registry不热替换。
服务端回传校验/真实revision，页面可保存无效中间图并定位修正。
"""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from deephelp_app.adapters.releases.sop_drafts import SOPDraftStore
from deephelp_app.api.workbench import BrowserAuth
from deephelp_app.application.sop_governance import FACT_TYPES, TOOL_GRANTS, SOPRegistry
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import DTO, ErrorCode, Identifier, VerifiedIdentity
from deephelp_tools.learning.workbench.evaluation import LearningRoute


class CreateDraft(DTO):
    sop_id: Identifier
    base_hash: Identifier


class SaveDraft(DTO):
    revision: int = Field(strict=True, ge=1)
    content: dict[str, Any]


def sop_authoring_router(
    assembly: Any, auth: BrowserAuth, owner: VerifiedIdentity, root: Path
) -> APIRouter:
    router = APIRouter(prefix="/learning/sop", route_class=LearningRoute)
    store = SOPDraftStore(root)

    def scoped(request: Request) -> SOPRegistry:
        if auth(request) != owner:
            raise AppError(ErrorCode.NOT_FOUND, "SOP authoring scope unavailable", 404)
        return SOPRegistry.model_validate(assembly.sop_registry.model_dump(mode="json"))

    @router.get("")
    async def catalog(request: Request) -> dict[str, Any]:
        base = scoped(request)
        return {
            "published": base.model_dump(mode="json"),
            "base_hash": base.snapshot_hash,
            "drafts": store.list(owner),
            "facts": {k.value: v for k, v in FACT_TYPES.items()},
            "grants": {k.value: [t.value for t in v] for k, v in TOOL_GRANTS.items()},
            "model_calls": 0,
        }

    @router.post("/drafts")
    async def create(body: CreateDraft, request: Request) -> dict[str, Any]:
        base = scoped(request)
        if body.base_hash != base.snapshot_hash:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Published SOP base changed", 409)
        return store.create(owner, base, body.sop_id)

    @router.get("/drafts/{draft_id}")
    async def read(draft_id: str, request: Request) -> dict[str, Any]:
        base = scoped(request)
        value = store.get(draft_id, owner)
        return {**value, "base_current": value["base_hash"] == base.snapshot_hash, "model_calls": 0}

    @router.put("/drafts/{draft_id}")
    async def save(draft_id: str, body: SaveDraft, request: Request) -> dict[str, Any]:
        scoped(request)
        return store.update(draft_id, owner, body.revision, body.content)

    return router
