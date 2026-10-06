"""独立评测聚合的PG持久库；借用原应用池，短事务CAS，不拥有模型或业务账本。

输入、两份盲标及冻结闭包随revision原子提交。异常/取消由acquire释放回滚；
任务与模型在事务外，未知响应先读回执。dataset_id不可更名，owner隔离所有查询。
"""

import json
from collections.abc import Callable
from typing import Any

from deephelp_app.domain.errors import AppError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_tools.evaluation.quality.independent_data import verify


class IndependentDatasets:
    def __init__(self, pool: Any) -> None:
        self.pool = pool

    async def migrate(self) -> None:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "CREATE TABLE IF NOT EXISTS dh_m45_datasets (tenant_id TEXT NOT NULL, "
                "user_id TEXT NOT NULL, dataset_id TEXT NOT NULL, revision INTEGER NOT NULL, "
                "body TEXT NOT NULL, PRIMARY KEY(tenant_id,user_id,dataset_id))"
            )
            await conn.commit()

    async def get(self, owner: VerifiedIdentity, dataset_id: str) -> dict[str, Any]:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m45_datasets WHERE tenant_id=%s "
                "AND user_id=%s AND dataset_id=%s",
                (owner.tenant_id, owner.user_id, dataset_id),
            )
            raw = await cursor.fetchone()
            if raw is None:
                raise AppError(ErrorCode.NOT_FOUND, "Independent dataset not found", 404)
            value: dict[str, Any] = json.loads(raw[0])
            if value["frozen"] is not None:
                verify(value["frozen"])
            return value

    async def list(self, owner: VerifiedIdentity) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m45_datasets WHERE tenant_id=%s AND user_id=%s "
                "ORDER BY dataset_id LIMIT 100",
                (owner.tenant_id, owner.user_id),
            )
            return [json.loads(raw[0]) for raw in await cursor.fetchall()]

    async def create(self, owner: VerifiedIdentity, row: dict[str, Any]) -> dict[str, Any]:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO dh_m45_datasets VALUES (%s,%s,%s,0,%s) ON CONFLICT DO NOTHING",
                (
                    owner.tenant_id,
                    owner.user_id,
                    row["dataset_id"],
                    json.dumps(row, ensure_ascii=False),
                ),
            )
            await conn.commit()
        # 同源字节重投只读原聚合，不能覆盖已标注/冻结revision。
        return await self.get(owner, row["dataset_id"])

    async def change(
        self,
        owner: VerifiedIdentity,
        dataset_id: str,
        revision: int,
        operation: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> dict[str, Any]:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            key = (owner.tenant_id, owner.user_id, dataset_id)
            await cursor.execute(
                "SELECT revision,body FROM dh_m45_datasets WHERE tenant_id=%s "
                "AND user_id=%s AND dataset_id=%s FOR UPDATE",
                key,
            )
            raw = await cursor.fetchone()
            if raw is None:
                raise AppError(ErrorCode.NOT_FOUND, "Independent dataset not found", 404)
            if raw[0] != revision:
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Independent dataset revision changed; reload", 409
                )
            row = operation(json.loads(raw[1]))
            row["revision"] = revision + 1
            await cursor.execute(
                "UPDATE dh_m45_datasets SET revision=%s,body=%s WHERE tenant_id=%s "
                "AND user_id=%s AND dataset_id=%s",
                (row["revision"], json.dumps(row, ensure_ascii=False), *key),
            )
            await conn.commit()
            return row
