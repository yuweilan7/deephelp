"""Rebuildable Redis memory projection; never the fact source."""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from deephelp_app.adapters.milvus_dense import local_connection
from deephelp_app.domain.models import MemoryWindow, VerifiedIdentity


def scope_key(identity: VerifiedIdentity, session: str) -> str:
    return hashlib.sha256(
        json.dumps([identity.tenant_id, identity.user_id, session], ensure_ascii=False).encode()
    ).hexdigest()


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
