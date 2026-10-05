"""只读记忆投影、纯规则练习与显式同向量检索对照。

历史窗口从PG授权读取，Redis只展示generation键；检索POST明确执行才用原预算。
规则练习只解释当前原文证据，不将RuleMatch当最终意图或后台模型调用。
"""

import asyncio
import json
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Request
from pydantic import Field
from redis.exceptions import RedisError

from deephelp_app.adapters.persistence.history import PostgresHistory
from deephelp_app.api.workbench import BrowserAuth
from deephelp_app.application.text_entity import (
    TextPolicy,
    clean_text,
    match_rules,
    merge_entities,
    regex_entities,
    required_fields,
)
from deephelp_app.bootstrap.runtime.assembly import LiveAssembly
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import request_execution
from deephelp_app.domain.models.common import DTO, ErrorCode, Identifier
from deephelp_app.domain.models.conversation import RequestEnvelope


class Practice(DTO):
    text: Annotated[str, Field(strict=True, min_length=1, max_length=2000, pattern=r"\S")]


def analysis_router(assembly: LiveAssembly, auth: BrowserAuth) -> APIRouter:
    router = APIRouter(prefix="/learning")

    def context(body: Practice, request: Request) -> RequestEnvelope:
        identity = auth(request)
        now, message_id = datetime.now(UTC), uuid4().hex
        return RequestEnvelope(
            identity=identity,
            channel="m22-lab",
            session_id="m22-lab-" + message_id,
            message_id=message_id,
            raw_text=body.text,
            occurred_at=now,
            received_at=now,
            request_id=request.state.request_id,
            trace_id=request.state.trace_id,
        )

    @router.post("/rules")
    async def rules(body: Practice, request: Request) -> dict[str, Any]:
        envelope = context(body, request)
        observations = regex_entities(envelope)
        entities, conflicts, unresolved = merge_entities(
            observations, (), required_fields(body.text)
        )
        return {
            "mode": "pure rules; not full extraction or 600 intent decision",
            "model_calls": 0,
            "clean": clean_text(body.text, TextPolicy()).model_dump(mode="json"),
            "observations": [e.model_dump(mode="json") for e in observations],
            "confirmed": [e.model_dump(mode="json") for e in entities],
            "conflicts": [e.model_dump(mode="json") for e in conflicts],
            "unresolved": unresolved,
            "rule_matches": [m.model_dump(mode="json") for m in match_rules(envelope)],
            "code": ["application/text_entity.py:regex_entities / merge_entities / match_rules"],
        }

    @router.post("/retrieval")
    async def compare(body: Practice, request: Request) -> dict[str, Any]:
        envelope = context(body, request)
        if assembly.retrieval is None:
            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Explicit Retrieval MCP required", 501)
        # This new model-consuming path uses the same durable gate as /converse.
        budget = request.app.state.budget_factory()
        request.state.budget = budget
        token = request_execution.set(envelope)
        started = perf_counter()
        try:
            async with asyncio.timeout(budget.remaining_seconds()):
                routes = await assembly.retrieval.compare(
                    body.text, assembly.scope, budget, top_k=3
                )
            receipt = next(
                r
                for r in reversed(assembly.retrieval.diagnostics)
                if r["request_id"] == envelope.request_id
            )
            return {
                "query": body.text,
                "routes": {k: v.model_dump(mode="json") for k, v in routes.items()},
                "elapsed_ms": (perf_counter() - started) * 1000,
                "trace_id": envelope.trace_id,
                "receipt": receipt,
                "code": [
                    "adapters/mcp/retrieval/retrieval_mcp.py:RemoteRetriever.compare",
                    "application/hybrid.py:HybridRetriever.compare / result_for",
                ],
                "budget_used": budget.usage().model_dump(mode="json"),
            }
        finally:
            request_execution.reset(token)

    @router.get("/memory")
    async def memory(request: Request, session_id: Identifier) -> dict[str, Any]:
        identity = auth(request)
        if assembly.ledger is None:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Memory runtime unavailable", 503)
        # An absent/foreign session must fail before reading projections.
        await PostgresHistory(assembly.ledger.pool).messages(
            identity, session_id, offset=0, limit=1
        )
        window = await assembly.ledger.snapshot(identity, session_id)
        memory_service = request.app.state.resources.conversation.memory
        cache = memory_service.cache
        projection: dict[str, Any] = {"status": "disabled", "model_calls": 0}
        if cache is not None:
            try:
                async with asyncio.timeout(5):
                    key = cache.key(identity, session_id, window.generation)
                    raw, ttl = await cache.client.get(key), await cache.client.ttl(key)
                projection = {
                    "status": "present" if raw else "absent",
                    "ttl_seconds": ttl,
                    "generation": window.generation,
                    "matches_authority": json.loads(raw) == window.model_dump(mode="json")
                    if raw
                    else None,
                }
            except RedisError, TimeoutError:
                projection = {"status": "unavailable"}
        async with assembly.ledger.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT o.event_id,o.question_id,o.version,o.done,o.attempts,o.last_error "
                "FROM dh_m10_outbox o JOIN dh_m08_questions q ON q.question_id=o.question_id "
                "WHERE q.tenant_id=%s AND q.user_id=%s AND q.session_id=%s "
                "ORDER BY o.event_id DESC LIMIT 50",
                (identity.tenant_id, identity.user_id, session_id),
            )
            rows = await cursor.fetchall()
            await conn.rollback()
        return {
            "authority": "PostgreSQL",
            "window": window.model_dump(mode="json"),
            "redis": projection,
            "outbox": [
                dict(
                    zip(
                        ("event_id", "question_id", "version", "done", "attempts", "last_error"),
                        r,
                        strict=True,
                    )
                )
                for r in rows
            ],
            "milvus": {
                "configured": assembly.event_index is not None,
                "reading": "no similarity search on browse; outbox shows projection completion",
            },
            "model_calls": 0,
            "code": [
                "application/memory.py:MemoryService.load",
                "adapters/persistence/cases.py:PostgresCaseRepository.snapshot",
                "application/memory.py:ProjectionWorker.once",
            ],
        }

    return router
