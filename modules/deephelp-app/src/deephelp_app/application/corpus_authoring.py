"""语料工作区的纯状态变换，PG和离线课程共用，不连接模型/索引或执行业务。

先读checked_records的目录/split防线，再看edit/preview/import/review。
调用者持有短事务锁并检查revision；这里先校验完整目标，再返回新聚合，失败不半写。
导入预览保留源hash及逐行位置；它不批准记录，也不进入正式索引。
"""

import copy
import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from deephelp_app.application.corpus import (
    MAX_ROWS,
    CorpusPreview,
    digest,
    normalized,
    validate_records,
)
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode
from deephelp_app.domain.models.retrieval import CorpusRecord


def empty_workspace(catalog: str) -> dict[str, Any]:
    return dict(
        format="m44-corpus-workspace-v1",
        catalog_version=catalog,
        revision=0,
        entries={},
        previews={},
        reviews=[],
    )


def checked_records(raw: list[dict[str, Any]], catalog: str) -> CorpusPreview:
    """校验整个目标的标签/重复/分组和跨split近重复，不把语义相似当人工金标。

    字符近重复是保守泄漏守卫；分组由作者声明，不能靠一个相似度保证独立盲测。
    """
    result = validate_records(list(enumerate(raw, 1)), digest(raw))
    errors = list(result.errors)
    texts: list[tuple[str, CorpusRecord]] = []
    for number, record in enumerate(result.records, 1):
        if record.registry_version != catalog:
            errors.append(f"row {number}: Catalog differs from workspace")
        text = re.sub(r"[\W_]", "", normalized(record.content)).casefold()
        for old_text, old in texts:
            if old.split != record.split and min(len(text), len(old_text)) >= 6:
                if SequenceMatcher(None, text, old_text, autojunk=False).ratio() >= 0.9:
                    errors.append(f"row {number}: Cross-split near duplicate of {old.doc_id}")
                    break
        texts.append((text, record))
    return CorpusPreview(result.file_sha256, result.records, tuple(errors))


def require_records(raw: list[dict[str, Any]], catalog: str) -> None:
    if not raw:
        return
    value = checked_records(raw, catalog)
    if value.errors:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "; ".join(value.errors), 400)


def edit_record(
    workspace: dict[str, Any], record: CorpusRecord, source: str, *, create: bool
) -> dict[str, Any]:
    """新增必须无同ID；编辑必须已有ID，修改后审核退回DRAFT，旧审核保留审计。"""
    row = copy.deepcopy(workspace)
    old = row["entries"].get(record.doc_id)
    if create == (old is not None):
        raise AppError(ErrorCode.VERSION_CONFLICT, "Document ID exists or is absent", 409)
    row["entries"][record.doc_id] = dict(
        record=record.model_dump(mode="json"),
        source=source,
        status="DRAFT",
        revision=(old["revision"] + 1 if old else 1),
    )
    require_records([e["record"] for e in row["entries"].values()], row["catalog_version"])
    return row


def delete_record(workspace: dict[str, Any], doc_id: str) -> dict[str, Any]:
    row = copy.deepcopy(workspace)
    if doc_id not in row["entries"]:
        raise AppError(ErrorCode.NOT_FOUND, "Corpus draft not found", 404)
    del row["entries"][doc_id]
    return row


def stage_preview(workspace: dict[str, Any], preview: CorpusPreview) -> dict[str, Any]:
    """预览也持久保存；只保存解析后记录/hash，上传字节不落临时文件。

    与已有记录整体验证后才允许导入。失败预览有诊断但不改变entries。
    """
    row = copy.deepcopy(workspace)
    if len(row["previews"]) >= 8:
        raise AppError(ErrorCode.VERSION_CONFLICT, "Remove an old preview before uploading", 409)
    records = [r.model_dump(mode="json") for r in preview.records]
    errors = list(preview.errors)
    combined = [e["record"] for e in row["entries"].values()] + records
    if len(combined) > MAX_ROWS:
        errors.append("Workspace exceeds row limit")
    elif combined:
        errors.extend(checked_records(combined, row["catalog_version"]).errors)
    identity = uuid4().hex
    row["previews"][identity] = dict(
        preview_id=identity,
        file_sha256=preview.file_sha256,
        records=records,
        errors=list(dict.fromkeys(errors)),
        status="PREVIEW",
        rows=len(records),
        imported_revision=None,
    )
    return row


def import_preview(workspace: dict[str, Any], identity: str) -> dict[str, Any]:
    """锁内重新验证当前目标；一份PG提交写入全部记录及导入回执，失败全回滚。

    响应丢失后先读取预览的IMPORTED/revision；不重复导入，旧CAS不会覆盖新资料。
    """
    row = copy.deepcopy(workspace)
    preview = row["previews"].get(identity)
    if preview is None:
        raise AppError(ErrorCode.NOT_FOUND, "Corpus preview not found", 404)
    if preview["errors"] or preview["status"] != "PREVIEW":
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Preview is invalid or already imported", 400)
    combined = [e["record"] for e in row["entries"].values()] + preview["records"]
    require_records(combined, row["catalog_version"])
    for raw in preview["records"]:
        record = CorpusRecord.model_validate(raw)
        row["entries"][record.doc_id] = dict(
            record=raw,
            source=record.metadata.get("source", record.source_group),
            status="DRAFT",
            revision=1,
        )
    preview["status"] = "IMPORTED"
    preview["imported_revision"] = row["revision"] + 1
    # 解析记录已有唯一entries消费者；完成后预览仅保留hash/数量/回执。
    preview["records"] = []
    return row


def review_records(
    workspace: dict[str, Any],
    doc_ids: tuple[str, ...],
    approve: bool,
    reason: str,
    reviewer: dict[str, Any],
) -> dict[str, Any]:
    """人工审核绑定记录revision与完整内容hash；模型没有调用这条写入口的许可。"""
    row = copy.deepcopy(workspace)
    if len(set(doc_ids)) != len(doc_ids) or any(d not in row["entries"] for d in doc_ids):
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Unknown/duplicate review IDs", 400)
    require_records([e["record"] for e in row["entries"].values()], row["catalog_version"])
    event = dict(
        review_id=uuid4().hex,
        reviewer=reviewer,
        reason=reason,
        at=datetime.now(UTC).isoformat(),
        status="APPROVED" if approve else "REJECTED",
        records={
            d: dict(
                revision=row["entries"][d]["revision"],
                hash=digest({k: row["entries"][d][k] for k in ("record", "source", "revision")}),
            )
            for d in doc_ids
        },
    )
    row["reviews"].append(event)
    for doc_id in doc_ids:
        row["entries"][doc_id]["status"] = event["status"]
        row["entries"][doc_id]["review_id"] = event["review_id"]
    return row
