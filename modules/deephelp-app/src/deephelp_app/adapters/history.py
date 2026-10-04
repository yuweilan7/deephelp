"""Read existing PostgreSQL facts, never debug logs or model calls."""

from datetime import UTC
from typing import Any

from psycopg import Error as PostgreSQLError

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.history import HistoryMessage, MessagePage, SessionPage, SessionSummary
from deephelp_app.domain.models import (
    ConverseInput,
    ErrorCode,
    Question,
    RequestEnvelope,
    ResponseEnvelope,
    VerifiedIdentity,
)


class PostgresHistory:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def read(self, sql: str, params: tuple[object, ...]) -> Any:
        try:
            async with self.pool.acquire() as conn:
                try:
                    async with conn.cursor() as cursor:
                        await cursor.execute(sql, params)
                        return await cursor.fetchall()
                finally:
                    await conn.rollback()
        except PostgreSQLError, OSError:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "持久历史暂不可用。", 503) from None

    async def sessions(self, identity: VerifiedIdentity, offset: int, limit: int) -> SessionPage:
        rows = await self.read(
            "SELECT q.session_id,COUNT(*),MAX(m.received_at) FROM dh_m08_messages m "
            "JOIN dh_m08_runs r ON r.run_id=m.run_id "
            "JOIN dh_m08_questions q ON q.question_id=r.question_id "
            "WHERE m.tenant_id=%s AND m.user_id=%s AND q.tenant_id=%s AND q.user_id=%s "
            "GROUP BY q.session_id ORDER BY MAX(m.received_at) DESC,q.session_id "
            "LIMIT %s OFFSET %s",
            (identity.tenant_id, identity.user_id) * 2 + (limit + 1, offset),
        )
        return SessionPage(
            items=tuple(
                SessionSummary(session_id=sid, message_count=count, updated_at=when.astimezone(UTC))
                for sid, count, when in rows[:limit]
            ),
            next_offset=offset + limit if len(rows) > limit else None,
        )

    async def messages(
        self, identity: VerifiedIdentity, session: str, offset: int, limit: int
    ) -> MessagePage:
        rows = await self.read(
            "SELECT m.body,r.run_id,r.status,q.body,r.response FROM dh_m08_messages m "
            "JOIN dh_m08_runs r ON r.run_id=m.run_id "
            "JOIN dh_m08_questions q ON q.question_id=r.question_id "
            "WHERE m.tenant_id=%s AND m.user_id=%s AND q.tenant_id=%s AND q.user_id=%s "
            "AND q.session_id=%s ORDER BY m.received_at,m.run_id LIMIT %s OFFSET %s",
            (identity.tenant_id, identity.user_id) * 2 + (session, limit + 1, offset),
        )
        if not rows and offset == 0:
            raise AppError(ErrorCode.NOT_FOUND, "会话不存在或无权读取。", 404)
        items = []
        for raw, run_id, status, question, response in rows[:limit]:
            stored = RequestEnvelope.model_validate_json(raw)
            items.append(
                HistoryMessage(
                    input=ConverseInput.model_validate(
                        stored.model_dump(include=set(ConverseInput.model_fields))
                    ),
                    run_id=run_id,
                    status=status,
                    question=Question.model_validate_json(question),
                    response=ResponseEnvelope.model_validate_json(response) if response else None,
                )
            )
        return MessagePage(
            items=tuple(items), next_offset=offset + limit if len(rows) > limit else None
        )
