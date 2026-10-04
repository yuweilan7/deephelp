"""Async LangGraph saver on existing MySQL; independent from the business ledger.

Stores full typed checkpoints, including channel values and pending writes. It is
an application adapter tested against the locked SDK, not an official MySQL backend.
"""

from collections.abc import AsyncIterator, Sequence
from importlib.resources import files
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)


async def migrate_m15(pool: Any) -> None:
    sql = (
        files("deephelp_app")
        .joinpath("resources/migrations/015_approval.sql")
        .read_text(encoding="utf-8")
    )
    async with pool.acquire() as conn, conn.cursor() as cursor:
        for statement in sql.split(";"):
            if statement.strip():
                await cursor.execute(statement)
        await conn.commit()


def configuration(thread: str, ns: str, checkpoint: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": checkpoint}}


class MySQLSaver(BaseCheckpointSaver[int]):
    def __init__(self, pool: Any) -> None:
        super().__init__()
        self.pool = pool

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        c = config["configurable"]
        thread, ns, cid = c["thread_id"], c.get("checkpoint_ns", ""), get_checkpoint_id(config)
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                await cursor.execute(
                    "SELECT checkpoint_id,parent_id,checkpoint_type,checkpoint,"
                    "metadata_type,metadata "
                    "FROM dh_m15_checkpoints WHERE thread_id=%s AND checkpoint_ns=%s "
                    + ("AND checkpoint_id=%s" if cid else "ORDER BY checkpoint_id DESC LIMIT 1"),
                    (thread, ns, cid) if cid else (thread, ns),
                )
                row = await cursor.fetchone()
                if not row:
                    return None
                cid, parent, ct, cp, mt, md = row
                await cursor.execute(
                    "SELECT task_id,channel,value_type,value FROM dh_m15_checkpoint_writes "
                    "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s "
                    "ORDER BY task_id,write_index",
                    (thread, ns, cid),
                )
                writes = await cursor.fetchall()
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

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        c = config["configurable"]
        thread, ns = c["thread_id"], c.get("checkpoint_ns", "")
        ct, cp = self.serde.dumps_typed(checkpoint)
        mt, md = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        if len(cp) + len(md) > 512 * 1024:
            raise ValueError("Checkpoint exceeds supported workflow state size")
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                await cursor.execute(
                    "INSERT INTO dh_m15_checkpoints VALUES (%s,%s,%s,%s,%s,UNHEX(%s),%s,UNHEX(%s)) "
                    "ON DUPLICATE KEY UPDATE checkpoint=VALUES(checkpoint),"
                    "metadata=VALUES(metadata)",
                    (
                        thread,
                        ns,
                        checkpoint["id"],
                        c.get("checkpoint_id"),
                        ct,
                        cp.hex(),
                        mt,
                        md.hex(),
                    ),
                )
                await conn.commit()
            except BaseException:
                conn.close()
                raise
        return configuration(thread, ns, checkpoint["id"])

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        c = config["configurable"]
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                for index, (channel, value) in enumerate(writes):
                    index = WRITES_IDX_MAP.get(channel, index)
                    vt, data = self.serde.dumps_typed(value)
                    sql = (
                        "INSERT INTO dh_m15_checkpoint_writes VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,UNHEX(%s),%s)"
                    )
                    sql += (
                        " ON DUPLICATE KEY UPDATE value_type=VALUES(value_type),value=VALUES(value)"
                        if index < 0
                        else " ON DUPLICATE KEY UPDATE task_id=task_id"
                    )
                    await cursor.execute(
                        sql,
                        (
                            c["thread_id"],
                            c.get("checkpoint_ns", ""),
                            c["checkpoint_id"],
                            task_id,
                            index,
                            channel,
                            vt,
                            data.hex(),
                            task_path,
                        ),
                    )
                await conn.commit()
            except BaseException:
                conn.close()
                raise

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
                "SELECT thread_id,checkpoint_ns,checkpoint_id FROM dh_m15_checkpoints "
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
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                for table in ("dh_m15_checkpoint_writes", "dh_m15_checkpoints"):
                    await cursor.execute(f"DELETE FROM {table} WHERE thread_id=%s", (thread_id,))
                await conn.commit()
            except BaseException:
                conn.close()
                raise
