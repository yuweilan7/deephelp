"""显式离线记忆仓储缝隙，读取原MemoryCaseRepository；不证明PG持久协议。

课程与固定反例共享它，选择策略仍调用真正LongMemoryService。没有服务失败回退路径。
"""

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_app.domain.models.conversation import Question
from deephelp_app.domain.models.long_memory import LongMemory, MemoryScope
from deephelp_tools.learning.fakes.cases_fake import MemoryCaseRepository


class OfflineLongMemory:
    def __init__(self, cases: MemoryCaseRepository) -> None:
        self.cases = cases
        self.memories: dict[str, LongMemory] = {}
        self.scopes: dict[tuple[str, str, str], MemoryScope] = {}

    async def source(
        self,
        owner: VerifiedIdentity,
        session: str,
        question: str,
    ) -> tuple[Question, tuple[str, ...]] | None:
        current = await self.cases.question(owner, session, question)
        if current is None:
            return None
        responses = [
            r.response
            for _, r in self.cases.rows.values()
            if r.question.question_id == question and r.response is not None
        ]
        return current, tuple(
            e.evidence_id for e in responses[-1].evidence_refs
        ) if responses else ()

    async def insert(self, memory: LongMemory) -> LongMemory:
        return self.memories.setdefault(memory.memory_id, memory)

    async def get(self, owner: VerifiedIdentity, memory_id: str) -> LongMemory | None:
        row = self.memories.get(memory_id)
        return row if row and row.owner == owner else None

    async def list(
        self, owner: VerifiedIdentity, sessions: tuple[str, ...]
    ) -> tuple[LongMemory, ...]:
        return tuple(
            r for r in self.memories.values() if r.owner == owner and r.source_session in sessions
        )

    async def scope(self, owner: VerifiedIdentity, session: str) -> MemoryScope:
        return self.scopes.get(
            (owner.tenant_id, owner.user_id, session),
            MemoryScope(
                owner=owner,
                target_session=session,
            ),
        )

    async def save_scope(self, scope: MemoryScope, expected_revision: int) -> MemoryScope:
        old = await self.scope(scope.owner, scope.target_session)
        if old.revision != expected_revision:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Memory scope changed", 409)
        self.scopes[(scope.owner.tenant_id, scope.owner.user_id, scope.target_session)] = scope
        return scope
