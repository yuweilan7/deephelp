"""按身份/session/generation写可重建Redis记忆，读事实仍由PG授权。

两个键在同pipeline事务写入并带TTL；没有latest指针，迟到旧generation不会覆盖新窗口。
open只在调用时从通用装配配置创建客户端，导入Redis模块不需要Milvus SDK。
aclose由装配退出栈调用，投影写入失败由MemoryService/worker按原事实边界处理。
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from deephelp_app.bootstrap.connections import local_connection
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_app.domain.models.memory import MemoryWindow


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
            # 当前put拥有pipeline；execute将窗口与关键词一起提交，退出归还连接。
            # generation写进键名，迟到旧写不会指向新窗口；这里不能批准问题归属。
            async with self.client.pipeline(transaction=True) as pipe:
                pipe.set(key, window.model_dump_json(), ex=self.ttl)
                pipe.set(key + ":keywords", json.dumps(keywords, ensure_ascii=False), ex=self.ttl)
                await pipe.execute()

    async def aclose(self) -> None:
        await self.client.aclose()
