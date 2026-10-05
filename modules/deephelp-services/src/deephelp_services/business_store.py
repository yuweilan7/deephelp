"""Business-owned short transactions. Every acquisition ends its snapshot before pooling."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from psycopg import Error as PostgreSQLError
from pydantic import ValidationError

from deephelp_app.adapters.ledger import PostgresLedger
from deephelp_app.adapters.mcp_protocol import TOOL_VERSION, canonical
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.business import (
    BusinessActivityInput,
    BusinessCouponInput,
    BusinessInput,
    BusinessOrderInput,
    BusinessRecord,
    BusinessRelations,
)
from deephelp_app.domain.models.common import ErrorCode, Money, VerifiedIdentity
from deephelp_app.domain.models.evidence import EvidenceRef, EvidenceSource, Fact, FactKind
from deephelp_app.domain.models.tools import (
    ToolInvocationEnvelope,
    ToolName,
    ToolResult,
    ToolStatus,
)

Kind = Literal["orders", "coupons", "activities"]
MODELS: dict[
    str, type[BusinessOrderInput] | type[BusinessCouponInput] | type[BusinessActivityInput]
] = {
    "orders": BusinessOrderInput,
    "coupons": BusinessCouponInput,
    "activities": BusinessActivityInput,
}
IDS = {"orders": "order_id", "coupons": "coupon_id", "activities": "activity_id"}


def checked_kind(kind: str) -> str:
    if kind not in MODELS:
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Unknown business resource")
    return kind


class BusinessRepository:
    def __init__(self, connection: PostgresLedger) -> None:
        self.connection, self.pool = connection, connection.pool

    @classmethod
    async def open(cls, root: Path) -> BusinessRepository:
        return cls(await PostgresLedger.open(root))

    async def aclose(self) -> None:
        await self.connection.aclose()

    @asynccontextmanager
    async def transaction(self, *, snapshot: bool = False) -> AsyncIterator[Any]:
        try:
            async with asyncio.timeout(10), self.pool.acquire() as conn:
                await conn.rollback()
                if snapshot:
                    await conn.execute("BEGIN ISOLATION LEVEL REPEATABLE READ")
                else:
                    await conn.begin()
                try:
                    async with conn.cursor() as cursor:
                        yield cursor
                    await conn.commit()
                except BaseException:
                    await conn.rollback()
                    raise
        except PostgreSQLError as exc:
            if exc.sqlstate in {"23505", "55P03", "40P01", "40001"}:
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Concurrent business record changed"
                ) from None
            raise AppError(
                ErrorCode.UPSTREAM_UNAVAILABLE, "Business database unavailable"
            ) from None
        except OSError:
            raise AppError(
                ErrorCode.UPSTREAM_UNAVAILABLE, "Business database unavailable"
            ) from None
        except TimeoutError:
            raise AppError(ErrorCode.TIMEOUT, "Business database timed out") from None

    async def migrate(self) -> None:
        sql = files("deephelp_services").joinpath("resources/020_business.sql").read_text()
        async with self.transaction() as cursor:
            for statement in sql.split(";"):
                if statement.strip():
                    await cursor.execute(statement)

    @staticmethod
    def owner(identity: VerifiedIdentity) -> tuple[str, str]:
        return identity.tenant_id, identity.user_id

    async def _get(
        self,
        cursor: Any,
        kind: str,
        identity: VerifiedIdentity,
        record_id: str,
        *,
        lock: bool = False,
    ) -> BusinessRecord:
        checked_kind(kind)
        money_columns = (
            ",paid,discount" if kind == "orders" else ",minimum_spend" if kind == "coupons" else ""
        )
        await cursor.execute(
            f"SELECT version,body{money_columns} FROM dh_m20_{kind} "
            "WHERE tenant_id=%s AND user_id=%s "
            "AND record_id=%s" + (" FOR UPDATE" if lock else ""),
            (*self.owner(identity), record_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "Business record not found for current identity")
        try:
            body = json.loads(row[1])
            if kind == "orders":
                body["paid"], body["discount"] = {"amount": str(row[2])}, {"amount": str(row[3])}
            elif kind == "coupons":
                body["minimum_spend"] = {"amount": str(row[2])}
            data = MODELS[kind].model_validate(body)
        except ValueError, TypeError, KeyError, IndexError, ValidationError:
            raise AppError(
                ErrorCode.MODEL_OUTPUT_INVALID, "Invalid stored business record"
            ) from None
        return BusinessRecord(identity=identity, version=row[0], data=data)

    async def get(self, kind: str, identity: VerifiedIdentity, record_id: str) -> BusinessRecord:
        async with self.transaction() as cursor:
            return await self._get(cursor, kind, identity, record_id)

    async def listing(
        self,
        kind: str,
        identity: VerifiedIdentity,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        checked_kind(kind)
        if not 0 <= offset <= 1000000 or not 1 <= limit <= 100:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Invalid business pagination")
        async with self.transaction() as cursor:
            await cursor.execute(
                f"SELECT record_id FROM dh_m20_{kind} WHERE tenant_id=%s AND user_id=%s "
                "ORDER BY record_id LIMIT %s OFFSET %s",
                (*self.owner(identity), limit + 1, offset),
            )
            rows = await cursor.fetchall()
            records = [await self._get(cursor, kind, identity, row[0]) for row in rows[:limit]]
            return {
                "items": [r.model_dump(mode="json") for r in records],
                "next_offset": offset + limit if len(rows) > limit else None,
            }

    async def put(
        self,
        kind: str,
        identity: VerifiedIdentity,
        data: BusinessInput,
        expected_version: int | None = None,
    ) -> BusinessRecord:
        checked_kind(kind)
        if type(data) is not MODELS[kind]:
            raise AppError(ErrorCode.INVALID_ARGUMENT, "Business resource type mismatch")
        record_id = str(getattr(data, IDS[kind]))
        async with self.transaction() as cursor:
            if isinstance(data, BusinessCouponInput):
                await self._get(cursor, "orders", identity, data.order_id, lock=True)
            try:
                current = await self._get(cursor, kind, identity, record_id, lock=True)
            except AppError as exc:
                if exc.code != ErrorCode.NOT_FOUND or expected_version is not None:
                    raise
                current = None
            if current:
                if expected_version is None:
                    if current.data == data:
                        return current
                    raise AppError(ErrorCode.IDEMPOTENCY_CONFLICT, "Existing business ID differs")
                if current.version != expected_version:
                    raise AppError(ErrorCode.VERSION_CONFLICT, "Business record version changed")
            version = current.version + 1 if current else 1
            columns, values = ["body"], [data.model_dump_json()]
            if isinstance(data, BusinessOrderInput):
                columns += ["paid", "discount"]
                values += [str(data.paid.amount), str(data.discount.amount)]
            elif isinstance(data, BusinessCouponInput):
                columns += ["order_id", "minimum_spend"]
                values += [data.order_id, str(data.minimum_spend.amount)]
            if current:
                await cursor.execute(
                    f"UPDATE dh_m20_{kind} SET version=%s,"
                    + ",".join(f"{c}=%s" for c in columns)
                    + " WHERE tenant_id=%s AND user_id=%s AND record_id=%s AND version=%s",
                    (version, *values, *self.owner(identity), record_id, expected_version),
                )
                if cursor.rowcount != 1:
                    raise AppError(ErrorCode.VERSION_CONFLICT, "Business record version changed")
            else:
                all_columns = ["tenant_id", "user_id", "record_id", "version", *columns]
                await cursor.execute(
                    f"INSERT INTO dh_m20_{kind} ("
                    + ",".join(all_columns)
                    + ") VALUES ("
                    + ",".join("%s" for _ in all_columns)
                    + ")",
                    (*self.owner(identity), record_id, version, *values),
                )
            return BusinessRecord(identity=identity, version=version, data=data)

    async def relations(
        self,
        identity: VerifiedIdentity,
        order_id: str,
        update: BusinessRelations | None = None,
    ) -> dict[str, Any]:
        async with self.transaction() as cursor:
            order = await self._get(cursor, "orders", identity, order_id, lock=update is not None)
            if update:
                if order.version != update.expected_version:
                    raise AppError(ErrorCode.VERSION_CONFLICT, "Order relation version changed")
                for activity_id in update.activity_ids:
                    await self._get(cursor, "activities", identity, activity_id)
                await cursor.execute(
                    "DELETE FROM dh_m20_order_activities WHERE tenant_id=%s AND user_id=%s "
                    "AND order_id=%s",
                    (*self.owner(identity), order_id),
                )
                for activity_id in sorted(update.activity_ids):
                    await cursor.execute(
                        "INSERT INTO dh_m20_order_activities VALUES (%s,%s,%s,%s)",
                        (*self.owner(identity), order_id, activity_id),
                    )
                await cursor.execute(
                    "UPDATE dh_m20_orders SET version=version+1 WHERE tenant_id=%s "
                    "AND user_id=%s AND record_id=%s",
                    (*self.owner(identity), order_id),
                )
            await cursor.execute(
                "SELECT activity_id FROM dh_m20_order_activities WHERE tenant_id=%s AND user_id=%s "
                "AND order_id=%s ORDER BY activity_id",
                (*self.owner(identity), order_id),
            )
            return {
                "order_id": order_id,
                "version": order.version + bool(update),
                "activity_ids": [r[0] for r in await cursor.fetchall()],
            }

    async def evidence(self, identity: VerifiedIdentity, evidence_id: str) -> dict[str, Any]:
        async with self.transaction() as cursor:
            await cursor.execute(
                "SELECT snapshot,result FROM dh_m20_evidence WHERE tenant_id=%s AND user_id=%s "
                "AND evidence_id=%s",
                (*self.owner(identity), evidence_id),
            )
            row = await cursor.fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "Business evidence not found for identity")
            return {"snapshot": json.loads(row[0]), "result": json.loads(row[1])}

    async def query(self, envelope: ToolInvocationEnvelope) -> ToolResult:
        req = envelope.request
        # Facts plus their immutable evidence must come from one consistent view.
        async with self.transaction(snapshot=True) as cursor:
            order = await self._get(cursor, "orders", req.identity, req.parameters.order_id)
            assert isinstance(order.data, BusinessOrderInput)
            snapshots = [order.model_dump(mode="json")]
            values: dict[str, Any] = {"order_id": order.data.order_id}
            now = datetime.now(UTC)
            if req.tool_name == ToolName.CHECK_COUPON:
                coupon = await self._get(
                    cursor, "coupons", req.identity, req.parameters.coupon_id or ""
                )
                assert isinstance(coupon.data, BusinessCouponInput)
                if coupon.data.order_id != order.data.order_id:
                    raise AppError(ErrorCode.INVALID_ARGUMENT, "Coupon does not belong to order")
                snapshots.append(coupon.model_dump(mode="json"))
                status = coupon.data.status
                if status == "usable":
                    if order.data.status != "active":
                        status = "scope_mismatch"
                    elif now < coupon.data.valid_from:
                        status = "not_started"
                    elif now >= coupon.data.valid_until:
                        status = "expired"
                    elif order.data.paid.amount < coupon.data.minimum_spend.amount:
                        status = "threshold_not_met"
                values.update(
                    coupon_id=coupon.data.coupon_id,
                    coupon_status=status,
                    minimum_spend=coupon.data.minimum_spend,
                    usable=status == "usable",
                )
            else:
                await cursor.execute(
                    "SELECT activity_id FROM dh_m20_order_activities WHERE tenant_id=%s "
                    "AND user_id=%s AND order_id=%s ORDER BY activity_id",
                    (*self.owner(req.identity), order.data.order_id),
                )
                activities = [
                    await self._get(cursor, "activities", req.identity, row[0])
                    for row in await cursor.fetchall()
                ]
                snapshots.extend(r.model_dump(mode="json") for r in activities)
                active = [
                    r.data
                    for r in activities
                    if isinstance(r.data, BusinessActivityInput)
                    and r.data.status == "active"
                    and r.data.valid_from <= now < r.data.valid_until
                    and order.data.status == "active"
                ]
                values.update(
                    paid=order.data.paid,
                    discount=order.data.discount,
                    discount_status=order.data.discount_status,
                    activity_ids=",".join(r.activity_id for r in active),
                    activity_labels=",".join(r.label for r in active),
                )
            evidence_id = "ev-" + uuid4().hex
            fact_values = {
                k: v.model_dump(mode="json") if isinstance(v, Money) else v
                for k, v in values.items()
            }
            result = ToolResult(
                operation_id=req.operation_id,
                run_id=req.run_id,
                tool_name=req.tool_name,
                tool_version=TOOL_VERSION,
                status=ToolStatus.SUCCEEDED,
                call_id=envelope.call_id,
                upstream_request_id=envelope.call_id,
                facts=tuple(
                    Fact(
                        name=k,
                        kind=FactKind.MONEY
                        if isinstance(v, Money)
                        else FactKind.FLAG
                        if isinstance(v, bool)
                        else FactKind.TEXT,
                        value=v,
                        evidence_ids=(evidence_id,),
                    )
                    for k, v in values.items()
                ),
                evidence_refs=(
                    EvidenceRef(
                        evidence_id=evidence_id,
                        source=EvidenceSource.TOOL,
                        record_id=envelope.call_id,
                        version="business-postgres-v1",
                        locator=order.data.order_id,
                        content_hash=hashlib.sha256(canonical(fact_values)).hexdigest(),
                        summary="PostgreSQL已提交业务记录；记录版本与原始快照可按evidence_id回查。",
                    ),
                ),
            )
            await cursor.execute(
                "INSERT INTO dh_m20_evidence "
                "(evidence_id,tenant_id,user_id,call_id,snapshot,result) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    evidence_id,
                    *self.owner(req.identity),
                    envelope.call_id,
                    json.dumps(
                        {"records": snapshots, "observed_at": now.isoformat()}, ensure_ascii=False
                    ),
                    result.model_dump_json(),
                ),
            )
            return result
