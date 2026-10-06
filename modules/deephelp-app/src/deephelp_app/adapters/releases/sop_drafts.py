"""SOP作者草稿的文件事实源：owner隔离、revision CAS与不可变基准。

阅读顺序：validate_content只调用原GovernedSOP；SOPDraftStore保存可编辑中间图。
草稿不是运行快照。无效图也可保存供修正，但只有原契约通过的图才能预演/审核。
短同步writer不跨await；独占创建锁防止进程并发覆盖，replace使读者只见完整文件。
"""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from deephelp_app.adapters.mcp.business.mcp_protocol import canonical
from deephelp_app.application.sop_governance import GovernedSOP, RegistryStore, SOPRegistry, digest
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


def draft_registry(value: dict[str, Any]) -> SOPRegistry:
    """把合法草稿放入其原完整依赖闭包；构造候选，不触碰active指针。

    预演和审核共用同一个确定性候选hash。旧定义/工具schema/提示及目录从保存基准取得，
    只替换稳定ID对应的一个定义；再过完整SOPRegistry校验，不信保存的valid布尔值。
    """
    try:
        definition = GovernedSOP.model_validate(value["content"])
    except ValidationError:
        raise AppError(
            ErrorCode.INVALID_ARGUMENT, "Fix invalid graph before candidate use", 400
        ) from None
    base = SOPRegistry.model_validate(value["base_registry"])
    raw = base.model_dump(mode="json")
    raw["registry_version"] = "m43-candidate-" + digest(value["content"])[7:23]
    raw["definitions"] = [
        definition.model_dump(mode="json")
        if d.sop_id == definition.sop_id
        else d.model_dump(mode="json")
        for d in base.definitions
    ]
    return SOPRegistry.model_validate(raw)


def sop_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """有限图的字段/顺序差异，只读已验证契约，不评估业务条件或调用模型。"""
    result: list[dict[str, Any]] = []

    def visit(left: Any, right: Any, path: list[str | int]) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(left.keys() | right.keys()):
                visit(left.get(key), right.get(key), [*path, key])
        elif isinstance(left, list) and isinstance(right, list):
            for index in range(max(len(left), len(right))):
                visit(
                    left[index] if index < len(left) else None,
                    right[index] if index < len(right) else None,
                    [*path, index],
                )
        elif type(left) is not type(right) or left != right:
            result.append(dict(path=path, before=left, after=right))

    visit(before, after, [])
    return result


def read_sop_review(path: Path) -> tuple[SOPRegistry, dict[str, Any]]:
    """完整发布只读不可变审核原件；再核对完整基准/候选闭包和明确审核主体。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    registry = SOPRegistry.model_validate(value["registry"])
    base = SOPRegistry.model_validate(value["base_registry"])
    definition = next((d for d in registry.definitions if d.sop_id == value["sop_id"]), None)
    if definition is None:
        raise ValueError("SOP review must identify a known definition")
    expected = draft_registry(
        {"base_registry": value["base_registry"], "content": definition.model_dump(mode="json")}
    )
    if (
        value.get("format") != "m43-sop-review-v1"
        or value.get("status") != "APPROVED"
        or value.get("candidate_hash") != registry.snapshot_hash
        or value.get("base_hash") != base.snapshot_hash
        or value.get("content_hash") != digest(definition.model_dump(mode="json"))
        or registry != expected
        or definition.version == base.definition(definition.intent_code).version
        or not str(value.get("reason", "")).strip()
        or VerifiedIdentity.model_validate(value["reviewer"])
        != VerifiedIdentity.model_validate(value["owner"])
    ):
        raise ValueError("SOP release requires matching explicit approval and complete closure")
    return registry, value


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

    def review(
        self,
        draft_id: str,
        owner: VerifiedIdentity,
        revision: int,
        base: SOPRegistry,
        reason: str,
        approved: bool,
    ) -> dict[str, Any]:
        """锁内核对当前基准/CAS，冻结原件；只有候选索引变化，正式运行active不变。

        RegistryStore在隔离versions根保留版本，防两个草稿借同版本换字节。
        审核后草稿不可编辑；新修改须从当前正式基准新建草稿，旧审核/问题引用保留。
        """
        if not reason.strip() or len(reason) > 1000:
            raise AppError(
                ErrorCode.INVALID_ARGUMENT, "Explicit bounded SOP review reason required", 400
            )
        with self.writer():
            current = self.get(draft_id, owner)
            if (
                current["revision"] != revision
                or current["status"] != "DRAFT"
                or current["base_hash"] != base.snapshot_hash
            ):
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "SOP review revision/base/status changed", 409
                )
            registry = draft_registry(current)
            flow = registry.definition(current["content"]["intent_code"])
            if flow.version == base.definition(flow.intent_code).version:
                raise AppError(ErrorCode.INVALID_ARGUMENT, "SOP review requires a new version", 400)
            if approved:
                versions = RegistryStore(self.root / "versions")
                try:
                    versions.publish(base)
                    versions.publish(registry)
                except ValueError, FileExistsError:
                    raise AppError(
                        ErrorCode.VERSION_CONFLICT,
                        "SOP retained version conflicts; use a new version or inspect its writer",
                        409,
                    ) from None
            value = dict(
                format="m43-sop-review-v1",
                draft_id=draft_id,
                sop_id=flow.sop_id,
                owner=owner.model_dump(mode="json"),
                reviewer=owner.model_dump(mode="json"),
                reason=reason,
                reviewed_at=datetime.now(UTC).isoformat(),
                revision=revision + 1,
                status="APPROVED" if approved else "REJECTED",
                base_hash=current["base_hash"],
                base_registry=current["base_registry"],
                content_hash=current["content_hash"],
                candidate_hash=registry.snapshot_hash,
                registry=registry.model_dump(mode="json"),
            )
            fingerprint = digest(value)[7:]
            path = self.root / "reviews" / (fingerprint + ".json")
            path.parent.mkdir(exist_ok=True)
            with path.open("x", encoding="utf-8") as stream:
                stream.write(canonical(value).decode())
            return self._write(
                {
                    **current,
                    "revision": revision + 1,
                    "status": value["status"],
                    "candidate_hash": registry.snapshot_hash,
                    "review_digest": fingerprint,
                }
            )

    def approved(self, draft_id: str, owner: VerifiedIdentity) -> Path:
        """审核元数据和原件双向对照；客户端只提交ID，不能指定文件路径。"""
        draft = self.get(draft_id, owner)
        fingerprint = str(draft.get("review_digest", ""))
        if draft["status"] != "APPROVED" or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise AppError(ErrorCode.VERSION_CONFLICT, "SOP draft has no approved snapshot", 409)
        path = self.root / "reviews" / (fingerprint + ".json")
        try:
            registry, review = read_sop_review(path)
        except ValueError, KeyError, OSError, AppError:
            raise AppError(ErrorCode.VERSION_CONFLICT, "SOP review artifact invalid", 409) from None
        if (
            digest(review)[7:] != fingerprint
            or any(
                review[k] != draft[k]
                for k in (
                    "draft_id",
                    "revision",
                    "owner",
                    "base_hash",
                    "content_hash",
                    "candidate_hash",
                )
            )
            or registry != draft_registry(draft)
        ):
            raise AppError(ErrorCode.VERSION_CONFLICT, "SOP review bytes drifted", 409)
        return path
