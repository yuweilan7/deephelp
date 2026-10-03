"""MySQL question facts and transactional outbox. Remote projections never hold row locks."""

import json
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from typing import Any
from uuid import uuid4

from deephelp_app.domain.models import (
    CaseSummary,
    ErrorCode,
    LifecycleCommand,
    MemoryMessage,
    MemoryWindow,
    Question,
    QuestionStatus,
    RequestEnvelope,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError
from deephelp_app.ledger import MySQLLedger, Receipt

OPEN = {QuestionStatus.ACTIVE, QuestionStatus.WAITING_SLOT}
CLOSED = {QuestionStatus.RESOLVED, QuestionStatus.HANDED_OFF, QuestionStatus.CANCELLED}


def summary(question: Question) -> CaseSummary:
    # Derived, deterministic, cited state; no model prose or fabricated conclusion.
    slots = "; ".join(f"{e.name.value}={e.value}" for e in question.entities)
    return CaseSummary(
        question_id=question.question_id,
        version=question.version,
        status=question.status,
        text=(
            f"{question.status}; {question.event_summary}; {slots}"
            if question.event_summary
            else f"{question.active_intent or 'UNCLASSIFIED'}; {question.status}; {slots}"
        )[:2000],
        message_ids=question.member_message_ids,
    )


def validate_transition(question: Question, command: LifecycleCommand) -> None:
    if question.version != command.expected_version:
        raise AppError(ErrorCode.VERSION_CONFLICT, "Question version changed", 409)
    expected = {
        QuestionStatus.RESOLVED: {"user_confirmation", "process_result"},
        QuestionStatus.CANCELLED: {"operator_cancel"},
        QuestionStatus.HANDED_OFF: {"operator_handoff"},
        QuestionStatus.WAITING_SLOT: {"slot_check"},
        QuestionStatus.ACTIVE: {"explicit_reopen"} if question.status in CLOSED else {"slot_check"},
    }
    if (
        command.target == question.status
        or command.evidence_source not in expected.get(command.target, set())
        or (question.status in CLOSED and command.target != QuestionStatus.ACTIVE)
    ):
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Lifecycle transition/evidence rejected", 422)


def bounded_history(
    rows: list[MemoryMessage], *, count: int, token_limit: int
) -> tuple[tuple[MemoryMessage, ...], bool]:
    """UTF-8 byte count is a conservative token bound; never truncate an identifier silently."""
    selected: list[MemoryMessage] = []
    remaining = token_limit
    trimmed = len(rows) > count
    for row in rows[:count]:
        size = len(row.text.encode("utf-8"))
        if remaining <= 0:
            trimmed = True
            break
        if size > remaining:
            text = row.text.encode("utf-8")[:remaining].decode("utf-8", errors="ignore").rstrip()
            trimmed = True
            if text:
                selected.append(row.model_copy(update={"text": text, "truncated": True}))
            break
        selected.append(row)
        remaining -= size
    return tuple(reversed(selected)), trimmed


class MySQLCaseRepository(MySQLLedger):
    supports_continuation = True

    async def migrate(self) -> None:
        await super().migrate()
        sql = (
            files("deephelp_app").joinpath("migrations/010_memory.sql").read_text(encoding="utf-8")
        )
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            for statement in sql.split(";"):
                if statement.strip():
                    await cursor.execute(statement)
            await cursor.execute("SHOW INDEX FROM dh_m08_runs WHERE Key_name='uq_m08_run_question'")
            if await cursor.fetchone():
                await cursor.execute(
                    "ALTER TABLE dh_m08_runs DROP INDEX uq_m08_run_question, ADD INDEX "
                    "ix_m10_run_question (question_id,status)"
                )
            # Incremental M08 backfill preserves bodies/receipts and makes old facts rebuildable.
            while True:
                await cursor.execute(
                    "SELECT m.body,q.question_id FROM dh_m08_messages m "
                    "JOIN dh_m08_runs r ON r.run_id=m.run_id "
                    "JOIN dh_m08_questions q ON q.question_id=r.question_id "
                    "LEFT JOIN dh_m10_members n ON n.tenant_id=m.tenant_id "
                    "AND n.user_id=m.user_id AND n.channel=m.channel AND n.message_id=m.message_id "
                    "WHERE n.message_id IS NULL LIMIT 500"
                )
                rows = await cursor.fetchall()
                if not rows:
                    break
                values = []
                for body, qid in rows:
                    request = RequestEnvelope.model_validate_json(body)
                    values.append(
                        (
                            request.identity.tenant_id,
                            request.identity.user_id,
                            request.session_id,
                            request.channel,
                            request.message_id,
                            qid,
                            request.occurred_at.astimezone(UTC).replace(tzinfo=None),
                        )
                    )
                await cursor.executemany(
                    "INSERT INTO dh_m10_members (tenant_id,user_id,session_id,channel,message_id,"
                    "question_id,occurred_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    values,
                )
            await cursor.execute(
                "INSERT INTO dh_m10_sessions SELECT q.tenant_id,q.user_id,q.session_id,1 FROM "
                "dh_m08_questions q LEFT JOIN dh_m10_sessions s ON s.tenant_id=q.tenant_id "
                "AND s.user_id=q.user_id AND s.session_id=q.session_id WHERE s.session_id IS NULL "
                "GROUP BY q.tenant_id,q.user_id,q.session_id"
            )
            await cursor.execute(
                "INSERT INTO dh_m10_outbox (question_id,version,body,reason) SELECT "
                "q.question_id,q.version,q.body,JSON_OBJECT('kind','m08_backfill') "
                "FROM dh_m08_questions q LEFT JOIN dh_m10_outbox e ON e.question_id=q.question_id "
                "AND e.version=q.version WHERE e.event_id IS NULL"
            )
            await conn.commit()

    async def lock_session(self, cursor: Any, identity: VerifiedIdentity, session: str) -> None:
        key = (identity.tenant_id, identity.user_id, session)
        await cursor.execute(
            "INSERT INTO dh_m10_sessions (tenant_id,user_id,session_id) VALUES (%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE generation=generation",
            key,
        )
        await cursor.execute(
            "SELECT generation FROM dh_m10_sessions WHERE tenant_id=%s AND user_id=%s AND "
            "session_id=%s FOR UPDATE",
            key,
        )
        await cursor.fetchone()

    async def busy(self, cursor: Any, qid: str) -> bool:
        await cursor.execute(
            "SELECT 1 FROM dh_m08_runs WHERE question_id=%s AND status='RUNNING' LIMIT 1", (qid,)
        )
        return await cursor.fetchone() is not None

    async def prepare_question(
        self, cursor: Any, request: RequestEnvelope, fresh: Receipt
    ) -> Receipt:
        await self.lock_session(cursor, request.identity, request.session_id)
        if not request.question_hint:
            return await super().prepare_question(cursor, request, fresh)
        await cursor.execute(
            "SELECT body FROM dh_m08_questions WHERE question_id=%s AND tenant_id=%s AND "
            "user_id=%s AND session_id=%s FOR UPDATE",
            (
                request.question_hint,
                request.identity.tenant_id,
                request.identity.user_id,
                request.session_id,
            ),
        )
        row = await cursor.fetchone()
        if not row:
            raise AppError(ErrorCode.FORBIDDEN, "Object access denied", 403)
        q = Question.model_validate_json(row[0])
        if q.status not in OPEN:
            raise AppError(
                ErrorCode.INVALID_ARGUMENT, "Closed question requires explicit reopen", 409
            )
        if (
            request.expected_question_version is not None
            and q.version != request.expected_question_version
        ):
            raise AppError(ErrorCode.VERSION_CONFLICT, "Question version changed", 409)
        if await self.busy(cursor, q.question_id):
            raise AppError(ErrorCode.VERSION_CONFLICT, "Question has an unfinished run", 409)
        await cursor.execute(
            "SELECT MAX(occurred_at) FROM dh_m10_members WHERE question_id=%s", (q.question_id,)
        )
        latest = (await cursor.fetchone())[0]
        if latest and request.occurred_at.astimezone(UTC).replace(tzinfo=None) < latest:
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Out-of-order continuation requires review", 409
            )
        q = Question.model_validate(
            q.model_copy(
                update={
                    "version": q.version + 1,
                    "member_message_ids": (*q.member_message_ids, request.message_id),
                    "updated_at": datetime.now(UTC),
                }
            ).model_dump()
        )
        await cursor.execute(
            "UPDATE dh_m08_questions SET version=%s,body=%s WHERE question_id=%s",
            (q.version, q.model_dump_json(), q.question_id),
        )
        return Receipt(True, fresh.run_id, q)

    async def accepted(self, cursor: Any, request: RequestEnvelope, receipt: Receipt) -> None:
        await cursor.execute(
            "INSERT INTO dh_m10_members "
            "(tenant_id,user_id,session_id,channel,message_id,question_id,occurred_at) VALUES "
            "(%s,%s,%s,%s,%s,%s,%s)",
            (
                request.identity.tenant_id,
                request.identity.user_id,
                request.session_id,
                request.channel,
                request.message_id,
                receipt.question.question_id,
                request.occurred_at.astimezone(UTC).replace(tzinfo=None),
            ),
        )
        await self.changed(cursor, receipt.question, "message_accepted")

    async def before_finish(
        self, cursor: Any, receipt: Receipt, question: Question, response: Any
    ) -> None:
        await self.lock_session(cursor, question.identity, question.session_id)

    async def changed(self, cursor: Any, question: Question, reason: str) -> None:
        await self.event(cursor, question, {"kind": reason})

    async def event(self, cursor: Any, question: Question, reason: dict[str, Any]) -> None:
        await cursor.execute(
            "UPDATE dh_m10_sessions SET generation=generation+1 WHERE tenant_id=%s AND "
            "user_id=%s AND session_id=%s",
            (question.identity.tenant_id, question.identity.user_id, question.session_id),
        )
        await cursor.execute(
            "INSERT INTO dh_m10_outbox (question_id,version,body,reason) VALUES (%s,%s,%s,%s)",
            (
                question.question_id,
                question.version,
                question.model_dump_json(),
                json.dumps(reason, ensure_ascii=False),
            ),
        )

    async def transition(
        self, identity: VerifiedIdentity, qid: str, command: LifecycleCommand
    ) -> Question:
        async with self.pool.acquire() as conn:
            try:
                async with conn.cursor() as cursor:
                    await self.lock_session(cursor, identity, command.session_id)
                    await cursor.execute(
                        "SELECT body FROM dh_m08_questions WHERE question_id=%s AND "
                        "tenant_id=%s AND user_id=%s AND session_id=%s FOR UPDATE",
                        (qid, identity.tenant_id, identity.user_id, command.session_id),
                    )
                    row = await cursor.fetchone()
                    if not row:
                        raise AppError(ErrorCode.FORBIDDEN, "Object access denied", 403)
                    q = Question.model_validate_json(row[0])
                    validate_transition(q, command)
                    if await self.busy(cursor, qid):
                        raise AppError(
                            ErrorCode.VERSION_CONFLICT,
                            "Unfinished run cannot be resumed or closed",
                            409,
                        )
                    q = Question.model_validate(
                        q.model_copy(
                            update={
                                "version": q.version + 1,
                                "status": command.target,
                                "updated_at": datetime.now(UTC),
                            }
                        ).model_dump()
                    )
                    await cursor.execute(
                        "UPDATE dh_m08_questions SET status=%s,version=%s,body=%s WHERE "
                        "question_id=%s",
                        (q.status, q.version, q.model_dump_json(), qid),
                    )
                    await self.event(
                        cursor, q, {"kind": "lifecycle", **command.model_dump(mode="json")}
                    )
                await conn.commit()
                return q
            except AppError:
                await conn.rollback()
                raise
            except BaseException:
                conn.close()
                raise

    async def snapshot(
        self,
        identity: VerifiedIdentity,
        session: str,
        *,
        count: int = 30,
        token_limit: int = 8192,
        age_seconds: int = 86400,
        case_limit: int = 32,
    ) -> MemoryWindow:
        if min(count, token_limit, age_seconds, case_limit) <= 0:
            raise ValueError("Positive memory bounds required")
        key = (identity.tenant_id, identity.user_id, session)
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                await cursor.execute(
                    "SELECT generation FROM dh_m10_sessions WHERE tenant_id=%s AND user_id=%s "
                    "AND session_id=%s",
                    key,
                )
                row = await cursor.fetchone()
                generation = int(row[0]) if row else 0
                await cursor.execute(
                    "SELECT body FROM dh_m08_questions WHERE tenant_id=%s AND user_id=%s AND "
                    "session_id=%s AND status IN ('ACTIVE','WAITING_SLOT') ORDER BY "
                    "JSON_UNQUOTE(JSON_EXTRACT(body,'$.updated_at')) DESC,question_id LIMIT %s",
                    (*key, case_limit + 1),
                )
                active = [Question.model_validate_json(row[0]) for row in await cursor.fetchall()]
                await cursor.execute(
                    "SELECT body FROM dh_m08_questions WHERE tenant_id=%s AND user_id=%s AND "
                    "session_id=%s AND status IN ('RESOLVED','HANDED_OFF','CANCELLED') ORDER "
                    "BY version DESC,question_id LIMIT %s",
                    (*key, case_limit + 1),
                )
                closed = [Question.model_validate_json(row[0]) for row in await cursor.fetchall()]
                cutoff = (datetime.now(UTC) - timedelta(seconds=age_seconds)).replace(tzinfo=None)
                await cursor.execute(
                    "SELECT "
                    "n.channel,n.message_id,n.question_id,n.occurred_at,JSON_UNQUOTE(JSON_EXTRA"
                    "CT(m.body,'$.raw_text')) FROM dh_m10_members n JOIN dh_m08_messages m ON "
                    "m.tenant_id=n.tenant_id AND m.user_id=n.user_id AND m.channel=n.channel "
                    "AND m.message_id=n.message_id WHERE n.tenant_id=%s AND n.user_id=%s AND "
                    "n.session_id=%s AND n.occurred_at>=%s ORDER BY n.occurred_at "
                    "DESC,n.channel,n.message_id DESC LIMIT %s",
                    (*key, cutoff, count + 1),
                )
                messages = [
                    MemoryMessage(
                        channel=r[0],
                        message_id=r[1],
                        question_id=r[2],
                        occurred_at=r[3].replace(tzinfo=UTC),
                        text=r[4],
                    )
                    for r in await cursor.fetchall()
                ]
                history, trimmed = bounded_history(messages, count=count, token_limit=token_limit)
                return MemoryWindow(
                    history=history,
                    active_questions=tuple(active[:case_limit]),
                    closed_summaries=tuple(summary(q) for q in closed[:case_limit]),
                    generation=generation,
                    trimmed=trimmed or len(active) > case_limit or len(closed) > case_limit,
                )
            finally:
                await conn.rollback()

    async def latest_questions(
        self, identity: VerifiedIdentity, session: str, *, limit: int = 100
    ) -> list[Question]:
        if limit <= 0:
            raise ValueError("Positive rebuild limit required")
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m08_questions WHERE tenant_id=%s AND user_id=%s "
                "AND session_id=%s ORDER BY question_id LIMIT %s",
                (identity.tenant_id, identity.user_id, session, limit),
            )
            rows = await cursor.fetchall()
            await conn.rollback()
        return [Question.model_validate_json(row[0]) for row in rows]

    async def claim_event(
        self, *, event_id: int | None = None, max_attempts: int = 5
    ) -> tuple[int, str, Question] | None:
        token = uuid4().hex
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT event_id,body FROM dh_m10_outbox WHERE done=FALSE AND attempts<%s AND "
                "(lease_until IS NULL OR lease_until<CURRENT_TIMESTAMP(6))"
                + (" AND event_id=%s" if event_id is not None else "")
                + " ORDER BY event_id LIMIT 1 FOR UPDATE SKIP LOCKED",
                (max_attempts, event_id) if event_id is not None else (max_attempts,),
            )
            row = await cursor.fetchone()
            if row:
                await cursor.execute(
                    "UPDATE dh_m10_outbox SET "
                    "attempts=attempts+1,lease_token=%s,lease_until=DATE_ADD(CURRENT_TIMESTAMP("
                    "6),INTERVAL 120 SECOND) WHERE event_id=%s",
                    (token, row[0]),
                )
            await conn.commit()
        return (int(row[0]), token, Question.model_validate_json(row[1])) if row else None

    async def acknowledge(self, event_id: int, token: str, *, error: str | None = None) -> bool:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE dh_m10_outbox SET "
                "done=%s,last_error=%s,lease_token=NULL,lease_until=NULL WHERE event_id=%s AND "
                "lease_token=%s AND lease_until>CURRENT_TIMESTAMP(6)",
                (error is None, error, event_id, token),
            )
            owned = bool(cursor.rowcount == 1)
            await conn.commit()
            return owned
