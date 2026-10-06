"""显式接收/盲审/冻结独立数据；来源文件私有，输出精确收据，模型0次。

本入口拥有所打开的PG池并finally关闭；浏览器借用原池。无postgres参数时仅
演示文件审核，不声称PG持久协议。恢复从原dataset_id/revision续做，不覆写已冻金标。
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.adapters.persistence.postgres import PostgresConfig, PostgresPool
from deephelp_app.adapters.releases.asset_integrity import file_digest
from deephelp_app.application.corpus import digest
from deephelp_app.application.dense import atomic_json
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_tools.assets import asset_path
from deephelp_tools.evaluation.quality.core import historical_inputs
from deephelp_tools.evaluation.quality.independent_data import (
    Annotation,
    Intake,
    adjudicate,
    annotate,
    freeze,
    receive,
    summary,
    verify,
)
from deephelp_tools.evaluation.quality.independent_store import IndependentDatasets


async def verify_protocol(repository: IndependentDatasets) -> dict[str, Any]:
    """真实PG短事务竞争/回滚，固定教学动作不经模型；只清理本函数创建的精确测试行。"""
    from deephelp_tools.learning.course.independent_quality import (
        teaching_annotation,
        teaching_intake,
    )

    owner = VerifiedIdentity(tenant_id="m45-protocol", user_id=uuid4().hex)
    row = receive(teaching_intake(), owner.model_dump(mode="json"), [])
    key = row["dataset_id"]
    created = False
    try:
        row = await repository.create(owner, row)
        created = True

        async def compete(reviewer: str) -> dict[str, Any]:
            receipt = teaching_annotation(row, reviewer)
            return await repository.change(owner, key, 0, lambda r: annotate(r, receipt, {}))

        results = await asyncio.gather(compete("race-a"), compete("race-b"), return_exceptions=True)
        current = await repository.get(owner, key)
        before = digest(current)

        def fail(r: dict[str, Any]) -> dict[str, Any]:
            r["status"] = "CORRUPTED"
            raise ConfigurationError("Synthetic transaction rollback")

        try:
            await repository.change(owner, key, 1, fail)
        except ConfigurationError:
            pass
        checks = dict(
            one_cas_winner=sum(isinstance(r, dict) for r in results) == 1,
            loser_conflict=sum(isinstance(r, AppError) and r.status_code == 409 for r in results)
            == 1,
            durable_revision=current["revision"] == 1,
            failed_operation_rollback=digest(await repository.get(owner, key)) == before,
        )
        return dict(status="PASS" if all(checks.values()) else "FAIL", checks=checks, model_calls=0)
    finally:
        if created:
            async with repository.pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM dh_m45_datasets WHERE tenant_id=%s "
                    "AND user_id=%s AND dataset_id=%s",
                    (owner.tenant_id, owner.user_id, key),
                )
                await conn.commit()


def development_inputs() -> list[str]:
    """现有公开开发/评测输入全纳入holdout泄漏检查，不将旧test改称独立来源。"""
    texts = historical_inputs()
    for name in ("m17_cases.json", "m17_scale_cases.json"):
        texts.extend(
            t["text"]
            for c in json.loads(asset_path(name).read_text(encoding="utf-8"))
            for t in c["turns"]
        )
    for split in ("dev", "test"):
        texts.extend(
            q["text"]
            for q in json.loads(
                asset_path(f"m42_catalog_v3_{split}.json").read_text(encoding="utf-8")
            )
        )
    texts.extend(
        json.loads(line)["content"]
        for line in asset_path("m42_catalog_v3_corpus.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    return list(dict.fromkeys(texts))


def read(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 8_000_000:
        raise ConfigurationError("Independent source exceeds 8MB")
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return value


async def ingest(args: argparse.Namespace) -> dict[str, Any]:
    actor = VerifiedIdentity(tenant_id=args.tenant, user_id=args.user)
    body = Intake.model_validate(read(args.intake))
    historical = await asyncio.to_thread(development_inputs)
    row = await asyncio.to_thread(receive, body, actor.model_dump(mode="json"), historical)
    pool = await PostgresPool.open(PostgresConfig.read(args.postgres)) if args.postgres else None
    repository = IndependentDatasets(pool) if pool else None
    try:
        if repository is not None:
            await repository.migrate()
            row = await repository.create(actor, row)
            if args.protocol_report:
                atomic_json(args.protocol_report, await verify_protocol(repository))
        for path in args.annotation:
            annotation = Annotation.model_validate(read(path))
            if annotation.reviewer_id in row["annotations"]:
                # 回执未知时允许原字节读回，拒绝以相同reviewer覆盖原审核。
                stored = row["annotations"][annotation.reviewer_id]
                if any(stored[k] != v for k, v in annotation.model_dump(mode="json").items()):
                    raise ConfigurationError("Persisted annotation source changed")
                continue

            def apply_annotation(
                r: dict[str, Any], receipt: Annotation = annotation
            ) -> dict[str, Any]:
                return annotate(r, receipt, actor.model_dump(mode="json"))

            row = (
                await repository.change(actor, row["dataset_id"], row["revision"], apply_annotation)
                if repository
                else apply_annotation(row)
            )
        if args.adjudication and row["status"] != "FROZEN":
            resolution = Annotation.model_validate(read(args.adjudication))

            def apply_resolution(r: dict[str, Any]) -> dict[str, Any]:
                return adjudicate(r, resolution, actor.model_dump(mode="json"))

            row = (
                await repository.change(actor, row["dataset_id"], row["revision"], apply_resolution)
                if repository
                else apply_resolution(row)
            )
        if row["status"] != "FROZEN":
            row = (
                await repository.change(actor, row["dataset_id"], row["revision"], freeze)
                if repository
                else freeze(row)
            )
        verify(row["frozen"])
        if args.output.exists() and read(args.output) != row["frozen"]:
            raise ConfigurationError("Existing frozen output differs; preserve original")
        atomic_json(args.output, row["frozen"])
        reloaded = await repository.get(actor, row["dataset_id"]) if repository else row
        checks = dict(
            frozen_readback=digest(reloaded) == digest(row),
            source_immutable=True,
            historical_inputs_checked=len(historical),
            holdout_hidden_in_summary="gold" not in summary(row),
        )
        if repository:
            try:
                await repository.get(
                    VerifiedIdentity(tenant_id=args.tenant, user_id="foreign-m45"),
                    row["dataset_id"],
                )
            except AppError as exc:
                checks["foreign_owner_404"] = exc.status_code == 404
            else:
                checks["foreign_owner_404"] = False
        sources = [
            args.intake,
            *args.annotation,
            *([args.adjudication] if args.adjudication else []),
        ]
        result = dict(
            status="PASS" if all(v for v in checks.values()) else "FAIL",
            dataset=summary(row),
            checks=checks,
            pg_verified=repository is not None,
            model_calls=0,
            source_files={p.name: file_digest(p) for p in sources},
            frozen_file_sha256=file_digest(args.output),
        )
        atomic_json(args.report, result)
        return result
    finally:
        if pool:
            await pool.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent source/blind receipts; model calls 0")
    parser.add_argument("--intake", type=Path, required=True)
    parser.add_argument("--annotation", type=Path, action="append", required=True)
    parser.add_argument("--adjudication", type=Path)
    parser.add_argument("--postgres", type=Path)
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--protocol-report", type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(ingest(args)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
