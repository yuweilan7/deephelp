"""SOP作者草稿的文件事实源：owner隔离、revision CAS与不可变基准。

阅读顺序：validate_content只调用原GovernedSOP；SOPDraftStore保存可编辑中间图。
草稿不是运行快照。无效图也可保存供修正，但只有原契约通过的图才能预演/审核。
短同步writer不跨await；独占创建锁防止进程并发覆盖，replace使读者只见完整文件。
"""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from deephelp_app.application.sop_governance import GovernedSOP, SOPRegistry, digest
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity


def validate_content(content: dict[str, Any]) -> dict[str, Any]:
    """返回原契约的真实校验结果及字段位置，不执行模型、工具或客户端表达式。"""
    try:
        GovernedSOP.model_validate(content)
    except ValidationError as exc:
        return {
            "valid": False,
            "errors": [
                {"path": list(e["loc"]), "message": e["msg"]}
                for e in exc.errors(include_context=False, include_input=False, include_url=False)
            ],
        }
    return {"valid": True, "errors": []}


class SOPDraftStore:
    """一个草稿一个文件；基准和owner永不由更新正文替换。

    root由服务端catalog装配。客户端只持有随机ID，不接受路径；外主体统一404。
    锁内先回读真实revision再更新。无效内容保持可修正，不伪装成已发布版本。
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, draft_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", draft_id):
            raise AppError(ErrorCode.NOT_FOUND, "SOP draft not found", 404)
        return self.root / (draft_id + ".json")

    @contextmanager
    def writer(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / "writer.lock"
        try:
            handle = path.open("x", encoding="utf-8")
        except FileExistsError as exc:
            raise AppError(ErrorCode.VERSION_CONFLICT, "SOP draft writer is busy", 409) from exc
        try:
            yield
        finally:
            handle.close()
            path.unlink()

    def _write(self, value: dict[str, Any]) -> dict[str, Any]:
        content = value["content"]
        if len(json.dumps(content, ensure_ascii=False)) > 64000:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "SOP draft exceeds content limit", 400)
        value = {**value, "content_hash": digest(content), "validation": validate_content(content)}
        path = self._path(value["draft_id"])
        temporary = path.with_suffix(".pending")
        try:
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return value

    def create(self, owner: VerifiedIdentity, base: SOPRegistry, sop_id: str) -> dict[str, Any]:
        """从当前注册表复制单一SOP与完整基准；正式对象及其hash保持。"""
        definition = next((d for d in base.definitions if d.sop_id == sop_id), None)
        if definition is None:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Unknown published SOP", 400)
        with self.writer():
            return self._write(
                {
                    "format": "m43-sop-draft-v1",
                    "draft_id": uuid4().hex,
                    "owner": owner.model_dump(mode="json"),
                    "base_hash": base.snapshot_hash,
                    "base_registry": base.model_dump(mode="json"),
                    "revision": 1,
                    "status": "DRAFT",
                    "content": definition.model_dump(mode="json"),
                }
            )

    def get(self, draft_id: str, owner: VerifiedIdentity) -> dict[str, Any]:
        try:
            value: dict[str, Any] = json.loads(self._path(draft_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise AppError(ErrorCode.NOT_FOUND, "SOP draft not found", 404) from exc
        if value["owner"] != owner.model_dump(mode="json"):
            raise AppError(ErrorCode.NOT_FOUND, "SOP draft not found", 404)
        base = SOPRegistry.model_validate(value["base_registry"])
        if value["base_hash"] != base.snapshot_hash or value["content_hash"] != digest(
            value["content"]
        ):
            raise AppError(ErrorCode.VERSION_CONFLICT, "SOP draft integrity check failed", 409)
        return value

    def list(self, owner: VerifiedIdentity) -> list[dict[str, Any]]:
        return [
            self.get(p.stem, owner)
            for p in sorted(self.root.glob("*.json"))
            if json.loads(p.read_text(encoding="utf-8"))["owner"] == owner.model_dump(mode="json")
        ]

    def update(
        self, draft_id: str, owner: VerifiedIdentity, revision: int, content: dict[str, Any]
    ) -> dict[str, Any]:
        """旧revision或已审核草稿拒绝；保存后刷新必须读取返回的新revision。"""
        with self.writer():
            current = self.get(draft_id, owner)
            if current["revision"] != revision or current["status"] != "DRAFT":
                raise AppError(ErrorCode.VERSION_CONFLICT, "SOP draft revision/status changed", 409)
            base = SOPRegistry.model_validate(current["base_registry"])
            original = next(d for d in base.definitions if d.sop_id == current["content"]["sop_id"])
            # 图编辑不重新归属意图/槽位；工具映射仍由原schema和已确认实体决定。
            if any(
                content.get(k) != original.model_dump(mode="json")[k]
                for k in ("sop_id", "intent_code", "required_slots")
            ):
                raise AppError(ErrorCode.INVALID_ARGUMENT, "SOP identity and slots are fixed", 400)
            return self._write({**current, "revision": revision + 1, "content": content})
