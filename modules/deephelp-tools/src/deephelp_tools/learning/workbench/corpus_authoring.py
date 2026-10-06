"""原识别/检索页的语料作者API，复用同源BrowserAuth与PG聚合事务。

router借用原ledger池，不拥有新连接池或模型预算；无PG时明确不可用。
预览/浏览/审核/导出均0模型；路径和owner由服务器提供，客户端只有记录/预览ID。
"""

import base64
import binascii
import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import Field

from deephelp_app.adapters.persistence.corpus_workspaces import PostgresCorpusWorkspaces
from deephelp_app.adapters.releases.feedback_store import FeedbackReader
from deephelp_app.api.workbench import BrowserAuth
from deephelp_app.application.corpus import read_corpus_bytes
from deephelp_app.application.corpus_authoring import (
    corpus_bytes,
    delete_record,
    edit_record,
    import_feedback,
    import_preview,
    review_records,
    stage_preview,
    version_diff,
)
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import DTO, ErrorCode, VerifiedIdentity
from deephelp_app.domain.models.corpus_authoring import (
    CorpusEdit,
    CorpusFeedback,
    CorpusFreeze,
    CorpusImport,
    CorpusReview,
    CorpusUpload,
)
from deephelp_app.domain.registry import complaint_registry
from deephelp_tools.learning.workbench.evaluation import LearningRoute


class CorpusDelete(DTO):
    revision: int = Field(strict=True, ge=0)


