"""在原PG run账本下保存专业任务进度，模型/工具等待期间不持数据库锁。

begin短锁原run，核对原消息hash与主体/问题版本，再唯一插入任务；重复RUNNING不重领。
write通过claim_token/state CAS保存公开决策、已验证观察和回执；完成后不可改写。
records只按原消息/问题双侧归属读取，未知状态不变成失败或允许盲目重放。
"""

import hashlib
from datetime import UTC, datetime
from importlib.resources import files
from typing import Any
from uuid import uuid4

from psycopg import Error as PostgreSQLError

from deephelp_app.adapters.persistence.ledger import payload_hash
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.collaboration import AgentTask, AgentTaskRecord
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_app.domain.models.conversation import RequestEnvelope
from deephelp_app.domain.models.tools import tool_parameters_hash


class PostgresAgentTasks:
    """借用原ledger连接池，不拥有新连接池、模型预算或独立业务事实源。"""

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def migrate(self) -> None:
        sql = (
            files("deephelp_app")
            .joinpath("resources/migrations/040_agent_tasks.sql")
            .read_text(encoding="utf-8")
        )
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            for statement in sql.split(";"):
                if statement.strip():
                    await cursor.execute(statement)
            await conn.commit()

    async def begin(
        self, task: AgentTask, context: RequestEnvelope, *, max_tasks: int
    ) -> AgentTaskRecord:
        """只领取原RUNNING消息的新子任务；已完成同任务只读回收，其余拒绝重领。"""
        if not 1 <= max_tasks <= 4:
            raise ValueError("Invalid task count limit")
        if task.identity != context.identity or task.session_id != context.session_id:
            raise AppError(ErrorCode.FORBIDDEN, "Delegation owner differs from message")
        try:
            async with self.pool.acquire() as conn, conn.cursor() as cursor:
                # 原run锁串行化同父任务计数/领取；外部调用在commit后发生。
                await cursor.execute(
                    "SELECT r.status,r.question_id,q.version,m.payload_hash FROM dh_m08_runs r "
                    "JOIN dh_m08_questions q ON q.question_id=r.question_id "
                    "JOIN dh_m08_messages m ON m.run_id=r.run_id "
                    "WHERE r.run_id=%s AND m.tenant_id=%s AND m.user_id=%s "
                    "AND q.tenant_id=%s AND q.user_id=%s AND q.session_id=%s "
                    "AND m.message_id=%s AND m.channel=%s FOR UPDATE OF r",
                    (
                        task.parent_run_id,
                        context.identity.tenant_id,
                        context.identity.user_id,
                        context.identity.tenant_id,
                        context.identity.user_id,
                        context.session_id,
                        context.message_id,
                        context.channel,
                    ),
                )
                parent = await cursor.fetchone()
                if (
                    not parent
                    or parent[1] != task.parent_question_id
                    or parent[3] != payload_hash(context)
                ):
                    raise AppError(
                        ErrorCode.FORBIDDEN, "Delegation is not owned by accepted message"
                    )
                if parent[0] != "RUNNING" or parent[2] != task.question_version:
                    raise AppError(ErrorCode.VERSION_CONFLICT, "Delegation parent has advanced")
                await cursor.execute(
                    "SELECT body FROM dh_m40_agent_tasks WHERE task_id=%s", (task.task_id,)
                )
                old = await cursor.fetchone()
                if old:
                    record = AgentTaskRecord.model_validate_json(old[0])
                    # 新ExecutionBudget的派发快照不能覆写原记录；其余绑定必须完全相同。
                    ignored = {"budget_at_dispatch"}
                    if record.task.model_dump(exclude=ignored) != task.model_dump(exclude=ignored):
                        raise AppError(ErrorCode.VERSION_CONFLICT, "Delegation binding changed")
                    if record.state != "COMPLETED":
                        raise AppError(
                            ErrorCode.OPERATION_UNKNOWN,
                            "Existing task requires read-only reconciliation",
                        )
                    await conn.rollback()
                    return record
                await cursor.execute(
                    "SELECT COUNT(*) FROM dh_m40_agent_tasks WHERE run_id=%s", (task.parent_run_id,)
                )
                if (await cursor.fetchone())[0] >= max_tasks:
                    raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Parent task count exhausted")
                query_hash = hashlib.sha256(
                    (
                        task.allowed_tools[0].value + ":" + tool_parameters_hash(task.parameters)
                    ).encode()
                ).hexdigest()
                await cursor.execute(
                    "SELECT 1 FROM dh_m40_agent_tasks WHERE run_id=%s AND query_hash=%s",
                    (task.parent_run_id, query_hash),
                )
                if await cursor.fetchone():
                    raise AppError(ErrorCode.INVALID_ARGUMENT, "Repeated delegated query blocked")
                record = AgentTaskRecord(
                    task=task,
                    claim_token=uuid4().hex,
                    state="RUNNING",
                    phase="delegate",
                    updated_at=datetime.now(UTC),
                )
                await cursor.execute(
                    "INSERT INTO dh_m40_agent_tasks"
                    "(task_id,run_id,node_id,query_hash,claim_token,state,body) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        task.task_id,
                        task.parent_run_id,
                        task.node_id,
                        query_hash,
                        record.claim_token,
                        record.state,
                        record.model_dump_json(),
                    ),
                )
                await conn.commit()
                return record
        except PostgreSQLError:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Task journal unavailable") from None

    async def write(self, record: AgentTaskRecord) -> None:
        """CAS推进当前拥有者；已关闭的回执不改写，竞争失败不得继续调用工具。"""
        try:
            await self._write(record)
        except PostgreSQLError:
            raise AppError(
                ErrorCode.UPSTREAM_UNAVAILABLE, "Task progress persistence unavailable"
            ) from None

    async def _write(self, record: AgentTaskRecord) -> None:
        # 持锁核对不可变绑定与单调进度，提交后释放；claim_token不能改变主体/事实。
        record = AgentTaskRecord.model_validate(record.model_dump())
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m40_agent_tasks WHERE task_id=%s FOR UPDATE",
                (record.task.task_id,),
            )
            row = await cursor.fetchone()
            if not row:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Task claim is absent")
            old = AgentTaskRecord.model_validate_json(row[0])
            phases = {"delegate": 0, "select_tool": 1, "tool": 2, "return_evidence": 3, "closed": 4}
            if (
                old.claim_token != record.claim_token
                or old.task != record.task
                or old.state not in {"RUNNING", "OBSERVED"}
                or phases[record.phase] < phases[old.phase]
                or record.decisions[: len(old.decisions)] != old.decisions
                or (old.observation is not None and record.observation != old.observation)
                or record.updated_at < old.updated_at
            ):
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Task claim or immutable progress differs"
                )
            await cursor.execute(
                "UPDATE dh_m40_agent_tasks SET state=%s,body=%s,updated_at=CURRENT_TIMESTAMP "
                "WHERE task_id=%s AND claim_token=%s AND state IN ('RUNNING','OBSERVED')",
                (record.state, record.model_dump_json(), record.task.task_id, record.claim_token),
            )
            if cursor.rowcount != 1:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Task claim or terminal state differs")
            await conn.commit()

    async def records(
        self, identity: VerifiedIdentity, session: str, run_id: str
    ) -> tuple[AgentTaskRecord, ...]:
        try:
            return await self._records(identity, session, run_id)
        except PostgreSQLError:
            raise AppError(
                ErrorCode.UPSTREAM_UNAVAILABLE, "Task progress read unavailable"
            ) from None

    async def _records(
        self, identity: VerifiedIdentity, session: str, run_id: str
    ) -> tuple[AgentTaskRecord, ...]:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT t.body FROM dh_m40_agent_tasks t JOIN dh_m08_runs r ON r.run_id=t.run_id "
                "JOIN dh_m08_messages m ON m.run_id=r.run_id "
                "JOIN dh_m08_questions q ON q.question_id=r.question_id "
                "WHERE m.tenant_id=%s AND m.user_id=%s AND q.tenant_id=%s AND q.user_id=%s "
                "AND q.session_id=%s AND r.run_id=%s ORDER BY t.updated_at,t.task_id LIMIT 4",
                (
                    identity.tenant_id,
                    identity.user_id,
                    identity.tenant_id,
                    identity.user_id,
                    session,
                    run_id,
                ),
            )
            rows = await cursor.fetchall()
            await conn.rollback()
        return tuple(AgentTaskRecord.model_validate_json(row[0]) for row in rows)
