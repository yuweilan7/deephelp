"""明确保留、允许来源和精确选择的唯一跨会话用例。

MemoryService借用本服务；它不扩大事件窗口，不改Question.entities，不重开旧问题。
每次选择回查原问题版本/hash及owner。PG或来源不可用不伪装成空历史。
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Protocol

from deephelp_app.domain.cases import CLOSED, summary
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, QuestionStatus, VerifiedIdentity
from deephelp_app.domain.models.conversation import Question
from deephelp_app.domain.models.entities import EntityName
from deephelp_app.domain.models.long_memory import (
    LongMemory,
    MemoryChange,
    MemoryDecision,
    MemoryReference,
    MemoryScope,
    MemorySelection,
    MemoryUse,
)


def source_hash(question: Question) -> str:
    """绑定原Question的完整规范内容；更新任何来源字段都需要重新保留。"""
    excluded = {"versions": {"memory"}} if question.versions.memory is None else None
    return hashlib.sha256(question.model_dump_json(exclude=excluded).encode()).hexdigest()


class LongMemoryStore(Protocol):
    async def source(
        self, owner: VerifiedIdentity, session: str, question: str
    ) -> tuple[Question, tuple[str, ...]] | None: ...
    async def insert(self, memory: LongMemory) -> LongMemory: ...
    async def get(self, owner: VerifiedIdentity, memory_id: str) -> LongMemory | None: ...
    async def list(
        self, owner: VerifiedIdentity, sessions: tuple[str, ...]
    ) -> tuple[LongMemory, ...]: ...
    async def scope(self, owner: VerifiedIdentity, session: str) -> MemoryScope: ...
    async def save_scope(self, scope: MemoryScope, expected_revision: int) -> MemoryScope: ...
    async def change(
        self, owner: VerifiedIdentity, memory_id: str, command: MemoryChange
    ) -> LongMemory: ...
    async def versions(self, owner: VerifiedIdentity, memory_id: str) -> tuple[LongMemory, ...]: ...
    async def authorized(self, scope: MemoryScope, reference: MemoryReference) -> bool: ...


class LongMemoryService:
    """只有可信owner能保留/选择自己的已结束问题；调用者拥有本次取消时序。"""

    def __init__(self, store: LongMemoryStore) -> None:
        self.store = store
        self.policy = json.loads(
            files("deephelp_app").joinpath("resources/memory-policy.json").read_text()
        )

    async def preserve(self, owner: VerifiedIdentity, session: str, question_id: str) -> LongMemory:
        source = await self.store.source(owner, session, question_id)
        if source is None:
            raise AppError(ErrorCode.NOT_FOUND, "Memory source not found", 404)
        question, evidence = source
        if (
            question.identity != owner
            or question.session_id != session
            or question.question_id != question_id
        ):
            raise AppError(ErrorCode.FORBIDDEN, "Memory source scope differs", 403)
        if question.status not in CLOSED or question.approval_operation_id:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Only ended non-approval sources", 409)
        for old in await self.store.list(owner, (session,)):
            if old.source_question == question_id and old.state in {"REVOKED", "FORGOTTEN"}:
                return old
        now, digest = datetime.now(UTC), source_hash(question)
        key = "|".join((owner.tenant_id, owner.user_id, session, question_id, digest))
        memory = LongMemory(
            memory_id=hashlib.sha256(key.encode()).hexdigest(),
            owner=owner,
            source_session=session,
            source_question=question_id,
            source_messages=question.member_message_ids,
            source_version=question.version,
            source_hash=digest,
            evidence_ids=evidence,
            text=summary(question).text,
            created_at=now,
            expires_at=now + timedelta(days=self.policy["default_valid_days"]),
            confidence="SOURCE_VERIFIED" if question.status == QuestionStatus.RESOLVED else "LOW",
        )
        return await self.store.insert(memory)

    async def change(
        self, owner: VerifiedIdentity, memory_id: str, command: MemoryChange
    ) -> LongMemory:
        """来源不被改写，CAS在仓储锁内执行；遗忘是不可复活的检索墓碑。"""
        return await self.store.change(owner, memory_id, command)

    async def detail(self, owner: VerifiedIdentity, memory_id: str) -> dict[str, object]:
        memory = await self.store.get(owner, memory_id)
        if memory is None:
            raise AppError(ErrorCode.NOT_FOUND, "Memory not found", 404)
        # 旧版本仅返回审计元数据。遗忘不删除原业务/审批记录，也不把旧文字再次交给检索。
        versions = await self.store.versions(owner, memory_id)
        return {
            "memory": memory.model_dump(mode="json"),
            "versions": [
                {
                    k: r.model_dump(mode="json")[k]
                    for k in (
                        "revision",
                        "state",
                        "change_action",
                        "edit_id",
                        "changed_at",
                        "source_session",
                        "source_question",
                        "source_version",
                        "source_hash",
                    )
                }
                for r in versions
            ],
            "model_calls": 0,
        }

    async def configure(
        self,
        owner: VerifiedIdentity,
        session: str,
        revision: int,
        enabled: bool,
        sources: tuple[str, ...],
    ) -> MemoryScope:
        scope = MemoryScope(
            owner=owner,
            target_session=session,
            revision=revision + 1,
            enabled=enabled,
            source_sessions=sources,
        )
        # 来源必须存在于同owner的已保留记录；外主体/伪造session不提供存在性信息。
        known = await self.store.list(owner, sources)
        if set(sources) - {r.source_session for r in known}:
            raise AppError(ErrorCode.NOT_FOUND, "Allowed memory sources not found", 404)
        return await self.store.save_scope(scope, revision)

    async def select(
        self,
        owner: VerifiedIdentity,
        session: str,
        use: MemoryUse | None,
        current: Question | None = None,
    ) -> MemorySelection:
        if use is None:
            return MemorySelection(target_session=session, status="DISABLED")
        scope = await self.store.scope(owner, session)
        results: list[MemoryDecision] = []
        used_bytes = 0
        for reference in use.references:
            reason = None
            memory = await self.store.get(owner, reference.memory_id)
            if not scope.enabled:
                reason = "scope_disabled"
            elif scope.revision != use.scope_revision:
                reason = "scope_changed"
            elif memory is None:
                reason = "source_unavailable"
            elif memory.owner != owner or memory.source_session not in scope.source_sessions:
                reason = "source_out_of_scope"
            elif memory.revision != reference.revision:
                reason = "memory_changed"
            elif memory.state != "ACTIVE":
                reason = "memory_" + memory.state.lower()
            elif memory.expires_at <= datetime.now(UTC):
                reason = "expired"
            elif memory.confidence == "LOW":
                reason = "low_confidence"
            else:
                source = await self.store.source(
                    owner, memory.source_session, memory.source_question
                )
                if source is None:
                    reason = "source_unavailable"
                elif source[0].identity != owner or source[0].session_id != memory.source_session:
                    reason = "source_out_of_scope"
                elif source[0].status not in CLOSED or source[0].approval_operation_id:
                    reason = "source_not_ended"
                elif (
                    source_hash(source[0]) != memory.source_hash or source[1] != memory.evidence_ids
                ):
                    reason = "source_changed"
                elif current is not None and memory.kind == "closed_summary":
                    reason = current_relevance(source[0], current)
                if (
                    not reason
                    and used_bytes + len(memory.text.encode()) > self.policy["context_bytes"]
                ):
                    reason = "context_limit"
                if not reason and not await self.store.authorized(scope, reference):
                    # 来源回查有await；最后单次权威读取是选择的线性化点，晚到撤回/范围改变不漏过。
                    # 此前已完成的原run是审计记录，不因以后遗忘而被改写。
                    reason = "authorization_changed"
            if reason:
                # 拒绝不回传越权或撤回内容；引用ID/原因足以解释原选择。
                results.append(
                    MemoryDecision(reference=reference, status="REJECTED", reason=reason)
                )
            else:
                assert memory is not None
                used_bytes += len(memory.text.encode())
                results.append(
                    MemoryDecision(
                        reference=reference,
                        status="ADOPTED",
                        reason="verified_explicit_reference",
                        memory=memory,
                    )
                )
        return MemorySelection(
            target_session=session,
            scope_revision=scope.revision,
            status="SELECTED" if any(r.status == "ADOPTED" for r in results) else "REJECTED",
            decisions=tuple(results),
            context_bytes=used_bytes,
        )

    async def candidates(self, owner: VerifiedIdentity, session: str) -> dict[str, object]:
        """浏览0模型；返回当前来源回查结果，是否实际采用仍由原run回执证明。"""
        scope = await self.store.scope(owner, session)
        records = await self.store.list(owner, scope.source_sessions) if scope.enabled else ()
        decisions: list[MemoryDecision] = []
        for memory in records:
            selection = await self.select(
                owner,
                session,
                MemoryUse(
                    scope_revision=scope.revision,
                    references=(
                        MemoryReference(memory_id=memory.memory_id, revision=memory.revision),
                    ),
                ),
            )
            decisions.extend(selection.decisions)
        return {
            "scope": scope.model_dump(mode="json"),
            "candidates": [r.model_dump(mode="json") for r in decisions],
            "model_calls": 0,
            "retrieval": "explicit PG sources; no cross-session vector search",
        }


def history_reference(selection: MemorySelection) -> str:
    """只渲染合法历史引用。金额/订单状态仍只在原fact_reply渲染当前事实。"""
    return "".join(
        f"历史参考（会话 {m.source_session}，问题 {m.source_question}，"
        f"源版本 {m.source_version}，记忆版本 {m.revision}，{m.confidence}）："
        f"{m.text}。历史仅供参考，当前结论以上述本次查询为准。"
        for r in selection.decisions
        if r.status == "ADOPTED" and (m := r.memory)
    )


def current_relevance(source: Question, current: Question) -> str | None:
    """业务摘要只对应当前已确认同一问题类型与槽位；不借历史填空或消除冲突。"""
    if current.conflicts and any(c.resolution == "unresolved" for c in current.conflicts):
        return "current_conflict"
    if current.unresolved_fields or not current.entities or current.active_intent is None:
        return "current_unconfirmed"
    if source.active_intent != current.active_intent:
        return "intent_mismatch"
    old = {e.name: e.value for e in source.entities}
    new = {e.name: e.value for e in current.entities}
    if not old:
        return "source_unclassified"
    if any(name not in new for name in old):
        return "current_unconfirmed"
    if any(new[name] != value for name, value in old.items()):
        return (
            "order_mismatch"
            if old.get(EntityName.ORDER_ID) != new.get(EntityName.ORDER_ID)
            else "slot_mismatch"
        )
    return None


def changed_memory(memory: LongMemory, command: MemoryChange) -> LongMemory:
    """锁内纯转换；更正只能更新主动保留的用户信息，不篡改来源摘要和工具事实。"""
    if memory.revision != command.expected_revision:
        raise AppError(ErrorCode.VERSION_CONFLICT, "Memory changed; reload", 409)
    if memory.state == "FORGOTTEN":
        raise AppError(ErrorCode.VERSION_CONFLICT, "Forgotten memory cannot be revived", 409)
    if command.action == "correct" and (memory.kind != "user_retained" or memory.state != "ACTIVE"):
        raise AppError(ErrorCode.VERSION_CONFLICT, "Correct an active retained note", 409)
    if command.action == "retain" and memory.state != "ACTIVE":
        raise AppError(ErrorCode.VERSION_CONFLICT, "Revoked memory cannot be revived", 409)
    now = datetime.now(UTC)
    update: dict[str, object] = {
        "revision": memory.revision + 1,
        "changed_at": now,
        "edit_id": command.edit_id,
        "change_action": command.action,
    }
    if command.action in {"retain", "correct"}:
        update.update(
            kind="user_retained",
            retained=True,
            text=command.text,
            confidence=command.confidence,
            expires_at=now + timedelta(days=command.valid_days),
        )
    else:
        update["state"] = "REVOKED" if command.action == "revoke" else "FORGOTTEN"
        if command.action == "forget":
            update["text"] = "[forgotten]"
    return LongMemory.model_validate(memory.model_copy(update=update).model_dump())
