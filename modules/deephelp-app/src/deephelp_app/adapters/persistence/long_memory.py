"""借用原PG池持久化跨会话派生记忆；不拥有池的关闭权。

来源仍读原M08问题/run，写入仅涉及M46记忆与授权表。短事务锁定来源和记忆版本，
不跨模型/工具等待。首次明确操作才迁移，默认同会话主链不访问新表。
"""

import asyncio
from importlib.resources import files
from typing import Any

from deephelp_app.application.long_memory import changed_memory, source_hash
from deephelp_app.domain.cases import CLOSED
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_app.domain.models.conversation import Question
from deephelp_app.domain.models.long_memory import (
    LongMemory,
    MemoryChange,
    MemoryReference,
    MemoryScope,
)
from deephelp_app.domain.models.responses import ResponseEnvelope


class PostgresLongMemory:
    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self.ready = False
        self.migration_lock = asyncio.Lock()

    async def migrate(self) -> None:
        # 一个实例只有一个迁移者；锁由本适配器拥有，取消释放锁且不标记ready。
        async with self.migration_lock:
            if self.ready:
                return
            sql = (
                files("deephelp_app")
                .joinpath("resources/migrations/046_long_memory.sql")
                .read_text()
            )
            async with self.pool.acquire() as conn, conn.cursor() as cursor:
                for statement in sql.split(";"):
                    if statement.strip():
                        await cursor.execute(statement)
                await conn.commit()
            self.ready = True

    async def source(
        self,
        owner: VerifiedIdentity,
        session: str,
        question: str,
    ) -> tuple[Question, tuple[str, ...]] | None:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            # 同一RR快照读原问题及终态证据；没有跨会话拼接消息或信任索引文字。
            await cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            await cursor.execute(
                "SELECT body FROM dh_m08_questions WHERE tenant_id=%s AND user_id=%s "
                "AND session_id=%s AND question_id=%s",
                (owner.tenant_id, owner.user_id, session, question),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            current = Question.model_validate_json(row[0])
            if (
                current.identity != owner
                or current.session_id != session
                or current.question_id != question
            ):
                raise AppError(ErrorCode.FORBIDDEN, "Memory source scope differs", 403)
            await cursor.execute(
                "SELECT response FROM dh_m08_runs WHERE question_id=%s AND response IS NOT NULL "
                "ORDER BY finished_at DESC,run_id DESC LIMIT 1",
                (question,),
            )
            response = await cursor.fetchone()
            evidence: tuple[str, ...] = ()
            if response:
                receipt = ResponseEnvelope.model_validate_json(response[0])
                if receipt.question_id != question:
                    raise AppError(ErrorCode.VERSION_CONFLICT, "Source receipt differs", 409)
                evidence = tuple(r.evidence_id for r in receipt.evidence_refs)
            return current, evidence

    async def insert(self, memory: LongMemory) -> LongMemory:
        await self.migrate()
        key = (memory.owner.tenant_id, memory.owner.user_id, memory.memory_id)
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m08_questions WHERE tenant_id=%s AND user_id=%s "
                "AND session_id=%s AND question_id=%s FOR SHARE",
                (*key[:2], memory.source_session, memory.source_question),
            )
            source = await cursor.fetchone()
            if not source:
                raise AppError(ErrorCode.NOT_FOUND, "Memory source no longer exists", 404)
            question = Question.model_validate_json(source[0])
            if question.status not in CLOSED or source_hash(question) != memory.source_hash:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Memory source changed before save", 409)
            await cursor.execute(
                "INSERT INTO dh_m46_memories"
                "(tenant_id,user_id,memory_id,revision,source_session,body) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (*key, memory.revision, memory.source_session, memory.model_dump_json()),
            )
            await cursor.execute(
                "SELECT body FROM dh_m46_memories WHERE tenant_id=%s AND user_id=%s "
                "AND memory_id=%s",
                key,
            )
            row = await cursor.fetchone()
            assert row is not None
            stored = LongMemory.model_validate_json(row[0])
            # 重投返回原时间/版本，不覆写撤回/遗忘状态。
            if stored.source_hash != memory.source_hash:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Memory identity differs", 409)
            await cursor.execute(
                "INSERT INTO dh_m46_memory_versions(tenant_id,user_id,memory_id,revision,body) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (*key, stored.revision, stored.model_dump_json()),
            )
            await conn.commit()
            return stored

    async def get(self, owner: VerifiedIdentity, memory_id: str) -> LongMemory | None:
        await self.migrate()
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m46_memories WHERE tenant_id=%s AND user_id=%s "
                "AND memory_id=%s",
                (owner.tenant_id, owner.user_id, memory_id),
            )
            row = await cursor.fetchone()
            return LongMemory.model_validate_json(row[0]) if row else None

    async def change(
        self, owner: VerifiedIdentity, memory_id: str, command: MemoryChange
    ) -> LongMemory:
        """一条head行锁拥有更正时序，head与不可变版本同事务提交；取消/异常回滚。"""
        await self.migrate()
        key = (owner.tenant_id, owner.user_id, memory_id)
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m46_memories WHERE tenant_id=%s AND user_id=%s "
                "AND memory_id=%s FOR UPDATE",
                key,
            )
            row = await cursor.fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "Memory not found", 404)
            memory = changed_memory(LongMemory.model_validate_json(row[0]), command)
            await cursor.execute(
                "UPDATE dh_m46_memories SET revision=%s,body=%s WHERE tenant_id=%s AND user_id=%s "
                "AND memory_id=%s",
                (memory.revision, memory.model_dump_json(), *key),
            )
            await cursor.execute(
                "INSERT INTO dh_m46_memory_versions(tenant_id,user_id,memory_id,revision,body) "
                "VALUES(%s,%s,%s,%s,%s)",
                (*key, memory.revision, memory.model_dump_json()),
            )
            await conn.commit()
            return memory

    async def versions(self, owner: VerifiedIdentity, memory_id: str) -> tuple[LongMemory, ...]:
        await self.migrate()
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m46_memory_versions WHERE tenant_id=%s AND user_id=%s "
                "AND memory_id=%s ORDER BY revision DESC LIMIT 32",
                (owner.tenant_id, owner.user_id, memory_id),
            )
            return tuple(LongMemory.model_validate_json(r[0]) for r in await cursor.fetchall())

    async def authorized(self, scope: MemoryScope, reference: MemoryReference) -> bool:
        """一条PG语句读当前授权及head，作为派生上下文选择的最终版本检查。"""
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT m.body,s.body FROM dh_m46_memories m JOIN dh_m46_scopes s "
                "ON m.tenant_id=s.tenant_id AND m.user_id=s.user_id "
                "WHERE m.tenant_id=%s AND m.user_id=%s AND m.memory_id=%s "
                "AND s.target_session=%s",
                (
                    scope.owner.tenant_id,
                    scope.owner.user_id,
                    reference.memory_id,
                    scope.target_session,
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                return False
            memory = LongMemory.model_validate_json(row[0])
            current = MemoryScope.model_validate_json(row[1])
            return (
                current == scope
                and memory.revision == reference.revision
                and memory.state == "ACTIVE"
            )

    async def list(
        self, owner: VerifiedIdentity, sessions: tuple[str, ...]
    ) -> tuple[LongMemory, ...]:
        await self.migrate()
        if not sessions:
            return ()
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m46_memories WHERE tenant_id=%s AND user_id=%s "
                "AND source_session=ANY(%s) ORDER BY memory_id LIMIT 33",
                (owner.tenant_id, owner.user_id, list(sessions)),
            )
            rows = await cursor.fetchall()
            if len(rows) > 32:
                raise AppError(
                    ErrorCode.INVALID_ARGUMENT, "Narrow memory sources; candidate limit 32", 422
                )
            return tuple(LongMemory.model_validate_json(row[0]) for row in rows)

    async def scope(self, owner: VerifiedIdentity, session: str) -> MemoryScope:
        await self.migrate()
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m46_scopes WHERE tenant_id=%s AND user_id=%s "
                "AND target_session=%s",
                (owner.tenant_id, owner.user_id, session),
            )
            row = await cursor.fetchone()
            return (
                MemoryScope.model_validate_json(row[0])
                if row
                else MemoryScope(
                    owner=owner,
                    target_session=session,
                )
            )

    async def save_scope(self, scope: MemoryScope, expected_revision: int) -> MemoryScope:
        await self.migrate()
        key = (scope.owner.tenant_id, scope.owner.user_id, scope.target_session)
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            empty = MemoryScope(owner=scope.owner, target_session=scope.target_session)
            await cursor.execute(
                "INSERT INTO dh_m46_scopes(tenant_id,user_id,target_session,revision,body) "
                "VALUES(%s,%s,%s,0,%s) ON CONFLICT DO NOTHING",
                (*key, empty.model_dump_json()),
            )
            await cursor.execute(
                "SELECT revision FROM dh_m46_scopes WHERE tenant_id=%s AND user_id=%s "
                "AND target_session=%s FOR UPDATE",
                key,
            )
            row = await cursor.fetchone()
            if (
                row is None
                or row[0] != expected_revision
                or scope.revision != expected_revision + 1
            ):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Memory scope changed; reload", 409)
            await cursor.execute(
                "UPDATE dh_m46_scopes SET revision=%s,body=%s WHERE tenant_id=%s AND user_id=%s "
                "AND target_session=%s",
                (scope.revision, scope.model_dump_json(), *key),
            )
            await conn.commit()
            return scope
