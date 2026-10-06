"""原评测页的独立样本入口；路径/owner由服务器决定，浏览与盲审模型0次。

路由在父lifespan就绪后借PG池。接收/审核只处理数据，不运行对话；独立来源
由实际导入主体提交声明收据，不能通过更换reviewer_id伪称经过人类盲审。
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Request
from pydantic import Field

from deephelp_app.api.workbench import BrowserAuth
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import DTO, ErrorCode, VerifiedIdentity
from deephelp_tools.evaluation.quality.independent_cli import development_inputs
from deephelp_tools.evaluation.quality.independent_data import (
    Annotation,
    Intake,
    adjudicate,
    annotate,
    freeze,
    receive,
    summary,
)
from deephelp_tools.evaluation.quality.independent_store import IndependentDatasets


class Revision(DTO):
    revision: int = Field(strict=True, ge=0)


class ReviewInput(Revision):
    annotation: Annotation


def independent_router(
    assembly: Any, auth: BrowserAuth, owner: VerifiedIdentity, *, store: Any = None
) -> APIRouter:
    from deephelp_tools.learning.workbench.evaluation import LearningRoute

    repository: Any = store
    migrated = False
    migration_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        nonlocal repository
        pool = getattr(getattr(assembly, "ledger", None), "pool", None)
        repository = store or (IndependentDatasets(pool) if pool else None)
        yield

    router = APIRouter(prefix="/independent", lifespan=lifespan, route_class=LearningRoute)

    async def scoped(request: Request) -> dict[str, Any]:
        nonlocal migrated
        if auth(request) != owner:
            raise AppError(ErrorCode.NOT_FOUND, "Independent evaluation not found", 404)
        if repository is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Independent data requires PostgreSQL", 501)
        # 首次独立数据操作才迁移；原评测/业务浏览不因此新增DB动作。
        # 锁仅串行化幂等DDL，取消后migrated仍false，下一请求可重新完成。
        async with migration_lock:
            if not migrated:
                await repository.migrate()
                migrated = True
        return owner.model_dump(mode="json")

    @router.get("")
    async def browse(request: Request) -> dict[str, Any]:
        await scoped(request)
        rows = await repository.list(owner)
        return dict(
            status="AVAILABLE" if rows else "DATA_NOT_AVAILABLE",
            datasets=[summary(r) for r in rows],
            model_calls=0,
        )

    @router.post("/datasets")
    async def intake(body: Intake, request: Request) -> dict[str, Any]:
        actor = await scoped(request)
        historical = await asyncio.to_thread(development_inputs)
        row = await asyncio.to_thread(receive, body, actor, historical)
        return summary(await repository.create(owner, row))

    @router.get("/datasets/{dataset_id}")
    async def read(dataset_id: str, request: Request) -> dict[str, Any]:
        await scoped(request)
        return summary(await repository.get(owner, dataset_id))

    @router.get("/datasets/{dataset_id}/inputs")
    async def inputs(dataset_id: str, request: Request) -> dict[str, Any]:
        await scoped(request)
        row = await repository.get(owner, dataset_id)
        # 盲标投影只有输入；scenario是来源描述，也不传给标签人作为标签提示。
        return dict(
            input_hash=row["input_hash"],
            cases=[dict(case_id=c["case_id"], turns=c["turns"]) for c in row["intake"]["cases"]],
            model_calls=0,
        )

    @router.post("/datasets/{dataset_id}/annotations")
    async def review(dataset_id: str, body: ReviewInput, request: Request) -> dict[str, Any]:
        actor = await scoped(request)
        return summary(
            await repository.change(
                owner, dataset_id, body.revision, lambda r: annotate(r, body.annotation, actor)
            )
        )

    @router.post("/datasets/{dataset_id}/adjudication")
    async def resolve(dataset_id: str, body: ReviewInput, request: Request) -> dict[str, Any]:
        actor = await scoped(request)
        return summary(
            await repository.change(
                owner, dataset_id, body.revision, lambda r: adjudicate(r, body.annotation, actor)
            )
        )

    @router.post("/datasets/{dataset_id}/freeze")
    async def lock(dataset_id: str, body: Revision, request: Request) -> dict[str, Any]:
        await scoped(request)
        return summary(await repository.change(owner, dataset_id, body.revision, freeze))

    return router
