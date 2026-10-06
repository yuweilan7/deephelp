"""Explicit complete release preparation and exact content validation."""

# 显式准备完整发布及精确内容验证，服务启动仍走app原发布入口。
#
# 命令先检查指定候选/配置/报告，不自动切换版本或复跑历史全量评测。

import argparse
import asyncio
import json
from pathlib import Path

from deephelp_app.adapters.releases.release_assets import release_hash
from deephelp_app.adapters.releases.release_store import ReleaseStore
from deephelp_app.adapters.releases.runtime_assets import read_data
from deephelp_app.application.dense import atomic_json
from deephelp_app.application.sop_governance import SOPRegistry, bundled_registry
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.bootstrap.runtime.budget import BudgetSession
from deephelp_app.domain.errors import ConfigurationError
from deephelp_tools.evaluation.releases.release_prepare import prepare_manifest


async def run(args: argparse.Namespace) -> int:
    if args.command == "prepare":
        output = local_path(args.output)
        if output.exists():
            raise ConfigurationError("A complete manifest is immutable; choose a new path")
        registry = (
            SOPRegistry.model_validate(read_data(Path(args.sop)))
            if args.sop
            else bundled_registry()
        )
        manifest = prepare_manifest(
            local_path(args.assets),
            Path(args.providers),
            registry,
            args.version,
            validation_path=local_path(args.validation) if args.validation else None,
            prompt_artifact=local_path(args.prompt_artifact) if args.prompt_artifact else None,
            quality_review=local_path(args.quality_review) if args.quality_review else None,
            skill_review=local_path(args.skill_review) if args.skill_review else None,
            sop_review=local_path(args.sop_review) if args.sop_review else None,
            memory_report=local_path(args.memory_report) if args.memory_report else None,
        )
        atomic_json(output, manifest.model_dump(mode="json"))
        print(json.dumps(dict(release_hash=release_hash(manifest), output=str(output))))
        return 0
    if not args.live:
        raise ConfigurationError("Content validation requires --live and a shared budget")
    gate = BudgetSession(local_path(args.budget_state))
    store = await ReleaseStore.open(Path.cwd())
    try:
        gate.open()
        registry = (
            SOPRegistry.model_validate(read_data(Path(args.sop)))
            if args.sop
            else bundled_registry()
        )
        from deephelp_tools.evaluation.feedback.flywheel_validation import validate

        report = await validate(
            store,
            local_path(args.assets),
            local_path(args.output),
            gate,
            Path(args.providers),
            local_path(args.auth),
            registry=registry,
            persist_assets_validation=False,
            prompt_artifact=local_path(args.prompt_artifact) if args.prompt_artifact else None,
            skill_review=local_path(args.skill_review) if args.skill_review else None,
            sop_review=local_path(args.sop_review) if args.sop_review else None,
        )
        print(json.dumps(dict(status=report["status"], output=args.output)))
        return 0 if report["status"] == "PASS" else 1
    finally:
        gate.close()
        await store.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "validate"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--assets", default=".local/m18/assets-v1/assets.json")
    parser.add_argument("--output", default=".local/m18-release/manifest.json")
    parser.add_argument("--version", default="m18-complete-v1")
    parser.add_argument("--sop")
    parser.add_argument("--validation")
    parser.add_argument("--prompt-artifact")
    parser.add_argument(
        "--quality-review", help="Reviewed independent improvement closure; extra gate only"
    )
    parser.add_argument("--skill-review")
    parser.add_argument(
        "--memory-report", help="Exact M46 policy/schema and fixed lifecycle content"
    )
    parser.add_argument(
        "--sop-review", help="Immutable approved SOP artifact for exact content gate"
    )
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--budget-state", default=".local/m18-release/budget.json")
    parser.add_argument("--auth", default=".local/m17/auth.json")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
