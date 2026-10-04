"""Explicit complete release preparation and exact content validation."""

import argparse
import asyncio
import json
from pathlib import Path

from deephelp_app.adapters.release_assets import release_hash
from deephelp_app.adapters.release_store import ReleaseStore
from deephelp_app.adapters.runtime_assets import read_data
from deephelp_app.application.dense import atomic_json
from deephelp_app.application.sop_governance import SOPRegistry, bundled_registry
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.bootstrap.mvp_runtime import BudgetSession
from deephelp_app.domain.errors import ConfigurationError
from deephelp_tools.evaluation.release_prepare import prepare_manifest


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
        )
        atomic_json(output, manifest.model_dump(mode="json"))
        print(json.dumps(dict(release_hash=release_hash(manifest), output=str(output))))
        return 0
    if not args.live:
        raise ConfigurationError("Content validation requires --live and a shared budget")
    store = await ReleaseStore.open(Path.cwd())
    gate = BudgetSession(local_path(args.budget_state))
    try:
        gate.open()
        registry = (
            SOPRegistry.model_validate(read_data(Path(args.sop)))
            if args.sop
            else bundled_registry()
        )
        from deephelp_tools.evaluation.flywheel_validation import validate

        report = await validate(
            store,
            local_path(args.assets),
            local_path(args.output),
            gate,
            Path(args.providers),
            local_path(args.auth),
            registry=registry,
            persist_assets_validation=False,
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
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--budget-state", default=".local/m18-release/budget.json")
    parser.add_argument("--auth", default=".local/m17/auth.json")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
