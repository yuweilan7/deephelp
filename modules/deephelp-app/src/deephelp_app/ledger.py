"""Durable single-message acceptance. RUNNING receipts are never automatically reclaimed."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
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
        include={"session_id", "raw_text", "occurred_at", "question_hint", "schema_version"},
    )
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

    async def accept(self, request: RequestEnvelope) -> Receipt:
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
        or response.run_status not in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
    ):
        raise AppError(ErrorCode.VERSION_CONFLICT, "Terminal ledger binding differs")


class MySQLLedger:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    @classmethod
    async def open(cls, root: Path) -> MySQLLedger:
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
        await self.pool.wait_closed()

    async def migrate(self) -> None:
        from importlib.resources import files

        sql = files("deephelp_app").joinpath("migrations/008_mvp.sql").read_text(encoding="utf-8")
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            for statement in sql.split(";"):
                if statement.strip():
                    await cursor.execute(statement)
            await conn.commit()

    async def accept(self, request: RequestEnvelope) -> Receipt:
        fresh = new_receipt(request)
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
                        await cursor.execute(
                            "INSERT INTO dh_m08_questions "
                            "(question_id,tenant_id,user_id,session_id,"
                            "version,status,body) VALUES (%s,%s,%s,%s,1,'ACTIVE',%s)",
                            (
                                fresh.question.question_id,
                                *key[:2],
                                request.session_id,
                                fresh.question.model_dump_json(),
                            ),
                        )
                        await cursor.execute(
                            "INSERT INTO dh_m08_runs (run_id,question_id,status) "
                            "VALUES (%s,%s,'RUNNING')",
                            (fresh.run_id, fresh.question.question_id),
                        )
                    await conn.commit()
                    return fresh
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
                        await cursor.execute(
                            "UPDATE dh_m08_runs SET status=%s,response=%s,"
                            "finished_at=CURRENT_TIMESTAMP(6) "
                            "WHERE run_id=%s AND question_id=%s AND status='RUNNING'",
                            (
                                response.run_status,
                                response.model_dump_json(),
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
                    await conn.commit()
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
