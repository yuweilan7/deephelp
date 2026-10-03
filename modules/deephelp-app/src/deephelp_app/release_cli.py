"""Complete releases, compare-and-swap publication, retained versions and serving."""

import argparse
import asyncio
import json
from pathlib import Path

import uvicorn

from deephelp_app.dense import atomic_json
from deephelp_app.domain.models import ReleaseManifest
from deephelp_app.errors import ConfigurationError
from deephelp_app.flywheel_assets import read_data
from deephelp_app.flywheel_validation import validate
from deephelp_app.live_probe import local_path
from deephelp_app.mvp_runtime import BudgetSession, LocalAuth, live_app
from deephelp_app.release_assets import prepare_manifest, release_hash, verify_complete
from deephelp_app.release_runtime import ReleaseRuntime
from deephelp_app.release_store import ReleaseStore
from deephelp_app.sop_governance import SOPRegistry, bundled_registry


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
        raise ConfigurationError("Release database/service operations require --live")
    store = await ReleaseStore.open(Path.cwd())
    gate = None
    try:
        if args.command == "migrate":
            await store.migrate()
            print("Complete release tables ready")
            return 0
        if args.command == "status":
            print((await store.active(args.channel)).model_dump_json())
            return 0
        if args.command in {"references", "retire"}:
            if not args.release:
                raise ConfigurationError("An explicit release hash is required")
            if args.command == "retire":
                await store.retire(args.release)
            print(
                json.dumps(
                    dict(release=args.release, references=await store.references(args.release))
                )
            )
            return 0
        gate = BudgetSession(local_path(args.budget_state))
        gate.open()
        if args.command == "validate":
            registry = (
                SOPRegistry.model_validate(read_data(Path(args.sop)))
                if args.sop
                else bundled_registry()
            )
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
        elif args.command == "register":
            manifest = ReleaseManifest.model_validate(read_data(local_path(args.manifest)))
            await verify_complete(manifest, gate)
            print(json.dumps(dict(release_hash=await store.register(manifest))))
        elif args.command in {"activate", "rollback"}:
            pointer = await store.active(args.channel)
            target = pointer.previous if args.command == "rollback" else args.release
            if not target or args.expected_revision is None:
                raise ConfigurationError("Publication needs target and observed expected revision")
            await verify_complete(await store.manifest(target), gate)
            print(
                (await store.switch(args.channel, target, args.expected_revision)).model_dump_json()
            )
        else:
            runtime = ReleaseRuntime(
                args.channel,
                rights_port=args.rights_port,
                rights_key=local_path(args.rights_key) if args.rights_key else None,
            )
            app = live_app(
                runtime,
                LocalAuth(local_path(args.auth)),
                gate,
                trace_path=str(local_path(args.trace_path)),
            )
            await uvicorn.Server(
                uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
            ).serve()
        return 0
    finally:
        if gate:
            gate.close()
        await store.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "prepare",
            "validate",
            "migrate",
            "register",
            "status",
            "activate",
            "rollback",
            "references",
            "retire",
            "serve",
        ),
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--channel", default="m18-demo")
    parser.add_argument("--release")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--manifest", default=".local/m18-release/manifest.json")
    parser.add_argument("--assets", default=".local/m18/assets-v1/assets.json")
    parser.add_argument("--output", default=".local/m18-release/manifest.json")
    parser.add_argument("--version", default="m18-complete-v1")
    parser.add_argument("--sop")
    parser.add_argument("--validation", help="Successful exact-SOP/code/provider regression report")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--budget-state", default=".local/m18-release/budget.json")
    parser.add_argument("--auth", default=".local/m17/auth.json")
    parser.add_argument("--trace-path", default=".local/m18-release/trace.jsonl")
    parser.add_argument("--rights-port", type=int)
    parser.add_argument("--rights-key")
    parser.add_argument("--port", type=int, default=8000)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
