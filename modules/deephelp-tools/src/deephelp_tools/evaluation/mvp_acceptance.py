"""Dedicated MySQL isolation checks and a real loopback server for explicit live probes."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI

from deephelp_app.adapters.ledger import MySQLLedger
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models import (
    ErrorCode,
    NextAction,
    Outcome,
    QuestionStatus,
    RequestEnvelope,
    ResponseEnvelope,
    RunStatus,
    VerifiedIdentity,
)


@asynccontextmanager
async def api_client(app: FastAPI, *, http: bool) -> AsyncIterator[httpx.AsyncClient]:
    if not http:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://local"
        ) as client:
            yield client
        return
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(
        uvicorn.Config(
            app, log_config=None, access_log=False, lifespan="off", timeout_graceful_shutdown=5
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise AppError(
                        ErrorCode.INTERNAL_ERROR, "Loopback server exited before startup"
                    )
                await asyncio.sleep(0.01)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{listener.getsockname()[1]}", trust_env=False, timeout=100
        ) as client:
            yield client
    finally:
        server.should_exit = True
        async with asyncio.timeout(6):
            await task
        listener.close()


async def mysql_claim_checks(ledger: MySQLLedger) -> dict[str, bool]:
    nonce = "m08-claim-" + uuid4().hex
    identity = VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-a")
    now = datetime.now(UTC)
    request = RequestEnvelope(
        channel="m08-claim",
        session_id=nonce,
        message_id=nonce,
        raw_text="synthetic receipt concurrency only",
        occurred_at=now,
        received_at=now,
        request_id=uuid4().hex,
        trace_id=uuid4().hex,
        identity=identity,
    )
    receipts = await asyncio.gather(*(ledger.accept(request) for _ in range(8)))
    owner = next(r for r in receipts if r.acquired)
    result = {
        "one_concurrent_owner": sum(r.acquired for r in receipts) == 1,
        "same_run": len({r.run_id for r in receipts}) == 1,
        "pending_has_no_response": all(r.response is None for r in receipts),
    }
    # A new connection pool cannot acquire/execute the existing RUNNING receipt.
    reader = await MySQLLedger.open(Path.cwd())
    try:
        existing = await reader.accept(request)
        result["new_pool_pending_not_reclaimed"] = (
            not existing.acquired and existing.response is None
        )
    finally:
        await reader.aclose()
    other_request = request.model_copy(
        update={
            "identity": VerifiedIdentity(tenant_id="synthetic-tenant", user_id="synthetic-user-b")
        }
    )
    other = await ledger.accept(other_request)
    result["user_isolation"] = other.acquired and other.run_id != owner.run_id
    result["question_ownership"] = (
        await ledger.question(
            other_request.identity, request.session_id, owner.question.question_id
        )
        is None
    )
    conflict = False
    try:
        await ledger.accept(request.model_copy(update={"raw_text": "different"}))
    except AppError as exc:
        conflict = exc.code == ErrorCode.IDEMPOTENCY_CONFLICT
    result["payload_conflict"] = conflict
    for receipt, context in [(owner, request), (other, other_request)]:
        question = receipt.question.model_copy(
            update={"version": 2, "status": QuestionStatus.CANCELLED}
        )
        response = ResponseEnvelope(
            request_id=context.request_id,
            trace_id=context.trace_id,
            run_id=receipt.run_id,
            question_id=question.question_id,
            run_status=RunStatus.CANCELLED,
            question_status=QuestionStatus.CANCELLED,
            outcome=Outcome.ERROR,
            next_action=NextAction.CONTACT_SUPPORT,
            reply="Dedicated claim test ended without tools",
        )
        await ledger.finish(receipt, question, response)
    stored = await ledger.accept(request)
    result["terminal_replay"] = stored.response is not None and stored.question.version == 2
    try:
        await ledger.finish(owner, stored.question, stored.response)  # type: ignore[arg-type]
        result["second_finalize_rejected"] = False
    except AppError as exc:
        result["second_finalize_rejected"] = exc.code == ErrorCode.VERSION_CONFLICT
    return result
