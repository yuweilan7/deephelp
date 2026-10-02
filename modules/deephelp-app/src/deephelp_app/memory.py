"""Bounded memory; MySQL authorizes every read, Redis/Milvus are disposable projections."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from deephelp_app.cases import CLOSED, MySQLCaseRepository, summary
from deephelp_app.domain.models import CaseSummary, MemoryWindow, Question, VerifiedIdentity
from deephelp_app.execution import ExecutionBudget
from deephelp_app.milvus_dense import local_connection
from deephelp_app.ports import CaseRepository


def scope_key(identity: VerifiedIdentity, session: str) -> str:
    return hashlib.sha256(
        json.dumps([identity.tenant_id, identity.user_id, session], ensure_ascii=False).encode()
    ).hexdigest()


class EventIndex(Protocol):
    async def put(self, question: Question, budget: ExecutionBudget) -> None: ...
    async def search(
        self, identity: VerifiedIdentity, session: str, query: str, budget: ExecutionBudget
    ) -> list[CaseSummary]: ...


class RedisMemory:
    def __init__(self, client: Any, *, namespace: str = "deephelp:m10", ttl: int = 300) -> None:
        if not namespace.startswith("deephelp:m10") or min(ttl, len(namespace)) <= 0:
            raise ValueError("M10 namespace and positive TTL required")
        self.client, self.namespace, self.ttl = client, namespace, ttl

    @classmethod
    def open(cls, root: Path, **kwargs: Any) -> RedisMemory:
        c = local_connection(root)
        return cls(
            Redis(
                host=c["REDIS_HOST"],
                port=int(c["REDIS_PORT"]),
                username=c["REDIS_USER"],
                password=c["REDIS_PASSWORD"],
                decode_responses=True,
                socket_connect_timeout=3,
                socket_timeout=3,
            ),
            **kwargs,
        )

    def key(self, identity: VerifiedIdentity, session: str, generation: int) -> str:
        return f"{self.namespace}:{scope_key(identity, session)}:g{generation}"

    async def put(self, identity: VerifiedIdentity, session: str, window: MemoryWindow) -> None:
        key = self.key(identity, session, window.generation)
        keywords = {
            q.question_id: [f"{e.name.value}={e.value}" for e in q.entities]
            for q in window.active_questions
        }
        async with asyncio.timeout(5):
            # Immutable generation keys make delayed worker writes harmless; no latest pointer.
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.set(key, window.model_dump_json(), ex=self.ttl)
                pipe.set(key + ":keywords", json.dumps(keywords, ensure_ascii=False), ex=self.ttl)
                await pipe.execute()

    async def aclose(self) -> None:
        await self.client.aclose()


class MemoryService:
    def __init__(
        self,
        repository: CaseRepository,
        cache: RedisMemory | None = None,
        index: EventIndex | None = None,
    ) -> None:
        self.repository, self.cache, self.index = repository, cache, index
        self.last_cache_error: str | None = None

    async def load(
        self,
        identity: VerifiedIdentity,
        session: str,
        budget: ExecutionBudget,
        *,
        query: str | None = None,
    ) -> MemoryWindow:
        budget.remaining_seconds()
        # A consistent bounded DB snapshot is the authority, including TTL/time trimming.
        window = await self.repository.snapshot(identity, session)
        if self.index and query:
            candidates = await self.index.search(identity, session, query, budget)
            verified = []
            for candidate in candidates:
                current = await self.repository.question(identity, session, candidate.question_id)
                if (
                    current
                    and current.status in CLOSED
                    and current.version == candidate.version
                    and current.status == candidate.status
                ):
                    verified.append(summary(current))
            window = window.model_copy(update={"closed_summaries": tuple(verified)})
        if self.cache:
            try:
                await self.cache.put(identity, session, window)
                self.last_cache_error = None
            except RedisError, TimeoutError:
                # Reportable degradation; original facts and request acceptance remain MySQL owned.
                self.last_cache_error = "redis_unavailable"
        return window


class ProjectionWorker:
    def __init__(
        self, repository: MySQLCaseRepository, memory: MemoryService, index: EventIndex
    ) -> None:
        self.repository, self.memory, self.index = repository, memory, index

    async def once(self, budget: ExecutionBudget, *, event_id: int | None = None) -> str:
        claim = await self.repository.claim_event(event_id=event_id)
        if claim is None:
            return "idle"
        eid, token, question = claim
        try:
            async with asyncio.timeout(min(90, budget.remaining_seconds())):
                await self.index.put(question, budget)
                await self.memory.load(question.identity, question.session_id, budget)
                if self.memory.last_cache_error:
                    raise RedisError("Redis projection unavailable")
            if not await self.repository.acknowledge(eid, token):
                return "lease_lost"
            return "projected"
        except asyncio.CancelledError:
            # Lease expiry permits restart recovery; never acknowledge an uncertain write.
            raise
        except Exception as exc:
            await self.repository.acknowledge(eid, token, error=type(exc).__name__[:64])
            raise

    async def drain(self, budget: ExecutionBudget, *, limit: int = 100) -> int:
        if limit <= 0:
            raise ValueError("Positive worker limit required")
        completed = 0
        for _ in range(limit):
            result = await self.once(budget)
            if result == "idle":
                break
            if result == "projected":
                completed += 1
        return completed
