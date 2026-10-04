"""Complete releases, compare-and-swap publication, retained versions and serving."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import uvicorn
from mcp import StdioServerParameters

from deephelp_app.adapters.release_assets import verify_complete
from deephelp_app.adapters.release_store import ReleaseStore
from deephelp_app.adapters.runtime_assets import read_data
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.bootstrap.mvp_runtime import BudgetSession, LocalAuth, live_app
from deephelp_app.bootstrap.release_runtime import ReleaseRuntime
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models import ReleaseManifest


async def run(args: argparse.Namespace) -> int:
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
        if args.command == "register":
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
            if not args.mcp_module:
                raise ConfigurationError("serve requires --mcp-module")
            runtime = ReleaseRuntime(
                args.channel,
                tool_server=StdioServerParameters(
                    command=sys.executable, args=["-m", args.mcp_module]
                ),
                corpus_path=args.corpus,
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
    parser.add_argument(
        "--mcp-module", help="Explicit stdio MCP module (demo requires deephelp-tools)"
    )
    parser.add_argument(
        "--corpus", type=Path, help="Exact published Hybrid corpus; no bundled demo fallback"
    )
    parser.add_argument("--port", type=int, default=8000)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
