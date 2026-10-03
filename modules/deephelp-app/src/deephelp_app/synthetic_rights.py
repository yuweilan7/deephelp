"""Owned loopback synthetic rights service. Effects and calls survive service restarts.

No payment/refund integration. Only a signed, approved M15 operation can record a
synthetic adjustment. The downstream effect commits separately from the agent ledger.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Request

from deephelp_app.approval_store import ApprovalRepository
from deephelp_app.domain.models import (
    DTO,
    ApprovalStatus,
    ErrorCode,
    Identifier,
    OperationRecord,
    OperationStatus,
)
from deephelp_app.errors import AppError
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.sop_acceptance import fixtures


def binding_hash(op: OperationRecord) -> str:
    data = {
        "plan": op.plan.model_dump(mode="json"),
        "run_id": op.run_id,
        "session_id": op.session_id,
        "question_version": op.question_version,
    }
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def signature(key: bytes, kind: str, operation: str, digest: str) -> str:
    return hmac.new(key, f"{kind}:{operation}:{digest}".encode(), hashlib.sha256).hexdigest()


class RightsRequest(DTO):
    operation_id: Identifier
    binding_hash: str
    signature: str


class SyntheticEffect(DTO):
    operation_id: Identifier
    binding_hash: str
    call_id: Identifier
    order_id: Identifier
    status: Literal["applied"] = "applied"
    synthetic: Literal[True] = True


class RightsObservation(DTO):
    status: Literal["ABSENT", "SUCCEEDED"]
    effect: SyntheticEffect | None = None


def create_rights_app(root: Path, key: bytes, fault_path: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        repo = await ApprovalRepository.open(root)
        app.state.repo = repo
        try:
            yield
        finally:
            await repo.aclose()

    app = FastAPI(lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "kind": "synthetic_rights"}

    async def handle(body: RightsRequest, request: Request, kind: str) -> RightsObservation:
        if not hmac.compare_digest(
            signature(key, kind, body.operation_id, body.binding_hash), body.signature
        ):
            raise HTTPException(403, "Invalid service signature")
        repo: ApprovalRepository = request.app.state.repo
        try:
            op = await repo.get(body.operation_id)
        except AppError:
            raise HTTPException(404, "Operation unavailable") from None
        if body.binding_hash != binding_hash(op):
            raise HTTPException(409, "Operation binding differs")
        call_id = uuid4().hex
        # A separate short downstream transaction is intentional.
        async with repo.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                await cursor.execute(
                    "INSERT INTO dh_m15_synthetic_calls (call_id,operation_id,kind) "
                    "VALUES (%s,%s,%s)",
                    (call_id, body.operation_id, kind),
                )
                await conn.commit()
                await cursor.execute(
                    "SELECT body FROM dh_m15_operations WHERE operation_id=%s FOR UPDATE",
                    (body.operation_id,),
                )
                op = OperationRecord.model_validate_json((await cursor.fetchone())[0])
                await cursor.execute(
                    "SELECT binding_hash,body FROM dh_m15_synthetic_effects WHERE operation_id=%s",
                    (body.operation_id,),
                )
                row = await cursor.fetchone()
                if row:
                    if row[0] != body.binding_hash:
                        raise HTTPException(409, "Idempotency payload differs")
                    await conn.rollback()
                    return RightsObservation(
                        status="SUCCEEDED", effect=SyntheticEffect.model_validate_json(row[1])
                    )
                if kind == "query":
                    await conn.rollback()
                    return RightsObservation(status="ABSENT")
                # Keep the operation lock through the effect commit. A query must
                # not observe ABSENT while this accepted execute can still commit.
                from datetime import UTC, datetime

                if (
                    op.approval_status != ApprovalStatus.APPROVED
                    or op.status != OperationStatus.IN_FLIGHT
                    or op.expires_at <= datetime.now(UTC)
                ):
                    raise HTTPException(409, "Operation is not authorized for dispatch")
                order = next(
                    (o for o in fixtures().orders if o.order_id == op.plan.parameters.order_id),
                    None,
                )
                if (
                    not order
                    or order.identity != op.plan.identity
                    or order.discount_status != "missing"
                ):
                    raise HTTPException(403, "Synthetic order precondition denied")
                fault = (
                    json.loads(await asyncio.to_thread(fault_path.read_text, encoding="utf-8")).get(
                        body.operation_id
                    )
                    if fault_path
                    else None
                )
                if fault == "before_effect":
                    raise HTTPException(503, "Injected before effect")
                effect = SyntheticEffect(
                    operation_id=body.operation_id,
                    binding_hash=body.binding_hash,
                    call_id=call_id,
                    order_id=order.order_id,
                )
                await cursor.execute(
                    "INSERT INTO dh_m15_synthetic_effects (operation_id,binding_hash,body) "
                    "VALUES (%s,%s,%s) "
                    "ON DUPLICATE KEY UPDATE operation_id=operation_id",
                    (body.operation_id, body.binding_hash, effect.model_dump_json()),
                )
                await cursor.execute(
                    "SELECT body FROM dh_m15_synthetic_effects WHERE operation_id=%s",
                    (body.operation_id,),
                )
                stored = SyntheticEffect.model_validate_json((await cursor.fetchone())[0])
                if stored.binding_hash != body.binding_hash:
                    raise HTTPException(409, "Idempotency payload differs")
                await conn.commit()
                if fault == "crash_after_effect":
                    os._exit(86)  # owned acceptance service only, after the durable effect commit
                if fault == "lost_response":
                    raise HTTPException(503, "Injected response loss after effect")
                return RightsObservation(status="SUCCEEDED", effect=stored)
            except BaseException:
                await conn.rollback()
                raise

    @app.post("/execute", response_model=RightsObservation)
    async def execute(body: RightsRequest, request: Request) -> RightsObservation:
        return await handle(body, request, "execute")

    @app.post("/query", response_model=RightsObservation)
    async def query(body: RightsRequest, request: Request) -> RightsObservation:
        return await handle(body, request, "query")

    return app


class RightsClient:
    def __init__(self, client: httpx.AsyncClient, port: int, key: bytes) -> None:
        if not 1 <= port <= 65535 or len(key) != 32:
            raise ValueError("Loopback port and a 32-byte service key are required")
        self.client, self.port, self.key = client, port, key
        self.calls = AsyncCalls(2, 10)

    async def call(
        self, kind: Literal["execute", "query"], op: OperationRecord, budget: ExecutionBudget
    ) -> RightsObservation:
        digest = binding_hash(op)
        body = RightsRequest(
            operation_id=op.plan.operation_id,
            binding_hash=digest,
            signature=signature(self.key, kind, op.plan.operation_id, digest),
        )

        async def send() -> RightsObservation:
            reply = await self.client.post(
                f"http://127.0.0.1:{self.port}/{kind}", json=body.model_dump()
            )
            if reply.status_code != 200:
                raise AppError(
                    ErrorCode.UPSTREAM_UNAVAILABLE,
                    f"Synthetic operation HTTP {reply.status_code} needs reconciliation",
                    503,
                )
            observation = RightsObservation.model_validate(reply.json())
            effect = observation.effect
            if observation.status == "SUCCEEDED" and (
                effect is None
                or effect.operation_id != op.plan.operation_id
                or effect.binding_hash != digest
                or effect.order_id != op.plan.parameters.order_id
            ):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Synthetic result binding differs")
            if observation.status == "ABSENT" and (kind != "query" or effect is not None):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Invalid synthetic observation")
            return observation

        return await self.calls.call(send, budget, retry_safe=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--fault-path", type=Path)
    args = parser.parse_args()
    key = args.key.read_bytes()
    if len(key) != 32:
        raise ValueError("Expected a 32-byte key")
    uvicorn.run(
        create_rights_app(Path.cwd(), key, args.fault_path),
        host="127.0.0.1",
        port=args.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
