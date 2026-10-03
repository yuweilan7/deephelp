"""Durable message acceptance. RUNNING receipts are never automatically reclaimed."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, Self
from uuid import uuid4

import aiomysql  # type: ignore[import-untyped]
from pymysql.err import IntegrityError, MySQLError  # type: ignore[import-untyped]

from deephelp_app.domain.models import (
    ErrorCode,
    Question,
    QuestionStatus,
    RequestEnvelope,
    ResponseEnvelope,
    RunStatus,
    VerifiedIdentity,
    VersionManifest,
)
from deephelp_app.errors import AppError
from deephelp_app.milvus_dense import local_connection


def payload_hash(request: RequestEnvelope) -> str:
    payload = request.model_dump(
        mode="json",
        include={
            "session_id",
            "raw_text",
            "occurred_at",
            "question_hint",
            "schema_version",
            "expected_question_version",
        },
    )
    # Preserve the hash of pre-M10 messages that did not provide a version.
    if payload.get("expected_question_version") is None:
        payload.pop("expected_question_version", None)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def message_key(request: RequestEnvelope) -> tuple[str, str, str, str]:
    return (
        request.identity.tenant_id,
        request.identity.user_id,
        request.channel,
        request.message_id,
    )


@dataclass(frozen=True)
class Receipt:
    acquired: bool
    run_id: str
    question: Question
    response: ResponseEnvelope | None = None


def new_receipt(request: RequestEnvelope) -> Receipt:
    now = datetime.now(UTC)
    return Receipt(
        True,
        uuid4().hex,
        Question(
            question_id=uuid4().hex,
            identity=request.identity,
            session_id=request.session_id,
            status=QuestionStatus.ACTIVE,
            version=1,
            member_message_ids=(request.message_id,),
            versions=VersionManifest(),
            created_at=now,
            updated_at=now,
        ),
    )


class MessageLedger(Protocol):
    async def accept(self, request: RequestEnvelope) -> Receipt: ...
    async def finish(
        self, receipt: Receipt, question: Question, response: ResponseEnvelope
    ) -> None: ...
    async def question(
        self, identity: VerifiedIdentity, session: str, question_id: str
    ) -> Question | None: ...


class MemoryLedger:
    """Offline adapter with the same claim/terminal semantics; never durable acceptance."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str, str], tuple[str, Receipt]] = {}
        self.questions: dict[str, Question] = {}

    async def lookup(self, request: RequestEnvelope) -> Receipt | None:
        row = self.rows.get(message_key(request))
        if row is None:
            return None
        digest, receipt = row
        if digest != payload_hash(request):
            raise AppError(ErrorCode.IDEMPOTENCY_CONFLICT, "Message payload conflicts")
        return Receipt(False, receipt.run_id, receipt.question, receipt.response)

    async def accept(
        self, request: RequestEnvelope, *, attribution: tuple[str, int] | None = None
    ) -> Receipt:
        if attribution:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Base ledger cannot attach events")
        key, digest = message_key(request), payload_hash(request)
        if key in self.rows:
            original_hash, receipt = self.rows[key]
            if original_hash != digest:
                raise AppError(ErrorCode.IDEMPOTENCY_CONFLICT, "Message payload conflicts")
            return Receipt(False, receipt.run_id, receipt.question, receipt.response)
        receipt = new_receipt(request)
        self.rows[key] = (digest, receipt)
        self.questions[receipt.question.question_id] = receipt.question
        return receipt

    async def finish(
        self, receipt: Receipt, question: Question, response: ResponseEnvelope
    ) -> None:
        for key, (digest, stored) in self.rows.items():
            if stored.run_id == receipt.run_id:
                if stored.response is not None:
                    raise AppError(ErrorCode.VERSION_CONFLICT, "Run already finalized")
                validate_terminal(receipt, question, response)
                self.rows[key] = (digest, Receipt(False, receipt.run_id, question, response))
                self.questions[question.question_id] = question
                return
        raise AppError(ErrorCode.NOT_FOUND, "Run not found")

    async def question(
        self, identity: VerifiedIdentity, session: str, question_id: str
    ) -> Question | None:
        row = self.questions.get(question_id)
        return row if row and row.identity == identity and row.session_id == session else None


