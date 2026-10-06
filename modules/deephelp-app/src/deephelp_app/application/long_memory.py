"""明确保留、允许来源和精确选择的唯一跨会话用例。

MemoryService借用本服务；它不扩大事件窗口，不改Question.entities，不重开旧问题。
每次选择回查原问题版本/hash及owner。PG或来源不可用不伪装成空历史。
"""

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Protocol

from deephelp_app.domain.cases import CLOSED, summary
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_app.domain.models.conversation import Question
from deephelp_app.domain.models.long_memory import (
    LongMemory,
    MemoryDecision,
    MemoryReference,
    MemoryScope,
    MemorySelection,
    MemoryUse,
)


def source_hash(question: Question) -> str:
    """绑定原Question的完整规范内容；更新任何来源字段都需要重新保留。"""
    return hashlib.sha256(question.model_dump_json().encode()).hexdigest()


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


class LongMemoryService:
    """只有可信owner能保留/选择自己的已结束问题；调用者拥有本次取消时序。"""

    def __init__(self, store: LongMemoryStore) -> None:
        self.store = store

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
            expires_at=now + timedelta(days=30),
        )
        return await self.store.insert(memory)

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
                elif used_bytes + len(memory.text.encode()) > 8192:
                    reason = "context_limit"
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
        f"源版本 {m.source_version}）："
        f"{m.text}。历史仅供参考，当前结论以上述本次查询为准。"
        for r in selection.decisions
        if r.status == "ADOPTED" and (m := r.memory)
    )
