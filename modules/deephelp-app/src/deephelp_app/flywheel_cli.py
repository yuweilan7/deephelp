"""Manual synthetic feedback review, three-route build and explicit demonstration rollback."""

import argparse
import asyncio
import json
import secrets
from pathlib import Path

import uvicorn

from deephelp_app.dense import atomic_json
from deephelp_app.domain.models import IntentCode
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.flywheel import ROUTES, RouteReview
from deephelp_app.flywheel_assets import activate, assembly, build, verify_files, verify_remote
from deephelp_app.flywheel_assets import read_data as load_json
from deephelp_app.flywheel_store import FeedbackStore
from deephelp_app.local_paths import local_path
from deephelp_app.mvp_runtime import BudgetSession, LocalAuth, live_app, validate_control_paths


async def run(args: argparse.Namespace) -> int:
    budget_path, key_path = local_path(args.budget_state), local_path(args.redaction_key)
    if args.command == "init":
        validate_control_paths([budget_path, key_path])
        if budget_path.exists() or key_path.exists():
            raise ConfigurationError("Existing feedback budget/key cannot be reset")
        atomic_json(
            budget_path,
            dict(
                max_calls=args.max_calls,
                max_tokens=args.max_tokens,
                max_cost_cny=args.max_cost,
                attempts=0,
                tokens=0,
                charged_tokens=0,
                cost_upper_cny="0",
                uncertain_attempts=0,
            ),
        )
        key_path.write_bytes(secrets.token_bytes(32))
        print("Initialized independent feedback budget and stable redaction key")
        return 0
    if not args.live:
        raise ConfigurationError("MySQL/assets/model operations require explicit --live")
    store = await FeedbackStore.open(Path.cwd())
    result: dict[str, object] = {}
    gate = None
    try:
        if args.command == "migrate":
            await store.migrate()
            result = {"status": "READY"}
        elif args.command == "capture":
            row = await store.capture(
                args.run_id, key_path.read_bytes(), args.source_group, args.variant_group
            )
            result = row.model_dump(mode="json")
        elif args.command == "show":
            row = await store.get(args.candidate_id)
            result = dict(
                candidate=row.model_dump(mode="json"),
                derivatives=await store.lineage(args.candidate_id),
                tasks=await store.tasks(args.candidate_id),
            )
        elif args.command in {"review", "withdraw"}:
            review = RouteReview(
                state=args.decision,
                label=IntentCode(args.label) if args.label else None,
                reviewer=args.reviewer,
                method=args.method,
                reason=args.reason,
                phrases=tuple(args.phrase),
                exclusions=tuple(args.exclude),
                independent_from_heldout=args.independent_from_heldout,
            )
            row = await store.review(
                args.candidate_id,
                args.revision,
                args.route if args.command == "review" else None,
                review if args.command == "review" else None,
                reviewer=args.reviewer,
                reason=args.reason,
            )
            result = row.model_dump(mode="json")
        else:
            gate = BudgetSession(budget_path)
            gate.open()
            assets = local_path(args.assets)
            pointer = local_path(args.pointer)
            if args.command == "build":
                ids = args.build_candidate
                if not ids and not args.baseline_only:
                    raise ConfigurationError(
                        "Build needs explicit approved candidate IDs or --baseline-only"
                    )
                result = await build(
                    store,
                    ids,
                    local_path(args.output),
                    args.version,
                    gate,
                    Path(args.providers),
                    local_path(args.fasttext_pointer),
                )
            elif args.command == "validate":
                from deephelp_app.evaluation.flywheel_validation import validate

                result = await validate(
                    store,
                    assets,
                    local_path(args.output),
                    gate,
                    Path(args.providers),
                    local_path(args.auth),
                    baseline=local_path(args.baseline) if args.baseline else None,
                )
            elif args.command == "verify":
                data = verify_files(assets)
                await store.require_current(data["source"])
                await verify_remote(data, gate)
                result = dict(status="PASS", version=data["version"])
            elif args.command in {"activate", "rollback"}:
                if args.command == "rollback":
                    old = load_json(pointer)
                    if not old.get("previous"):
                        raise ConfigurationError("No previous complete demo version")
                    assets = Path(old["previous"])
                await activate(store, assets, pointer, gate)
                result = load_json(pointer)
            else:
                data = verify_files(Path(load_json(pointer)["active"]), validation_inputs=False)
                await store.require_current(data["source"])
                app = live_app(
                    assembly(data, Path(args.providers), "dh_m10_events_m18_demo"),
                    LocalAuth(local_path(args.auth)),
                    gate,
                    trace_path=str(local_path(args.trace_path)),
                )
                await uvicorn.Server(
                    uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
                ).serve()
                result = dict(status="STOPPED")
        if args.command == "validate":
            print(
                json.dumps(
                    dict(status=result["status"], checks=result["checks"]), ensure_ascii=False
                )
            )
            return 0 if result["status"] == "PASS" else 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        if gate:
            gate.close()
        await store.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "init",
            "migrate",
            "capture",
            "show",
            "review",
            "withdraw",
            "build",
            "validate",
            "verify",
            "activate",
            "rollback",
            "serve",
        ),
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--candidate-id")
    parser.add_argument("--source-group", default="")
    parser.add_argument("--variant-group", default="")
    parser.add_argument("--revision", type=int, default=0)
    parser.add_argument("--route", choices=ROUTES)
    parser.add_argument(
        "--decision", choices=("approved", "rejected", "disputed"), default="approved"
    )
    parser.add_argument("--label", choices=[c.value for c in IntentCode if c.actionable])
    parser.add_argument("--method", choices=("human", "deterministic_evidence"), default="human")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--phrase", action="append", default=[])
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--independent-from-heldout", action="store_true")
    parser.add_argument("--build-candidate", action="append", default=[])
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--version", default="")
    parser.add_argument("--output", default=".local/m18/validation-report.json")
    parser.add_argument("--assets", default=".local/m18/assets-v1/assets.json")
    parser.add_argument("--pointer", default=".local/m18/active.json")
    parser.add_argument("--fasttext-pointer", default=".local/m13/active.json")
    parser.add_argument("--baseline")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--auth", default=".local/m17/auth.json")
    parser.add_argument("--budget-state", default=".local/m18/session-budget.json")
    parser.add_argument("--redaction-key", default=".local/m18/redaction.key")
    parser.add_argument("--trace-path", default=".local/m18/trace.jsonl")
    parser.add_argument("--max-calls", type=int, default=1800)
    parser.add_argument("--max-tokens", type=int, default=4000000)
    parser.add_argument("--max-cost", default="30")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (ConfigurationError, AppError, ValueError, OSError) as exc:
        print(
            json.dumps(
                dict(status="FAILED", error=type(exc).__name__, message=str(exc)),
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