def validate_terminal(receipt: Receipt, question: Question, response: ResponseEnvelope) -> None:
    if (
        response.run_id != receipt.run_id
        or response.question_id != question.question_id
        or question.question_id != receipt.question.question_id
        or question.identity != receipt.question.identity
        or question.session_id != receipt.question.session_id
        or question.version != receipt.question.version + 1
        or response.question_status != question.status
        or response.run_status
        not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.WAITING_APPROVAL,
        }
    ):
        raise AppError(ErrorCode.VERSION_CONFLICT, "Terminal ledger binding differs")


class MySQLLedger:
    supports_continuation = False

    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @classmethod
    async def open(cls, root: Path) -> Self:
        c = local_connection(root)
        try:
            pool = await aiomysql.create_pool(
                host=c["MYSQL_HOST"],
                port=int(c["MYSQL_PORT"]),
                user=c["MYSQL_USER"],
                password=c["MYSQL_PASSWORD"],
                db=c["MYSQL_DATABASE"],
                charset="utf8mb4",
                autocommit=False,
                minsize=1,
                maxsize=4,
                pool_recycle=300,
                connect_timeout=5,
            )
            return cls(pool)
        except MySQLError, OSError:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "MySQL connection unavailable") from None

    async def aclose(self) -> None:
        self.pool.close()
        try:
            async with asyncio.timeout(5):
                await self.pool.wait_closed()
        except TimeoutError:
            self.pool.terminate()
            async with asyncio.timeout(5):
                await self.pool.wait_closed()

    async def migrate(self) -> None:
        from importlib.resources import files

        sql = files("deephelp_app").joinpath("migrations/008_mvp.sql").read_text(encoding="utf-8")
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            for statement in sql.split(";"):
                if statement.strip():
                    await cursor.execute(statement)
            await conn.commit()

    async def lookup(self, request: RequestEnvelope) -> Receipt | None:
        try:
            async with self.pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT m.payload_hash,r.run_id,q.body,r.response FROM dh_m08_messages m "
                    "JOIN dh_m08_runs r ON r.run_id=m.run_id "
                    "JOIN dh_m08_questions q ON q.question_id=r.question_id "
                    "WHERE m.tenant_id=%s AND m.user_id=%s AND m.channel=%s AND m.message_id=%s",
                    message_key(request),
                )
                row = await cursor.fetchone()
                await conn.rollback()
            if not row:
                return None
            if row[0] != payload_hash(request):
                raise AppError(ErrorCode.IDEMPOTENCY_CONFLICT, "Message payload conflicts")
            return Receipt(
                False,
                row[1],
                Question.model_validate_json(row[2]),
                ResponseEnvelope.model_validate_json(row[3]) if row[3] else None,
            )
        except MySQLError, OSError:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "MySQL lookup unavailable") from None

    async def accept(
        self, request: RequestEnvelope, *, attribution: tuple[str, int] | None = None
    ) -> Receipt:
        fresh = new_receipt(request)
        if attribution and not self.supports_continuation:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Base ledger cannot attach events")
        if attribution and request.question_hint not in {None, attribution[0]}:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Explicit hint conflicts with attribution")
        if attribution and request.expected_question_version not in {None, attribution[1]}:
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Explicit version conflicts with attribution"
            )
        key, digest = message_key(request), payload_hash(request)
        try:
            async with self.pool.acquire() as conn:
                try:
                    async with conn.cursor() as cursor:
                        await cursor.execute(
                            "INSERT INTO dh_m08_messages "
                            "(tenant_id,user_id,channel,message_id,payload_hash,run_id,body) "
                            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (*key, digest, fresh.run_id, request.model_dump_json()),
                        )
                        routing = request
                        if attribution:
                            routing = request.model_copy(
                                update={
                                    "question_hint": attribution[0],
                                    "expected_question_version": attribution[1],
                                }
                            )
                        fresh = await self.prepare_question(cursor, routing, fresh)
                        await cursor.execute(
                            "INSERT INTO dh_m08_runs (run_id,question_id,status) "
                            "VALUES (%s,%s,'RUNNING')",
                            (fresh.run_id, fresh.question.question_id),
                        )
                        await self.accepted(cursor, request, fresh)
                    await conn.commit()
                    return fresh
                except AppError, IntegrityError:
                    await conn.rollback()
                    raise
                except BaseException:
                    conn.close()  # disconnect rolls back uncommitted work, including cancellation
                    raise
        except IntegrityError as exc:
            if exc.args[0] != 1062:
                raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "MySQL acceptance failed") from None
            async with self.pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT m.payload_hash,r.run_id,q.body,r.response FROM dh_m08_messages m "
                    "JOIN dh_m08_runs r ON r.run_id=m.run_id "
                    "JOIN dh_m08_questions q ON q.question_id=r.question_id "
                    "WHERE m.tenant_id=%s AND m.user_id=%s AND m.channel=%s AND m.message_id=%s",
                    key,
                )
                row = await cursor.fetchone()
                await conn.rollback()
            if row is None:
                raise AppError(
                    ErrorCode.UPSTREAM_UNAVAILABLE, "Message receipt unavailable"
                ) from None
            if row[0] != digest:
                raise AppError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Message payload conflicts"
                ) from None
            return Receipt(
                False,
                row[1],
                Question.model_validate_json(row[2]),
                ResponseEnvelope.model_validate_json(row[3]) if row[3] else None,
            )
        except MySQLError, OSError:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "MySQL acceptance unavailable") from None

    async def finish(
        self, receipt: Receipt, question: Question, response: ResponseEnvelope
    ) -> None:
        validate_terminal(receipt, question, response)
        try:
            async with self.pool.acquire() as conn:
                try:
                    async with conn.cursor() as cursor:
                        await self.before_finish(cursor, receipt, question, response)
                        await cursor.execute(
                            "UPDATE dh_m08_runs SET status=%s,response=%s,"
                            "finished_at=IF(%s='WAITING_APPROVAL',NULL,CURRENT_TIMESTAMP(6)) "
                            "WHERE run_id=%s AND question_id=%s AND status='RUNNING'",
                            (
                                response.run_status,
                                response.model_dump_json(),
                                response.run_status,
                                receipt.run_id,
                                question.question_id,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise AppError(ErrorCode.VERSION_CONFLICT, "Run no longer owned")
                        await cursor.execute(
                            "UPDATE dh_m08_questions SET version=%s,status=%s,body=%s "
                            "WHERE question_id=%s AND tenant_id=%s AND user_id=%s AND version=%s",
                            (
                                question.version,
                                question.status,
                                question.model_dump_json(),
                                question.question_id,
                                question.identity.tenant_id,
                                question.identity.user_id,
                                receipt.question.version,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise AppError(ErrorCode.VERSION_CONFLICT, "Question version changed")
                        await self.changed(cursor, question, "run_finished")
                    await conn.commit()
                except AppError:
                    await conn.rollback()
                    raise
                except BaseException:
                    conn.close()
                    raise
        except MySQLError, OSError:
            raise AppError(
                ErrorCode.UPSTREAM_UNAVAILABLE, "MySQL terminal write unavailable"
            ) from None

    async def question(
        self, identity: VerifiedIdentity, session: str, question_id: str
    ) -> Question | None:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m08_questions WHERE question_id=%s AND tenant_id=%s "
                "AND user_id=%s AND session_id=%s",
                (question_id, identity.tenant_id, identity.user_id, session),
            )
            row = await cursor.fetchone()
            await conn.rollback()
        return Question.model_validate_json(row[0]) if row else None

    async def prepare_question(
        self, cursor: Any, request: RequestEnvelope, fresh: Receipt
    ) -> Receipt:
        await cursor.execute(
            "INSERT INTO dh_m08_questions "
            "(question_id,tenant_id,user_id,session_id,version,status,body) "
            "VALUES (%s,%s,%s,%s,1,'ACTIVE',%s)",
            (
                fresh.question.question_id,
                request.identity.tenant_id,
                request.identity.user_id,
                request.session_id,
                fresh.question.model_dump_json(),
            ),
        )
        return fresh

    async def accepted(self, cursor: Any, request: RequestEnvelope, receipt: Receipt) -> None:
        pass

    async def before_finish(
        self, cursor: Any, receipt: Receipt, question: Question, response: ResponseEnvelope
    ) -> None:
        pass

    async def changed(self, cursor: Any, question: Question, reason: str) -> None:
        pass
