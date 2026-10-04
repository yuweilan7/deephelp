"""Read historical typed checkpoints in m25_legacy; M26 owns future execution.

The old codec remains available to offline mechanism fixtures only.
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

from deephelp_app.adapters.telemetry import annotate, observed


async def migrate_m15(pool: Any) -> None:
    sql = (
        files("deephelp_app")
        .joinpath("resources/migrations/015_approval.sql")
        .read_text(encoding="utf-8")
    )
    sql += "\n;" + files("deephelp_app").joinpath(
        "resources/migrations/021_observation.sql"
    ).read_text(encoding="utf-8")
    async with pool.acquire() as conn, conn.cursor() as cursor:
        for statement in sql.split(";"):
            if statement.strip():
                await cursor.execute(statement)
        await conn.commit()


def configuration(thread: str, ns: str, checkpoint: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": checkpoint}}


class LegacyCheckpointArchive(BaseCheckpointSaver[int]):
    def __init__(self, pool: Any) -> None:
        super().__init__()
        self.pool = pool

    def require_writable(self) -> None:
        if getattr(self.pool, "backend", None) == "postgresql":
            from deephelp_app.domain.errors import AppError
            from deephelp_app.domain.models import ErrorCode

            raise AppError(ErrorCode.NOT_IMPLEMENTED, "Legacy checkpoint archive is read-only", 501)

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

    @observed("checkpoint.write")
    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        self.require_writable()
        c = config["configurable"]
        annotate(
            **{
                "deephelp.checkpoint_id": checkpoint["id"],
                "deephelp.parent_checkpoint_id": c.get("checkpoint_id", "none"),
            }
        )
        thread, ns = c["thread_id"], c.get("checkpoint_ns", "")
        ct, cp = self.serde.dumps_typed(checkpoint)
        mt, md = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        if len(cp) + len(md) > 512 * 1024:
            raise ValueError("Checkpoint exceeds supported workflow state size")
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                await cursor.execute(
                    "INSERT INTO m25_legacy.dh_m15_checkpoints VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (thread_id,checkpoint_ns,checkpoint_id) DO UPDATE SET "
                    "checkpoint=EXCLUDED.checkpoint,metadata=EXCLUDED.metadata",
                    (
                        thread,
                        ns,
                        checkpoint["id"],
                        c.get("checkpoint_id"),
                        ct,
                        cp,
                        mt,
                        md,
                    ),
                )
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
        return configuration(thread, ns, checkpoint["id"])

    @observed("checkpoint.pending_writes")
    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.require_writable()
        c = config["configurable"]
        annotate(
            **{"deephelp.checkpoint_id": c["checkpoint_id"], "deephelp.pending_writes": len(writes)}
        )
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                for index, (channel, value) in enumerate(writes):
                    index = WRITES_IDX_MAP.get(channel, index)
                    vt, data = self.serde.dumps_typed(value)
                    sql = (
                        "INSERT INTO m25_legacy.dh_m15_checkpoint_writes VALUES "
                        "(%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                    )
                    sql += (
                        " ON CONFLICT (thread_id,checkpoint_ns,checkpoint_id,task_id,write_index) "
                        "DO UPDATE SET value_type=EXCLUDED.value_type,value=EXCLUDED.value"
                        if index < 0
                        else " ON CONFLICT DO NOTHING"
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
                            data,
                            task_path,
                        ),
                    )
                await conn.commit()
            except BaseException:
                await conn.rollback()
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

    @observed("checkpoint.delete")
    async def adelete_thread(self, thread_id: str) -> None:
        self.require_writable()
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            try:
                for table in (
                    "m25_legacy.dh_m15_checkpoint_writes",
                    "m25_legacy.dh_m15_checkpoints",
                ):
                    await cursor.execute(f"DELETE FROM {table} WHERE thread_id=%s", (thread_id,))
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise
