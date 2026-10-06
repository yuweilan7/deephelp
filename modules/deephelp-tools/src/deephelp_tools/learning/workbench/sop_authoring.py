"""原工作流页面的SOP作者API，复用BrowserAuth/同源保护和服务端图契约。

catalog决定根路径与唯一学习owner；保存只写草稿，运行registry不热替换。
服务端回传校验/真实revision，页面可保存无效中间图并定位修正。
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import Field

from deephelp_app.adapters.releases.sop_drafts import SOPDraftStore, draft_registry
from deephelp_app.api.workbench import BrowserAuth
from deephelp_app.application.sop_governance import FACT_TYPES, TOOL_GRANTS, SOPRegistry
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import DTO, ErrorCode, Identifier, VerifiedIdentity
from deephelp_tools.learning.replay.sop_rehearsal import execute_rehearsal
from deephelp_tools.learning.workbench.evaluation import LearningRoute
from deephelp_tools.learning.workbench.jobs import LearningJobs


class CreateDraft(DTO):
    sop_id: Identifier
    base_hash: Identifier


class SaveDraft(DTO):
    revision: int = Field(strict=True, ge=1)
    content: dict[str, Any]


class RehearsalInput(DTO):
    revision: int = Field(strict=True, ge=1)
    mode: Literal["offline", "live-readonly"] = "offline"
    slots: dict[str, Identifier] = Field(default_factory=dict, max_length=8)
    scenario: Literal["normal", "invalid_fact", "tool_failure", "slow"] = "normal"
    confirmed: bool = False


def sop_authoring_router(
    assembly: Any,
    auth: BrowserAuth,
    owner: VerifiedIdentity,
    root: Path,
    budget_factory: Callable[[], ExecutionBudget] | None = None,
    budget_available: Callable[[], bool] | None = None,
) -> APIRouter:
    store = SOPDraftStore(root)

    def live_available() -> bool:
        # 安装router早于app lifespan；只检查原累计writer是否就绪，读目录不领取预算。
        return bool(
            getattr(assembly, "gateway", None)
            and getattr(assembly, "tools", None)
            and budget_factory
            and (budget_available is None or budget_available())
        )

    async def run(row: dict[str, Any], output: Path) -> dict[str, Any]:
        """LearningJobs拥有worker；原装配拥有真实Port和唯一累计预算writer。

        原节点回调把同一run的部分观察持久写入任务；终态与取消由jobs在finally保存。
        output不需要第二份完整导出；任务JSON已经是预演事实源。
        """

        def progress(value: dict[str, Any]) -> None:
            row["observation"] = value
            jobs.save(row)

        options = row["options"]
        live = options["mode"] == "live-readonly"
        return await execute_rehearsal(
            options,
            model=getattr(assembly, "gateway", None),
            tools=getattr(assembly, "tools", None),
            budget=budget_factory() if live and budget_factory else None,
            progress=progress,
        )

    jobs = LearningJobs(root / "rehearsals", run, timeout=100)

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await jobs.close()

    router = APIRouter(prefix="/learning/sop", route_class=LearningRoute, lifespan=lifespan)

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
            "live_readonly_available": live_available(),
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

    @router.post("/drafts/{draft_id}/rehearsals")
    async def rehearse(draft_id: str, body: RehearsalInput, request: Request) -> dict[str, Any]:
        base = scoped(request)
        draft = store.get(draft_id, owner)
        if body.revision != draft["revision"]:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Rehearsal draft revision changed", 409)
        if not draft["validation"]["valid"]:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Fix invalid graph before rehearsal", 400)
        registry = draft_registry(draft)
        flow = registry.definition(draft["content"]["intent_code"])
        if set(body.slots) - {s.value for s in flow.required_slots} or any(
            n.proposal for n in flow.nodes
        ):
            raise AppError(
                ErrorCode.FORBIDDEN, "Rehearsal input/proposal exceeds fixed grants", 403
            )
        if body.mode == "live-readonly":
            if not body.confirmed or body.scenario != "normal":
                raise AppError(
                    ErrorCode.FORBIDDEN, "Live rehearsal requires explicit read-only choice", 403
                )
            if draft["base_hash"] != base.snapshot_hash:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Live rehearsal base changed", 409)
            if not live_available():
                raise AppError(
                    ErrorCode.NOT_IMPLEMENTED, "Original live read-only runtime unavailable", 503
                )
        return await jobs.start(
            dict(
                **body.model_dump(mode="json"),
                draft_id=draft_id,
                draft_hash=draft["content_hash"],
                registry=registry.model_dump(mode="json"),
                intent_code=flow.intent_code.value,
                owner=owner.model_dump(mode="json"),
                run_id="rehearsal-" + uuid4().hex,
            )
        )

    def owned_job(identity: str, request: Request) -> dict[str, Any]:
        scoped(request)
        row = jobs.get(identity)
        if row["options"]["owner"] != owner.model_dump(mode="json"):
            raise AppError(ErrorCode.NOT_FOUND, "Rehearsal not found", 404)
        return row

    @router.get("/drafts/{draft_id}/rehearsals")
    async def list_rehearsals(draft_id: str, request: Request) -> dict[str, Any]:
        scoped(request)
        store.get(draft_id, owner)
        rows = [
            row
            for row in jobs.rows.values()
            if row["options"]["draft_id"] == draft_id
            and row["options"]["owner"] == owner.model_dump(mode="json")
        ]
        return {"jobs": sorted(rows, key=lambda r: r["created_at"], reverse=True), "model_calls": 0}

    @router.get("/rehearsals/{identity}")
    async def read_rehearsal(identity: str, request: Request) -> dict[str, Any]:
        return owned_job(identity, request)

    @router.post("/rehearsals/{identity}/cancel")
    async def cancel_rehearsal(identity: str, request: Request) -> dict[str, Any]:
        owned_job(identity, request)
        return await jobs.cancel(identity)

    return router