def corpus_authoring_router(
    assembly: Any, auth: BrowserAuth, owner: VerifiedIdentity, *, store: Any = None
) -> APIRouter:
    pool: Any = None
    repository: Any = store
    catalog = ""

    @asynccontextmanager
    async def lifespan(app: Any) -> AsyncIterator[None]:
        nonlocal pool, repository, catalog
        # 正式ReleaseRuntime在外层应用lifespan才打开原装配；注册路由时尚无ledger/目录。
        # 此处在父生命周期就绪后借池并迁移，所有权仍属原装配，退出不关闭借用池。
        pool = getattr(getattr(assembly, "ledger", None), "pool", None)
        catalog = assembly.sop_registry.catalog_version
        repository = store or (PostgresCorpusWorkspaces(pool) if pool else None)
        if repository is not None:
            await repository.migrate()
        yield

    router = APIRouter(prefix="/learning/corpus", route_class=LearningRoute, lifespan=lifespan)

    def scoped(request: Request) -> None:
        if auth(request) != owner:
            raise AppError(ErrorCode.NOT_FOUND, "Corpus workspace not found", 404)
        if repository is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Corpus authoring requires PostgreSQL", 501)
        if not catalog:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Corpus runtime is not ready", 503)

    def view(row: dict[str, Any], offset: int = 0, limit: int = 20) -> dict[str, Any]:
        value = copy.deepcopy(row)
        entries = sorted(value.pop("entries").values(), key=lambda e: e["record"]["doc_id"])
        value["entries"] = entries[offset : offset + limit]
        value["total"] = len(entries)
        value["next_offset"] = offset + limit if offset + limit < len(entries) else None
        value["previews"] = list(value["previews"].values())
        value["labels"] = [
            d.model_dump(mode="json")
            for d in complaint_registry(catalog).intents
            if d.is_actionable
        ]
        value["model_calls"] = 0
        value["authority"] = "PostgreSQL corpus drafts; never business facts"
        return value

    @router.get("")
    async def browse(
        request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)
    ) -> dict[str, Any]:
        scoped(request)
        return view(await repository.get(owner, catalog), offset, limit)

    @router.post("/records")
    async def create(body: CorpusEdit, request: Request) -> dict[str, Any]:
        scoped(request)
        return view(
            await repository.change(
                owner,
                catalog,
                body.revision,
                lambda row: edit_record(row, body.record, body.source, create=True),
            )
        )

    @router.put("/records/{doc_id}")
    async def update(doc_id: str, body: CorpusEdit, request: Request) -> dict[str, Any]:
        scoped(request)
        if body.record.doc_id != doc_id:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Editing cannot rename document ID", 400)
        return view(
            await repository.change(
                owner,
                catalog,
                body.revision,
                lambda row: edit_record(row, body.record, body.source, create=False),
            )
        )

    @router.delete("/records/{doc_id}")
    async def delete(doc_id: str, body: CorpusDelete, request: Request) -> dict[str, Any]:
        scoped(request)
        return view(
            await repository.change(
                owner, catalog, body.revision, lambda row: delete_record(row, doc_id)
            )
        )

    @router.post("/previews")
    async def preview(body: CorpusUpload, request: Request) -> dict[str, Any]:
        scoped(request)
        try:
            data = base64.b64decode(body.data_base64, validate=True)
            parsed = read_corpus_bytes(data, "." + body.format, columns=body.columns)
        except ValueError, UnicodeError, binascii.Error:
            raise AppError(
                ErrorCode.INVALID_ARGUMENT, "Invalid upload encoding/size/format", 400
            ) from None
        return view(
            await repository.change(
                owner, catalog, body.revision, lambda row: stage_preview(row, parsed)
            )
        )

    @router.delete("/previews/{preview_id}")
    async def remove_preview(
        preview_id: str, body: CorpusDelete, request: Request
    ) -> dict[str, Any]:
        scoped(request)

        def remove(row: dict[str, Any]) -> dict[str, Any]:
            if (
                preview_id not in row["previews"]
                or row["previews"][preview_id]["status"] != "PREVIEW"
            ):
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "Only unused previews can be removed", 400
                )
            row["previews"].pop(preview_id)
            return row

        return view(await repository.change(owner, catalog, body.revision, remove))

    @router.post("/imports")
    async def confirm_import(body: CorpusImport, request: Request) -> dict[str, Any]:
        scoped(request)
        return view(
            await repository.change(
                owner, catalog, body.revision, lambda row: import_preview(row, body.preview_id)
            )
        )

    @router.post("/reviews")
    async def review(body: CorpusReview, request: Request) -> dict[str, Any]:
        scoped(request)
        return view(
            await repository.change(
                owner,
                catalog,
                body.revision,
                lambda row: review_records(
                    row, body.doc_ids, body.approve, body.reason, owner.model_dump(mode="json")
                ),
            )
        )

    @router.get("/export")
    async def export(request: Request) -> dict[str, Any]:
        scoped(request)
        row = await repository.get(owner, catalog)
        return dict(
            revision=row["revision"],
            catalog_version=catalog,
            model_calls=0,
            jsonl=corpus_bytes(row).decode(),
            note="Draft export, not reviewed release or business facts",
        )

    @router.post("/versions")
    async def freeze(body: CorpusFreeze, request: Request) -> dict[str, Any]:
        scoped(request)
        value: dict[str, Any] = await repository.freeze(owner, catalog, body.revision, body.version)
        return value

    @router.post("/feedback")
    async def from_feedback(body: CorpusFeedback, request: Request) -> dict[str, Any]:
        scoped(request)
        if pool is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Original feedback requires PostgreSQL", 501)
        candidate = await FeedbackReader(pool).get(body.candidate_id)
        if candidate.revision != body.candidate_revision:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Feedback revision changed", 409)
        review = candidate.reviews.get("corpus")
        if review is None or review.reviewer != f"{owner.tenant_id}/{owner.user_id}":
            raise AppError(
                ErrorCode.NOT_FOUND, "Feedback approval belongs to another reviewer", 404
            )
        return view(
            await repository.change(
                owner, catalog, body.revision, lambda row: import_feedback(row, candidate)
            )
        )

    @router.get("/versions/{identity}/diff/{base}")
    async def differences(identity: str, base: str, request: Request) -> dict[str, Any]:
        scoped(request)
        before = await repository.version(owner, catalog, base)
        after = await repository.version(owner, catalog, identity)
        return version_diff(before, after)

    @router.get("/versions")
    async def versions(request: Request) -> dict[str, Any]:
        scoped(request)
        scope = getattr(assembly, "scope", None)
        return dict(
            versions=await repository.versions(owner, catalog),
            model_calls=0,
            published_scope=scope.model_dump(mode="json") if scope is not None else None,
        )

    @router.get("/versions/{identity}")
    async def version(identity: str, request: Request) -> dict[str, Any]:
        scoped(request)
        value: dict[str, Any] = await repository.version(owner, catalog, identity)
        return value

    return router
