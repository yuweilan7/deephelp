"""Explicit synthetic rights HTTP server; effects are not enterprise writes."""

import argparse
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from deephelp_app.adapters.approval_store import ApprovalRepository
from deephelp_app.adapters.synthetic_rights import (
    RightsObservation,
    RightsRequest,
    SyntheticEffect,
    binding_hash,
    signature,
)
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models import ApprovalStatus, OperationRecord, OperationStatus
from deephelp_tools.demo.scenarios import fixtures


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
                fault = None
                if fault_path:
                    from deephelp_tools.learning.rights_faults import before_effect

                    fault = await before_effect(fault_path, body.operation_id)
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
                if fault:
                    from deephelp_tools.learning.rights_faults import after_effect

                    after_effect(fault)
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
