"""显式迁移/审计才读取的旧typed checkpoint归档。

只保留aget_tuple/alist读取，删除拒绝；原业务源码不再用它写新的图进度。
"""

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, get_checkpoint_id

from deephelp_app.adapters.approval.checkpoints import configuration
from deephelp_app.adapters.observability.telemetry import annotate, observed
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode


class LegacyCheckpointArchive(BaseCheckpointSaver[int]):
    def __init__(self, pool: Any) -> None:
        super().__init__()
        self.pool = pool

    @observed("checkpoint.read")
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        c = config["configurable"]
        thread, ns, cid = c["thread_id"], c.get("checkpoint_ns", ""), get_checkpoint_id(config)
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                await cursor.execute(
                    "SELECT checkpoint_id,parent_id,checkpoint_type,checkpoint,"
                    "metadata_type,metadata "
                    "FROM m25_legacy.dh_m15_checkpoints WHERE thread_id=%s AND checkpoint_ns=%s "
                    + ("AND checkpoint_id=%s" if cid else "ORDER BY checkpoint_id DESC LIMIT 1"),
                    (thread, ns, cid) if cid else (thread, ns),
                )
                row = await cursor.fetchone()
                if not row:
                    return None
                cid, parent, ct, cp, mt, md = row
                await cursor.execute(
                    "SELECT task_id,channel,value_type,value FROM "
                    "m25_legacy.dh_m15_checkpoint_writes "
                    "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s "
                    "ORDER BY task_id,write_index",
                    (thread, ns, cid),
                )
                writes = await cursor.fetchall()
                annotate(
                    **{
                        "deephelp.checkpoint_id": cid,
                        "deephelp.parent_checkpoint_id": parent or "none",
                        "deephelp.pending_writes": len(writes),
                    }
                )
                return CheckpointTuple(
                    config=configuration(thread, ns, cid),
                    checkpoint=self.serde.loads_typed((ct, cp)),
                    metadata=self.serde.loads_typed((mt, md)),
                    parent_config=configuration(thread, ns, parent) if parent else None,
                    pending_writes=[
                        (t, ch, self.serde.loads_typed((vt, v))) for t, ch, vt, v in writes
                    ],
                )
            finally:
                await conn.rollback()

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        terms: list[str] = []
        args: list[Any] = []
        if config:
            c = config["configurable"]
            terms += ["thread_id=%s", "checkpoint_ns=%s"]
            args += [c["thread_id"], c.get("checkpoint_ns", "")]
            if cid := get_checkpoint_id(config):
                terms.append("checkpoint_id=%s")
                args.append(cid)
        if before:
            terms.append("checkpoint_id<%s")
            args.append(get_checkpoint_id(before))
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT thread_id,checkpoint_ns,checkpoint_id FROM m25_legacy.dh_m15_checkpoints "
                + ("WHERE " + " AND ".join(terms) if terms else "")
                + " ORDER BY checkpoint_id DESC",
                args,
            )
            rows = await cursor.fetchall()
            await conn.rollback()
        count = 0
        for thread, ns, cid in rows:
            if limit is not None and count >= limit:
                break
            item = await self.aget_tuple(configuration(thread, ns, cid))
            if item and all(item.metadata.get(k) == v for k, v in (filter or {}).items()):
                yield item
                count += 1

    async def adelete_thread(self, thread_id: str) -> None:
        raise AppError(ErrorCode.NOT_IMPLEMENTED, "Legacy checkpoint archive is read-only", 501)
