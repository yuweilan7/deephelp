"""官方AsyncPostgresSaver的生命周期、事件循环和观测接缝。

包装器不重写官方保存协议，方法转发到连接所属loop；setup只在显式迁移入口执行。
显式setup用迁移锁串行建表；运行打开只核对现有迁移版本，旧历史由原thread_id读取。
checkpoint是图进度，业务批准/UNKNOWN/效果以ApprovalRepository为准。
"""

import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from deephelp_app.adapters.observability.telemetry import annotate, observed
from deephelp_app.adapters.persistence.postgres import PoolLoop, PostgresConfig, on_pool_loop

SCHEMA = "deephelp_checkpoints"


class PostgresCheckpoints(BaseCheckpointSaver[Any]):
    """转交官方saver保存协议，复用原序列化器与版本生成接口。

    专用池使用官方autocommit/行工厂/prepare配置；Windows驱动留在Selector loop，
    图与子进程仍可用主Proactor loop。这里不另写checkpoint SQL或编码协议。
    """

    def __init__(self, saver: AsyncPostgresSaver, owner: PoolLoop | None) -> None:
        super().__init__(serde=saver.serde)
        self.official, self.owner = saver, owner

    def get_next_version(self, current: Any, channel: Any) -> Any:
        """旧导入thread的整数版本继续递增；其他版本使用官方生成器。

        若把旧整数直接改成字符串，图内部新旧版本比较会失败；不改历史versions_seen。
        """
        # Imported MySQL threads use integer versions. Switching their next
        # version to a string breaks LangGraph's old/new comparison. Retain the
        # locked base class's public integer sequence for those threads; new
        # threads use the official Postgres generator. Stored versions and
        # versions_seen are never rewritten during import.
        if type(current) is int:
            return super().get_next_version(current, channel)
        return self.official.get_next_version(current, channel)

    @observed("checkpoint.read")
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        item = await on_pool_loop(self.owner, self.official.aget_tuple(config))
        if item:
            annotate(
                **{
                    "deephelp.checkpoint_id": item.checkpoint["id"],
                    "deephelp.parent_checkpoint_id": (
                        item.parent_config["configurable"]["checkpoint_id"]
                        if item.parent_config
                        else "none"
                    ),
                    "deephelp.pending_writes": len(item.pending_writes or []),
                }
            )
        return item

    @observed("checkpoint.write")
    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        annotate(**{"deephelp.checkpoint_id": checkpoint["id"]})
        return await on_pool_loop(
            self.owner, self.official.aput(config, checkpoint, metadata, new_versions)
        )

    @observed("checkpoint.pending_writes")
    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        annotate(**{"deephelp.pending_writes": len(writes)})
        await on_pool_loop(
            self.owner, self.official.aput_writes(config, writes, task_id, task_path)
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        async def collect() -> list[CheckpointTuple]:
            return [
                item
                async for item in self.official.alist(
                    config, filter=filter, before=before, limit=limit
                )
            ]

        for item in await on_pool_loop(self.owner, collect()):
            yield item

    @observed("checkpoint.delete")
    async def adelete_thread(self, thread_id: str) -> None:
        await on_pool_loop(self.owner, self.official.adelete_thread(thread_id))


@asynccontextmanager
async def official_checkpoints(
    config: PostgresConfig,
    *,
    owner: PoolLoop | None = None,
    schema: str = SCHEMA,
    setup: bool = False,
) -> AsyncIterator[PostgresCheckpoints]:
    """拥有独立checkpoint池，返回原官方saver的loop包装器，退出必关池。

    setup只供显式迁移：autocommit适配并发索引DDL，advisory lock在finally释放。
    普通运行不自动升级库；缺表或版本不符报错，由部署/迁移入口处理。
    owner由外层借入，函数只关闭自己的池，不关闭其他业务正在用的loop。
    """
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
        raise ValueError("Invalid checkpoint schema")
    kwargs = config.connection_kwargs() | {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
    }
    kwargs["options"] += " -c search_path=" + schema
    pool = AsyncConnectionPool[AsyncConnection[Any]](
        kwargs=kwargs,
        min_size=1,
        max_size=4,
        open=False,
        timeout=5,
        reconnect_timeout=5,
    )

    async def open_saver() -> AsyncPostgresSaver:
        await pool.open(wait=True, timeout=5)
        async with pool.connection() as connection:
            if setup:
                # Serialize official migrations on this database/schema. setup
                # includes concurrent-index DDL and therefore needs autocommit.
                key = "deephelp-checkpoints:" + config.dbname + ":" + schema
                await connection.execute("SELECT pg_advisory_lock(hashtext(%s))", (key,))
                try:
                    await connection.execute(
                        sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema))
                    )
                    await AsyncPostgresSaver(connection).setup()
                finally:
                    await connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (key,))
            else:
                row = await (
                    await connection.execute(
                        "SELECT to_regclass('checkpoint_migrations') AS existing"
                    )
                ).fetchone()
                if not row or row["existing"] is None:
                    raise ValueError("Run the explicit official checkpoint setup before startup")
                version = await (
                    await connection.execute("SELECT max(v) AS version FROM checkpoint_migrations")
                ).fetchone()
                if not version or version["version"] != len(AsyncPostgresSaver.MIGRATIONS) - 1:
                    raise ValueError(
                        "Official checkpoint migrations are incomplete or incompatible"
                    )
        return AsyncPostgresSaver(pool)

    try:
        saver = await on_pool_loop(owner, open_saver())
        yield PostgresCheckpoints(saver, owner)
    finally:
        await on_pool_loop(owner, pool.close(timeout=5))
