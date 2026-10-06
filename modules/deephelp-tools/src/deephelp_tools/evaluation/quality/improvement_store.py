"""一轮质量改进的PG证据链，按owner/review_hash隔离并用短事务revision CAS。

审核在模型调用前持久化；候选与闭环只能各前进一次。资源与网络调用在事务外，
本仓库借用原池，不关闭连接拥有者，不修改M18候选/正式发布或业务账本。
"""

import json
from typing import Any

from deephelp_app.application.corpus import digest
from deephelp_app.application.intent_prompt import fallback_contract
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models.common import ErrorCode, VerifiedIdentity
from deephelp_tools.evaluation.quality.paired import Experiment


class ImprovementStore:
    def __init__(self, pool: Any):
        self.pool = pool

    async def migrate(self) -> None:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "CREATE TABLE IF NOT EXISTS dh_m45_improvements (tenant_id TEXT NOT NULL, user_id "
                "TEXT NOT NULL, review_hash TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT "
                "NOT NULL, PRIMARY KEY(tenant_id,user_id,review_hash))"
            )
            await conn.commit()

        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "CREATE TABLE IF NOT EXISTS dh_m45_holdout_claims (tenant_id TEXT NOT NULL, "
                "user_id TEXT NOT NULL, dataset_id TEXT NOT NULL, freeze_hash TEXT NOT NULL, "
                "target TEXT NOT NULL, experiment_fingerprint TEXT NOT NULL, "
                "review_hash TEXT NOT NULL, "
                "PRIMARY KEY(tenant_id,user_id,dataset_id,freeze_hash,target))"
            )
            await conn.commit()

    async def get(self, owner: VerifiedIdentity, key: str) -> dict[str, Any]:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m45_improvements WHERE tenant_id=%s AND user_id=%s AND "
                "review_hash=%s",
                (owner.tenant_id, owner.user_id, key),
            )
            raw = await cursor.fetchone()
            if raw is None:
                raise AppError(ErrorCode.NOT_FOUND, "Quality improvement not found", 404)
            value: dict[str, Any] = json.loads(raw[0])
            return value

    async def list(self, owner: VerifiedIdentity) -> list[dict[str, Any]]:
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m45_improvements WHERE tenant_id=%s AND user_id=%s ORDER BY "
                "review_hash LIMIT 100",
                (owner.tenant_id, owner.user_id),
            )
            return [json.loads(r[0]) for r in await cursor.fetchall()]

    async def create(self, owner: VerifiedIdentity, review: dict[str, Any]) -> dict[str, Any]:
        if review["owner"] != owner.model_dump(mode="json"):
            raise ConfigurationError("Reviewed source owner differs")
        row = dict(
            review_hash=review["review_hash"],
            revision=0,
            state="REVIEWED",
            review=review,
            candidate=None,
            closure=None,
            holdout_fingerprint=None,
        )
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO dh_m45_improvements VALUES (%s,%s,%s,0,%s) ON CONFLICT DO NOTHING",
                (
                    owner.tenant_id,
                    owner.user_id,
                    row["review_hash"],
                    json.dumps(row, ensure_ascii=False),
                ),
            )
            await conn.commit()
        stored = await self.get(owner, row["review_hash"])
        if stored["review"] != review:
            raise ConfigurationError("Existing immutable review differs")
        return stored

    async def advance(
        self, owner: VerifiedIdentity, key: str, expected: int, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """review→candidate→closure顺序CAS；重复同字节只读，冲突不覆写或伪造复核。"""
        name = "closure" if "closure_hash" in payload else "candidate"
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            identity = (owner.tenant_id, owner.user_id, key)
            await cursor.execute(
                "SELECT revision,body FROM dh_m45_improvements WHERE tenant_id=%s AND user_id=%s "
                "AND review_hash=%s FOR UPDATE",
                identity,
            )
            raw = await cursor.fetchone()
            if raw is None:
                raise AppError(ErrorCode.NOT_FOUND, "Quality improvement not found", 404)
            row: dict[str, Any] = json.loads(raw[1])
            if row[name] == payload:
                return row
            if raw[0] != expected or row[name] is not None:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Improvement revision changed", 409)
            if row["state"] != ("REVISED" if name == "closure" else "REVIEWED"):
                raise ConfigurationError("Quality lineage cannot skip or repeat a phase")
            if name == "closure" and (
                payload["review_hash"] != key or payload["review"] != row["review"]
            ):
                raise ConfigurationError("Closure differs from original approved experiment")
            if name == "closure" and (
                row["holdout_fingerprint"] != payload["holdout_fingerprint"]
                or row["candidate"]["request_digest"] != payload["candidate_request_digest"]
            ):
                raise ConfigurationError("Closure does not consume the one claimed holdout")
            row.update(
                revision=expected + 1, state=payload["state"] if name == "closure" else "REVISED"
            )
            row[name] = payload
            await cursor.execute(
                "UPDATE dh_m45_improvements SET revision=%s,body=%s WHERE tenant_id=%s AND "
                "user_id=%s AND review_hash=%s",
                (row["revision"], json.dumps(row, ensure_ascii=False), *identity),
            )
            await conn.commit()
            return row

    async def claim(self, owner: VerifiedIdentity, spec: Experiment) -> dict[str, Any]:
        """派发前锁定唯一holdout合同；恢复同hash可继续，换版本/参数不可第二轮调参。"""
        async with self.pool.acquire() as conn, conn.cursor() as cursor:
            identity = (owner.tenant_id, owner.user_id, spec.review_hash)
            await cursor.execute(
                "SELECT body FROM dh_m45_improvements WHERE tenant_id=%s AND user_id=%s AND "
                "review_hash=%s FOR UPDATE",
                identity,
            )
            raw = await cursor.fetchone()
            if raw is None:
                raise AppError(ErrorCode.NOT_FOUND, "Reviewed improvement not found", 404)
            row: dict[str, Any] = json.loads(raw[0])
            if (
                spec.split != "holdout"
                or row["candidate"] is None
                or row["review"]["freeze_hash"] != spec.freeze_hash
                or row["review"]["dataset_id"] != spec.dataset_id
                or row["review"]["baseline_prompt"] != spec.baseline.system_prompt
                or row["candidate"]["request_digest"]
                != digest([spec.candidate.system_prompt, fallback_contract("complaints-v3")])
            ):
                raise ConfigurationError("Holdout does not bind reviewed original/candidate")
            if row["holdout_fingerprint"] not in {None, spec.fingerprint}:
                raise ConfigurationError("Holdout already consumed by another exact experiment")
            # 锁定到整个冻结数据/目标，而非只锁review ID；另写审核不能再次用旧holdout调参。
            scope = (owner.tenant_id, owner.user_id, spec.dataset_id, spec.freeze_hash, spec.target)
            await cursor.execute(
                "INSERT INTO dh_m45_holdout_claims VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (*scope, spec.fingerprint, spec.review_hash),
            )
            await cursor.execute(
                "SELECT experiment_fingerprint,review_hash FROM dh_m45_holdout_claims "
                "WHERE tenant_id=%s AND user_id=%s AND dataset_id=%s "
                "AND freeze_hash=%s AND target=%s",
                scope,
            )
            claimed = await cursor.fetchone()
            if claimed is None or tuple(claimed) != (spec.fingerprint, spec.review_hash):
                raise ConfigurationError(
                    "Frozen holdout already used; obtain a new independent set"
                )
            if row["holdout_fingerprint"] is None:
                row["holdout_fingerprint"] = spec.fingerprint
                row["revision"] += 1
                await cursor.execute(
                    "UPDATE dh_m45_improvements SET revision=%s,body=%s WHERE tenant_id=%s AND "
                    "user_id=%s AND review_hash=%s",
                    (row["revision"], json.dumps(row, ensure_ascii=False), *identity),
                )
            await conn.commit()
            return row
