"""语料作者离线实验复用纯状态变换，不创建第二存储/网络或声称PG持久证据。"""

import json
from typing import Any

from deephelp_app.application.corpus import read_corpus_bytes
from deephelp_app.application.corpus_authoring import (
    delete_record,
    empty_workspace,
    freeze_workspace,
    import_preview,
    review_records,
    stage_preview,
    version_diff,
)
from deephelp_app.domain.errors import AppError


async def corpus_authoring_demo() -> dict[str, Any]:
    """返回父标签拒绝、预览不写入、明确导入与人工审核的真实核心结果。"""
    raw: dict[str, Any] = dict(
        doc_id="course-corpus-order",
        content="请核对这张订单是否关联活动",
        intent_code="ORDER_ACTIVITY_QUERY",
        split="reference",
        source_group="course-order",
        variant_group="course-order",
        synthetic=True,
        registry_version="complaints-v3",
        metadata={"source": "独立合成学习资料"},
    )
    workspace = empty_workspace("complaints-v3")
    bad = {**raw, "intent_code": "SERVICE"}
    rejected = stage_preview(workspace, read_corpus_bytes(json.dumps(bad).encode(), ".jsonl"))
    try:
        import_preview(rejected, next(iter(rejected["previews"])))
    except AppError as exc:
        rejection = exc.code.value
    else:
        raise AssertionError("Parent label must not import")
    staged = stage_preview(workspace, read_corpus_bytes(json.dumps(raw).encode(), ".jsonl"))
    imported = import_preview(staged, next(iter(staged["previews"])))
    reviewed = review_records(
        imported,
        (raw["doc_id"],),
        True,
        "核对合法叶子与来源",
        {"tenant_id": "course", "user_id": "reader"},
    )
    first = freeze_workspace(reviewed, "course-v1", {"tenant_id": "course", "user_id": "reader"})
    # 删除工作区条目不影响原快照；新版本仍需至少一条资料，这里比较审核修改。
    revised = review_records(
        reviewed,
        (raw["doc_id"],),
        True,
        "复核来源独立性",
        {"tenant_id": "course", "user_id": "reader"},
    )
    second = freeze_workspace(revised, "course-v2", {"tenant_id": "course", "user_id": "reader"})
    removed = delete_record(reviewed, raw["doc_id"])
    return dict(
        version_diff_changed=len(version_diff(first, second)["changed"]),
        old_snapshot_rows=len(first["entries"]),
        deleted_workspace_rows=len(removed["entries"]),
        frozen_version=freeze_workspace(
            reviewed, "course-v1", {"tenant_id": "course", "user_id": "reader"}
        )["version"],
        invalid_parent=rejection,
        failed_import_rows=len(rejected["entries"]),
        preview_rows=len(staged["entries"]),
        imported_status=imported["entries"][raw["doc_id"]]["status"],
        approved_status=reviewed["entries"][raw["doc_id"]]["status"],
        model_calls=0,
        boundary="Pure data mechanism; no PG durability or index activation claim",
    )
