"""独立配对实验CLI：freeze零模型，run沿原gateway与一个累计预算writer。

仅本命令创建HTTP会话并负责关闭；合同文件不可覆写，完成结果恢复零调用。
独立holdout的freeze另需审核合同，不接受临时扩大样本/自动换模型。
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from deephelp_app.adapters.model_gateway.providers import (
    EndpointConfig,
    ProviderConfig,
    create_gateway,
    endpoint_credentials,
)
from deephelp_app.adapters.releases.asset_integrity import file_digest
from deephelp_app.application.corpus import digest
from deephelp_app.application.intent_prompt import default_system
from deephelp_app.bootstrap.connections import read_env
from deephelp_app.bootstrap.runtime.budget import BudgetSession
from deephelp_app.domain.execution import AsyncCalls
from deephelp_tools.evaluation.quality.paired import Experiment, Variant, selected
from deephelp_tools.evaluation.quality.paired_runtime import execute, read_contract, source_identity
from deephelp_tools.evaluation.retrieval.corpus_candidate import immutable


def snapshot(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, Any] = value.get("frozen", value)
    return result


def freeze_contract(args: argparse.Namespace) -> dict[str, Any]:
    """先锁定测试语义/输入集合/角色配置，freeze本身不连接模型或向量库。"""
    data = snapshot(args.dataset)
    binding = json.loads(args.binding.read_text(encoding="utf-8"))
    paths = dict(
        root=str(args.root.resolve()),
        dataset=str(args.dataset.resolve()),
        providers=str(args.providers.resolve()),
        judge=str(args.judge.resolve()),
        pointer=str(args.pointer.resolve()),
        corpus=str(Path(binding["corpus"]).resolve()),
        policy=str(Path(binding["cascade_policy"]).resolve()),
        binding=str(args.binding.resolve()),
    )
    prompt = default_system("complaints-v3")
    candidate = args.prompt.read_text(encoding="utf-8") if args.prompt else prompt
    config = ProviderConfig.load(args.providers)
    judge = EndpointConfig.model_validate_json(args.judge.read_text(encoding="utf-8"))
    env = read_env(args.root / ".env.local")
    if args.split == "holdout":
        from deephelp_tools.evaluation.quality.improvement import verify_review

        value = json.loads(args.review.read_text(encoding="utf-8"))
        verify_review(value)
        if value["review_hash"] != args.review_hash or value["owner"] != {
            "tenant_id": args.tenant,
            "user_id": args.user,
        }:
            raise ValueError("Holdout review owner/hash differs")
        paths.update(
            review=str(args.review.resolve()),
            postgres=str(args.postgres.resolve()),
            tenant=args.tenant,
            user=args.user,
        )
    spec = Experiment(
        dataset_id=data["dataset_id"],
        freeze_hash=data["freeze_hash"],
        split=args.split,
        factor="fallback_prompt" if args.prompt else "retrieval_route",
        baseline=Variant(route="hybrid", system_prompt=prompt),
        candidate=Variant(route="hybrid" if args.prompt else "dense", system_prompt=candidate),
        selection_hash=digest(selected(data, args.split)),
        files={
            p: file_digest(Path(p)) for k, p in paths.items() if k not in {"root", "tenant", "user"}
        },
        source=source_identity(),
        endpoint_hashes={
            "embedding": digest(endpoint_credentials(config.embedding, env)[0]),
            "classifier": digest(endpoint_credentials(judge, env)[0]),
        },
        review_hash=args.review_hash,
    )
    spec.require_factor()
    value = dict(
        experiment_fingerprint=spec.fingerprint, contract=spec.model_dump(mode="json"), paths=paths
    )
    immutable(args.contract, json.dumps(value, ensure_ascii=False, sort_keys=True).encode())
    return dict(
        experiment_fingerprint=spec.fingerprint,
        split=spec.split,
        cases=len(selected(data, args.split)),
        target=spec.target,
        model_calls=0,
    )


async def run_contract(
    contract: Path, directory: Path, budget_path: Path, *, retry_uncertain: bool = False
) -> dict[str, Any]:
    """CLI拥有唯一BudgetSession；finally关闭锁，不复位已经预留/消耗的额度。"""
    _, paths = read_contract(contract)
    config = ProviderConfig.load(Path(paths["providers"]))
    judge = EndpointConfig.model_validate_json(
        await asyncio.to_thread(Path(paths["judge"]).read_text, encoding="utf-8")
    )
    gate = BudgetSession(budget_path)
    gate.open()
    try:
        spec, _ = read_contract(contract)
        if spec.split == "holdout":
            from deephelp_app.adapters.persistence.postgres import PostgresConfig, PostgresPool
            from deephelp_app.domain.models.common import VerifiedIdentity
            from deephelp_tools.evaluation.quality.improvement_store import ImprovementStore
            from deephelp_tools.evaluation.quality.independent_store import IndependentDatasets
            from deephelp_tools.evaluation.quality.paired_runtime import validate_contract

            owner = VerifiedIdentity(tenant_id=paths["tenant"], user_id=paths["user"])
            pool = await PostgresPool.open(PostgresConfig.read(Path(paths["postgres"])))
            try:
                frozen = (await IndependentDatasets(pool).get(owner, spec.dataset_id))["frozen"]
                validate_contract(spec, frozen, repair_path=directory / "repair.json")
                await ImprovementStore(pool).claim(owner, spec)
            finally:
                await pool.aclose()
        async with httpx.AsyncClient(timeout=90) as client:
            env = read_env(Path(paths["root"]) / ".env.local")
            embedding = create_gateway(client, config, AsyncCalls(1, 60), env=env)
            classifier = create_gateway(
                client, config.model_copy(update={"chat": judge}), AsyncCalls(1, 60), env=env
            )
            return await execute(
                contract,
                snapshot(Path(paths["dataset"])),
                directory,
                embedding,
                classifier,
                gate,
                retry_uncertain=retry_uncertain,
            )
    finally:
        gate.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="M45 frozen single-factor paired classification")
    parser.add_argument("action", choices=("freeze", "run"))
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--budget", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--providers", type=Path)
    parser.add_argument("--judge", type=Path)
    parser.add_argument("--pointer", type=Path)
    parser.add_argument("--binding", type=Path)
    parser.add_argument("--split", choices=("dev", "holdout"), default="dev")
    parser.add_argument("--prompt", type=Path)
    parser.add_argument("--review-hash")
    parser.add_argument("--review", type=Path)
    parser.add_argument("--postgres", type=Path)
    parser.add_argument("--tenant")
    parser.add_argument("--user")
    parser.add_argument("--retry-uncertain", action="store_true")
    args = parser.parse_args()
    if args.action == "freeze":
        if args.split == "holdout" and not all(
            (args.review, args.review_hash, args.postgres, args.tenant, args.user, args.prompt)
        ):
            parser.error("holdout freeze requires review/hash/PG/owner/revised prompt")
        if not all((args.dataset, args.providers, args.judge, args.pointer, args.binding)):
            parser.error("freeze requires dataset/providers/judge/pointer/binding")
        result = freeze_contract(args)
    else:
        if args.directory is None or args.budget is None:
            parser.error("run requires directory and original budget")
        value = asyncio.run(
            run_contract(
                args.contract, args.directory, args.budget, retry_uncertain=args.retry_uncertain
            )
        )
        result = {
            k: value[k] for k in ("status", "experiment_fingerprint", "routes", "paired", "usage")
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
