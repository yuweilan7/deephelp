"""PG语料作者聚合：借用原ledger池，owner+目录隔离，短事务revision CAS。

一个工作区保存最多1000记录；整体验证/导入/审核与revision一次commit。
SELECT FOR UPDATE仅覆盖CPU状态变换与写回，绝不跨模型/索引等待。
取消或校验异常由原PostgresPool.acquire finally回滚，连接池仍归装配关闭。
"""

import json
from collections.abc import Callable
from importlib.resources import files
from typing import Any

from deephelp_app.application.corpus_authoring import empty_workspace
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity


class PostgresCorpusWorkspaces:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def migrate(self) -> None:
        sql = files("deephelp_app").joinpath("resources/migrations/044_corpus.sql").read_text()
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(sql)
            await conn.commit()

    async def get(self, owner: VerifiedIdentity, catalog: str) -> dict[str, Any]:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m44_corpus_workspaces "
                "WHERE tenant_id=%s AND user_id=%s AND catalog_version=%s",
                (owner.tenant_id, owner.user_id, catalog),
            )
            value = await cursor.fetchone()
            return json.loads(value[0]) if value else empty_workspace(catalog)

    async def change(
        self,
        owner: VerifiedIdentity,
        catalog: str,
        revision: int,
        operation: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        """唯一事务写入口；不存在行也通过唯一键/冲突等待串行化初次创建。

        回执和状态同事务持久化；未知响应先get对账，不自动重放批量导入。
        """
        key = (owner.tenant_id, owner.user_id, catalog)
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO dh_m44_corpus_workspaces "
                "(tenant_id,user_id,catalog_version,revision,body) VALUES (%s,%s,%s,0,%s) "
                "ON CONFLICT DO NOTHING",
                (*key, json.dumps(empty_workspace(catalog))),
            )
            await cursor.execute(
                "SELECT revision,body FROM dh_m44_corpus_workspaces "
                "WHERE tenant_id=%s AND user_id=%s AND catalog_version=%s FOR UPDATE",
                key,
            )
            value = await cursor.fetchone()
            if value is None or value[0] != revision:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Corpus revision changed; reload", 409)
            row = operation(json.loads(value[1]))
            row["revision"] = revision + 1
            await cursor.execute(
                "UPDATE dh_m44_corpus_workspaces SET revision=%s,body=%s,"
                "updated_at=CURRENT_TIMESTAMP WHERE tenant_id=%s AND user_id=%s "
                "AND catalog_version=%s",
                (row["revision"], json.dumps(row, ensure_ascii=False), *key),
            )
            await conn.commit()
            return row
