"""Business approval/operation authority. Session, case and operation lock in that order.

All locks are short MySQL transactions. HTTP, graph persistence and human waits
take place after commit. The checkpoint can be reconstructed from these records.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from deephelp_app.cases import MySQLCaseRepository
from deephelp_app.checkpoints import migrate_m15
from deephelp_app.domain.models import (
    ApprovalCommand,
    ApprovalStatus,
    ErrorCode,
    NextAction,
    OperationRecord,
    OperationStatus,
    Outcome,
    Question,
    QuestionStatus,
    ResponseEnvelope,
    RunStatus,
    VerifiedIdentity,
)
from deephelp_app.errors import AppError
from deephelp_app.ledger import Receipt

TERMINAL = {OperationStatus.SUCCEEDED, OperationStatus.FAILED, OperationStatus.CANCELLED}


def validate_binding(op: OperationRecord, q: Question, snapshot: str) -> None:
    plan = op.plan
    values = {e.name.value: e.value for e in q.entities}
    if (
        q.identity != plan.identity
        or q.session_id != op.session_id
        or q.question_id != plan.question_id
        or q.version != op.question_version
        or q.status != QuestionStatus.WAITING_APPROVAL
        or q.approval_operation_id != plan.operation_id
        or q.versions.sop != plan.sop_version
        or q.versions.sop_snapshot != plan.snapshot_hash
        or snapshot != plan.snapshot_hash
        or values.get("order_id") != plan.parameters.order_id
        or q.unresolved_fields
    ):
        raise AppError(ErrorCode.VERSION_CONFLICT, "Approval binding is no longer current", 409)


class ApprovalRepository(MySQLCaseRepository):
    approval_ttl_seconds = 900
    lease_seconds = 120

    async def current_snapshot(self, cursor: Any, op: OperationRecord, snapshot: str) -> str:
        return snapshot

    async def migrate(self) -> None:
        await super().migrate()
        await migrate_m15(self.pool)

    async def busy(self, cursor: Any, qid: str) -> bool:
        await cursor.execute(
            "SELECT 1 FROM dh_m08_runs WHERE question_id=%s "
            "AND status IN ('RUNNING','WAITING_APPROVAL') LIMIT 1",
            (qid,),
        )
        return await cursor.fetchone() is not None

    async def before_finish(
        self,
        cursor: Any,
        receipt: Receipt,
        question: Question,
        response: ResponseEnvelope,
    ) -> None:
        await super().before_finish(cursor, receipt, question, response)
        if response.outcome != Outcome.PENDING_APPROVAL:
            return
        plan = response.sop_plan
        assert plan is not None
        if plan.question_version != receipt.question.version or plan.identity != question.identity:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Proposal input binding differs", 409)
        op = OperationRecord(
            plan=plan,
            run_id=receipt.run_id,
            session_id=question.session_id,
            question_version=question.version,
            expires_at=datetime.now(UTC) + timedelta(seconds=self.approval_ttl_seconds),
            pending_response=response,
        )
        validate_binding(op, question, plan.snapshot_hash)
        await cursor.execute(
            "INSERT INTO dh_m15_operations "
            "(operation_id,run_id,question_id,status,approval_status,expires_at,body) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (
                plan.operation_id,
                op.run_id,
                question.question_id,
                op.status,
                op.approval_status,
                op.expires_at.replace(tzinfo=None),
                op.model_dump_json(),
            ),
        )
        await self.audit(cursor, op, "prepared")

    async def get(self, operation_id: str) -> OperationRecord:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m15_operations WHERE operation_id=%s",
                (operation_id,),
            )
            row = await cursor.fetchone()
            await conn.rollback()
        if not row:
            raise AppError(ErrorCode.NOT_FOUND, "Operation unavailable", 404)
        return OperationRecord.model_validate_json(row[0])

    async def scoped(
        self,
        identity: VerifiedIdentity,
        session: str,
        run: str,
        operation_id: str,
    ) -> OperationRecord:
        op = await self.get(operation_id)
        if op.plan.identity != identity or op.session_id != session or op.run_id != run:
            raise AppError(ErrorCode.NOT_FOUND, "Operation unavailable", 404)
        return op

    async def by_run(self, run_id: str) -> OperationRecord | None:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute("SELECT body FROM dh_m15_operations WHERE run_id=%s", (run_id,))
            row = await cursor.fetchone()
            await conn.rollback()
        return OperationRecord.model_validate_json(row[0]) if row else None

    async def history(self, operation_id: str) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT kind,body FROM dh_m15_audit WHERE operation_id=%s ORDER BY event_id",
                (operation_id,),
            )
            rows = await cursor.fetchall()
            await conn.rollback()
        return [{"kind": k, **json.loads(body)} for k, body in rows]

    @asynccontextmanager
    async def locked(
        self, operation_id: str
    ) -> AsyncIterator[tuple[Any, OperationRecord, Question, Any]]:
        initial = await self.get(operation_id)
        async with self.pool.acquire() as conn:
            try:
                async with conn.cursor() as cursor:
                    await self.lock_session(cursor, initial.plan.identity, initial.session_id)
                    await cursor.execute(
                        "SELECT body FROM dh_m08_questions WHERE question_id=%s FOR UPDATE",
                        (initial.plan.question_id,),
                    )
                    qrow = await cursor.fetchone()
                    if not qrow:
                        raise AppError(ErrorCode.NOT_FOUND, "Question unavailable", 404)
                    await cursor.execute(
                        "SELECT body,lease_token,lease_until FROM dh_m15_operations "
                        "WHERE operation_id=%s FOR UPDATE",
                        (operation_id,),
                    )
                    row = await cursor.fetchone()
                    yield (
                        cursor,
                        OperationRecord.model_validate_json(row[0]),
                        Question.model_validate_json(qrow[0]),
                        row[1:],
                    )
                await conn.commit()
            except AppError:
                await conn.rollback()
                raise
            except BaseException:
                conn.close()
                raise

    async def audit(self, cursor: Any, op: OperationRecord, kind: str) -> None:
        await cursor.execute(
            "INSERT INTO dh_m15_audit (operation_id,kind,body) VALUES (%s,%s,%s)",
            (
                op.plan.operation_id,
                kind,
                json.dumps(
                    {
                        "status": op.status.value,
                        "approval_status": op.approval_status.value,
                        "question_version": op.question_version,
                        "dispatch_attempts": op.dispatch_attempts,
                        "query_attempts": op.query_attempts,
                        "last_error": op.last_error,
                        "approver": op.approver.model_dump() if op.approver else None,
                    }
                ),
            ),
        )

    async def save(self, cursor: Any, op: OperationRecord, kind: str) -> None:
        await cursor.execute(
            "UPDATE dh_m15_operations SET status=%s,approval_status=%s,body=%s "
            "WHERE operation_id=%s",
            (op.status, op.approval_status, op.model_dump_json(), op.plan.operation_id),
        )
        await self.audit(cursor, op, kind)

    async def close_operation(
        self,
        cursor: Any,
        op: OperationRecord,
        q: Question,
        response: ResponseEnvelope,
    ) -> OperationRecord:
        q = Question.model_validate(
            q.model_copy(
                update={
                    "status": response.question_status,
                    "version": q.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            ).model_dump()
        )
        await cursor.execute(
            "UPDATE dh_m08_questions SET status=%s,version=%s,body=%s "
            "WHERE question_id=%s AND version=%s",
            (q.status, q.version, q.model_dump_json(), q.question_id, op.question_version),
        )
        if cursor.rowcount != 1:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Question changed during operation", 409)
        await cursor.execute(
            "UPDATE dh_m08_runs SET status=%s,response=%s,finished_at=CURRENT_TIMESTAMP(6) "
            "WHERE run_id=%s AND status='WAITING_APPROVAL'",
            (response.run_status, response.model_dump_json(), op.run_id),
        )
        if cursor.rowcount != 1:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Approval run no longer owned", 409)
        op = op.model_copy(update={"response": response})
        await self.save(cursor, op, "closed")
        await self.event(
            cursor, q, {"kind": "operation_closed", "operation_id": op.plan.operation_id}
        )
        return op

    async def cancel(
        self,
        cursor: Any,
        op: OperationRecord,
        q: Question,
        status: ApprovalStatus,
    ) -> OperationRecord:
        response = ResponseEnvelope.model_validate(
            op.pending_response.model_copy(
                update={
                    "outcome": Outcome.HANDOFF,
                    "question_status": QuestionStatus.HANDED_OFF,
                    "run_status": RunStatus.SUCCEEDED,
                    "next_action": NextAction.CONTACT_SUPPORT,
                    "sop_plan": None,
                    "reply_presentation": None,
                    "reply": "变更未执行，审批已"
                    + {
                        ApprovalStatus.REJECTED: "拒绝",
                        ApprovalStatus.EXPIRED: "过期",
                        ApprovalStatus.REVOKED: "撤销",
                    }[status]
                    + "；如需处理请重新规划。",
                }
            ).model_dump()
        )
        op = op.model_copy(update={"approval_status": status, "status": OperationStatus.CANCELLED})
        return await self.close_operation(cursor, op, q, response)

    async def decide(
        self,
        identity: VerifiedIdentity,
        operation_id: str,
        command: ApprovalCommand,
        snapshot: str,
    ) -> OperationRecord:
        await self.scoped(identity, command.session_id, command.run_id, operation_id)
        async with self.locked(operation_id) as (cursor, op, q, lease):
            if (
                command.expected_question_version != op.question_version
                or command.parameters_hash != op.plan.parameters_hash
                or command.sop_version != op.plan.sop_version
                or command.snapshot_hash != op.plan.snapshot_hash
            ):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Approval command binding differs", 409)
            desired = {
                "approve": ApprovalStatus.APPROVED,
                "reject": ApprovalStatus.REJECTED,
                "revoke": ApprovalStatus.REVOKED,
            }[command.decision]
            if op.approval_status == desired:
                return op
            if op.status != OperationStatus.PREPARED or (
                op.approval_status != ApprovalStatus.PENDING
                and not (
                    desired == ApprovalStatus.REVOKED
                    and op.approval_status == ApprovalStatus.APPROVED
                )
            ):
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Operation cannot accept this decision", 409
                )
            if lease[0] and lease[1] > datetime.now(UTC).replace(tzinfo=None):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Operation is being resumed", 409)
            # Closing the old plan needs its unchanged case binding, not the newly
            # published SOP. A stale plan still cannot gain execution approval.
            validate_binding(op, q, op.plan.snapshot_hash)
            if op.expires_at <= datetime.now(UTC):
                return await self.cancel(cursor, op, q, ApprovalStatus.EXPIRED)
            if desired == ApprovalStatus.APPROVED:
                validate_binding(op, q, await self.current_snapshot(cursor, op, snapshot))
            op = op.model_copy(update={"approver": identity})
            if desired != ApprovalStatus.APPROVED:
                return await self.cancel(cursor, op, q, desired)
            op = op.model_copy(update={"approval_status": desired})
            await self.save(cursor, op, "approved")
            return op

    async def acquire(self, operation_id: str, snapshot: str) -> tuple[OperationRecord, str | None]:
        async with self.locked(operation_id) as (cursor, op, q, lease):
            if op.status in TERMINAL:
                return op, None
            if lease[0] and lease[1] > datetime.now(UTC).replace(tzinfo=None):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Operation resume already owned", 409)
            if op.status == OperationStatus.PREPARED:
                validate_binding(op, q, op.plan.snapshot_hash)
                if op.expires_at <= datetime.now(UTC):
                    return await self.cancel(cursor, op, q, ApprovalStatus.EXPIRED), None
                validate_binding(op, q, await self.current_snapshot(cursor, op, snapshot))
                if op.approval_status != ApprovalStatus.APPROVED or op.approver != op.plan.identity:
                    raise AppError(ErrorCode.FORBIDDEN, "Explicit approval required", 403)
            # Already dispatched operations must be reconciled even after expiry or SOP change.
            token = uuid4().hex
            await cursor.execute(
                "UPDATE dh_m15_operations SET lease_token=%s,lease_until=%s WHERE operation_id=%s",
                (
                    token,
                    (datetime.now(UTC) + timedelta(seconds=self.lease_seconds)).replace(
                        tzinfo=None
                    ),
                    operation_id,
                ),
            )
            return op, token

    @staticmethod
    def own(lease: Any, token: str) -> None:
        if lease[0] != token or lease[1] <= datetime.now(UTC).replace(tzinfo=None):
            raise AppError(ErrorCode.VERSION_CONFLICT, "Operation execution lease changed", 409)

    async def dispatch(self, operation_id: str, token: str, snapshot: str) -> OperationRecord:
        async with self.locked(operation_id) as (cursor, op, q, lease):
            self.own(lease, token)
            validate_binding(op, q, await self.current_snapshot(cursor, op, snapshot))
            if op.approval_status != ApprovalStatus.APPROVED or op.expires_at <= datetime.now(UTC):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Approval no longer executable", 409)
            if op.dispatch_attempts >= 2:
                raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Operation retry bound reached", 409)
            op = op.model_copy(
                update={
                    "status": OperationStatus.IN_FLIGHT,
                    "dispatch_attempts": op.dispatch_attempts + 1,
                }
            )
            await self.save(cursor, op, "dispatched")
            return op

    async def unknown(self, operation_id: str, token: str, error: str = "cancelled") -> None:
        async with self.locked(operation_id) as (cursor, op, q, lease):
            self.own(lease, token)
            if op.status not in TERMINAL:
                await self.save(
                    cursor,
                    op.model_copy(
                        update={
                            "status": OperationStatus.UNKNOWN,
                            "last_error": error,
                        }
                    ),
                    "unknown",
                )

    async def query_dispatched(self, operation_id: str, token: str) -> OperationRecord:
        async with self.locked(operation_id) as (cursor, op, q, lease):
            self.own(lease, token)
            op = op.model_copy(update={"query_attempts": op.query_attempts + 1})
            await self.save(cursor, op, "query_dispatched")
            return op

    async def complete(
        self,
        operation_id: str,
        token: str,
        response: ResponseEnvelope,
    ) -> OperationRecord:
        async with self.locked(operation_id) as (cursor, op, q, lease):
            self.own(lease, token)
            if op.status in TERMINAL:
                return op
            if q.version != op.question_version:
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Effect requires manual reconciliation", 409
                )
            return await self.close_operation(
                cursor,
                op.model_copy(
                    update={
                        "status": OperationStatus.SUCCEEDED,
                        "last_error": None,
                    }
                ),
                q,
                response,
            )

    async def release(self, operation_id: str, token: str) -> None:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE dh_m15_operations SET lease_token=NULL,lease_until=NULL "
                "WHERE operation_id=%s AND lease_token=%s",
                (operation_id, token),
            )
            await conn.commit()
