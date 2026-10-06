"""PG语料作者聚合：借用原ledger池，owner+目录隔离，短事务revision CAS。

一个工作区保存最多1000记录；整体验证/导入/审核与revision一次commit。
SELECT FOR UPDATE仅覆盖CPU状态变换与写回，绝不跨模型/索引等待。
取消或校验异常由原PostgresPool.acquire finally回滚，连接池仍归装配关闭。
"""

import json
from collections.abc import Callable
from importlib.resources import files
from typing import Any

from deephelp_app.application.corpus_authoring import (
    empty_workspace,
    freeze_workspace,
    verify_snapshot,
)
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity


class PostgresCorpusWorkspaces:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def migrate(self) -> None:
        sql = files("deephelp_app").joinpath("resources/migrations/044_corpus.sql").read_text()
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            for statement in sql.split(";"):
                if statement.strip():
                    await cursor.execute(statement)
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

    async def freeze(
        self, owner: VerifiedIdentity, catalog: str, revision: int, version: str
    ) -> dict[str, Any]:
        """与作者CAS同一短事务登记不可变版本；同版本不同字节拒绝，workspace不变。"""
        key = (owner.tenant_id, owner.user_id, catalog)
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT revision,body FROM dh_m44_corpus_workspaces "
                "WHERE tenant_id=%s AND user_id=%s AND catalog_version=%s FOR UPDATE",
                key,
            )
            row = await cursor.fetchone()
            if row is None or row[0] != revision:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Corpus freeze revision changed", 409)
            snapshot = freeze_workspace(json.loads(row[1]), version, owner.model_dump(mode="json"))
            await cursor.execute(
                "SELECT body FROM dh_m44_corpus_versions WHERE "
                "tenant_id=%s AND user_id=%s AND catalog_version=%s AND version=%s",
                (*key, version),
            )
            old = await cursor.fetchone()
            if old:
                if json.loads(old[0]) != snapshot:
                    raise AppError(
                        ErrorCode.VERSION_CONFLICT, "Frozen version already differs", 409
                    )
            else:
                await cursor.execute(
                    "INSERT INTO dh_m44_corpus_versions "
                    "(version_hash,tenant_id,user_id,catalog_version,version,body) "
                    "VALUES (%s,%s,%s,%s,%s,%s)",
                    (
                        snapshot["version_hash"],
                        *key,
                        version,
                        json.dumps(snapshot, ensure_ascii=False),
                    ),
                )
            await conn.commit()
            return snapshot

    async def versions(self, owner: VerifiedIdentity, catalog: str) -> list[dict[str, Any]]:
        """只列当前owner的冻结摘要；分页作者与历史快照互不覆写。"""
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT version_hash,version,created_at FROM dh_m44_corpus_versions "
                "WHERE tenant_id=%s AND user_id=%s AND catalog_version=%s ORDER BY created_at DESC "
                "LIMIT 100",
                (owner.tenant_id, owner.user_id, catalog),
            )
            return [
                dict(version_hash=r[0], version=r[1], created_at=r[2].isoformat())
                for r in await cursor.fetchall()
            ]

    async def version(self, owner: VerifiedIdentity, catalog: str, identity: str) -> dict[str, Any]:
        """按可信owner读取原件，并复算内部归属/目录/hash；数据库行存在不是内容证明。"""
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m44_corpus_versions WHERE tenant_id=%s "
                "AND user_id=%s AND catalog_version=%s AND version_hash=%s",
                (owner.tenant_id, owner.user_id, catalog, identity),
            )
            row = await cursor.fetchone()
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, "Frozen corpus version not found", 404)
            value: dict[str, Any] = json.loads(row[0])
            verify_snapshot(value)
            if (
                value["owner"] != owner.model_dump(mode="json")
                or value["catalog_version"] != catalog
                or value["version_hash"] != identity
            ):
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Frozen corpus owner/catalog/hash differs", 409
                )
            return value
