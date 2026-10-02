"""M05 offline preview / explicitly enabled real ingestion and candidate evaluation."""

import argparse
import asyncio
import json
import platform
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

import httpx

from deephelp_app.corpus import CorpusPreview, frozen_preview, read_corpus
from deephelp_app.dense import (
    DenseImporter,
    DenseRetriever,
    atomic_json,
    evaluate_dev,
    load_json,
    verify_manifest_rows,
)
from deephelp_app.domain.models import DenseScope, ErrorCode
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.live_probe import local_path
from deephelp_app.milvus_dense import (
    MilvusDenseStore,
    SSHCapacity,
    activate,
    create_client,
    rollback,
)
from deephelp_app.providers import ProviderConfig, create_gateway


class LostAcknowledgementStore(MilvusDenseStore):
    """Real upsert, then ONE synthetic client acknowledgement loss; no server failure injected."""

    failed = False

    async def write(self, scope: DenseScope, rows: list[dict[str, object]]) -> None:
        await super().write(scope, rows)
        if not self.failed:
            self.failed = True
            raise AppError(ErrorCode.TIMEOUT, "Synthetic lost acknowledgement after real upsert")


def control_paths(values: list[str]) -> list[Path]:
    paths = [local_path(v) for v in values]
    reserved: set[Path] = set()
    for path in paths:
        owned = {path, path.with_suffix(".lock"), path.with_suffix(path.suffix + ".tmp")}
        if reserved & owned:
            raise ConfigurationError(
                "Budget/output/manifest/pointer and their lock paths must differ"
            )
        reserved.update(owned)
    return paths


async def run_live(args: argparse.Namespace, preview: CorpusPreview) -> int:
    state_path, manifest, pointer, output = control_paths(
        [args.budget_state, args.manifest, args.pointer, args.output]
    )
    original = load_json(state_path)
    lock_path = state_path.with_suffix(".lock")
    try:
        lock = lock_path.open("x", encoding="utf-8")
    except FileExistsError:
        raise ConfigurationError("Live budget locked; inspect previous writer") from None
    report: dict[str, object] = {
        "command": args.command,
        "status": "PENDING",
        "preview": preview.summary(),
        "environment": {"python": platform.python_version(), "pymilvus": version("pymilvus")},
    }
    budget: ExecutionBudget | None = None
    try:
        budget = ExecutionBudget.start(
            args.timeout,
            int(str(original["max_calls"])) - int(str(original["attempts"])),
            0,
            token_limit=int(str(original["max_tokens"])) - int(str(original["charged_tokens"])),
            cost_limit=Decimal(str(original["max_cost_cny"]))
            - Decimal(str(original["cost_upper_cny"])),
        )

        def persist() -> None:
            assert budget is not None
            state = dict(original)
            state.update(
                attempts=int(str(original["attempts"])) + budget.attempts_used,
                tokens=int(str(original["tokens"])) + budget.tokens_used,
                charged_tokens=int(str(original["charged_tokens"])) + budget.token_upper_bound,
                cost_upper_cny=str(
                    Decimal(str(original["cost_upper_cny"])) + budget.cost_upper_bound
                ),
                uncertain_attempts=int(str(original.get("uncertain_attempts", 0)))
                + budget.uncertain_attempts,
                stage=args.command,
            )
            atomic_json(state_path, state)

        budget.on_change = persist
        config = ProviderConfig.load(Path(args.providers))
        scope = DenseScope(
            namespace=args.namespace,
            dataset_version=args.dataset_version,
            signature=config.signature(),
        )
        report["scope"] = scope.model_dump(mode="json")
        report["collection"] = scope.collection_name
        capacity = SSHCapacity(Path.cwd())
        report["capacity"] = capacity.observations
        async with httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0),
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        ) as http:
            gateway = create_gateway(http, config, AsyncCalls(1, 30))
            client = create_client(Path.cwd())
            store = MilvusDenseStore(client, budget)
            retriever = DenseRetriever(gateway, store)
            try:
                async with asyncio.timeout(budget.remaining_seconds()):
                    if args.command in {"import", "accept"}:
                        if args.command == "accept" and not manifest.is_file():  # noqa: ASYNC240
                            lossy = LostAcknowledgementStore(client, budget)
                            try:
                                await DenseImporter(
                                    gateway, lossy, capacity, batch_size=2
                                ).import_corpus(preview, scope, budget, manifest)
                            except AppError as exc:
                                if not lossy.failed or exc.code != ErrorCode.TIMEOUT:
                                    raise
                                report["lost_ack_remote_rows"] = await store.count(scope)
                                report["lost_ack_completed_ids"] = load_json(manifest)[
                                    "completed_ids"
                                ]
                        state = await DenseImporter(gateway, store, capacity).import_corpus(
                            preview, scope, budget, manifest
                        )
                        report["import_status"] = state["status"]
                        report["row_count"] = await store.count(scope, filtered=False)
                        if args.command == "accept":
                            before = budget.tokens_used
                            await DenseImporter(gateway, store, capacity).import_corpus(
                                preview, scope, budget, manifest
                            )
                            report["repeat_row_count"] = await store.count(scope, filtered=False)
                            report["repeat_embedding_tokens"] = budget.tokens_used - before
                            assert (
                                report["row_count"]
                                == report["repeat_row_count"]
                                == len(preview.index_records)
                            )
                            assert report["repeat_embedding_tokens"] == 0
                            report["evaluation"] = await evaluate_dev(
                                preview, scope, retriever, budget
                            )
                            await activate(store, scope, manifest, pointer)
                            report["activation"] = "VERIFIED"
                    elif args.command == "evaluate":
                        await store.validate(scope, preview.index_digest)
                        report["evaluation"] = await evaluate_dev(preview, scope, retriever, budget)
                    elif args.command == "search":
                        await store.validate(scope, preview.index_digest)
                        result = await retriever.retrieve(
                            args.query, scope, budget, top_k=args.top_k
                        )
                        report["retrieval"] = result.model_dump(mode="json")
                    elif args.command == "verify":
                        state = load_json(manifest)
                        if (
                            state.get("status") != "VERIFIED"
                            or state.get("scope") != scope.model_dump(mode="json")
                            or state.get("corpus_digest") != preview.index_digest
                            or state.get("file_sha256") != preview.file_sha256
                        ):
                            raise ConfigurationError(
                                "Verification requires a complete bound manifest"
                            )
                        ids = state.get("completed_ids")
                        if not isinstance(ids, list):
                            raise ConfigurationError("Invalid completed IDs")
                        before = budget.tokens_used
                        await store.validate(scope, preview.index_digest)
                        rows = await store.read(scope, ids)
                        verify_manifest_rows(state, rows)
                        report["row_count"] = await store.count(scope, filtered=False)
                        assert report["row_count"] == len(preview.index_records)
                        report["reconnect_persistence"] = "VERIFIED"
                        report["server_restart"] = "NOT_RUN"
                        report["embedding_tokens"] = budget.tokens_used - before
                        result = await retriever.retrieve(
                            "优惠券不能用，帮我查下原因。", scope, budget
                        )
                        assert result.candidates[0].intent_code.value == "COUPON_UNUSABLE"
                        report["retrieval"] = result.model_dump(mode="json")
                    elif args.command == "activate":
                        await activate(store, scope, manifest, pointer)
                    elif args.command == "rollback":
                        report["rolled_back"] = (await rollback(store, pointer)).model_dump(
                            mode="json"
                        )
                    elif args.command == "delete":
                        if not args.allow_delete_synthetic:
                            raise ConfigurationError(
                                "Deletion requires explicit synthetic maintenance authorization"
                            )
                        await store.delete_synthetic(scope, args.doc_id)
                        report["deleted_ids"] = args.doc_id
                    report["server_version"] = await store._rpc(
                        lambda: client.get_server_version(timeout=None, retry_times=0)
                    )
                    report["status"] = "PASS"
            finally:
                await client.close()
                report["diagnostics"] = gateway.diagnostics
    except (AppError, ConfigurationError, ValueError, TimeoutError, OSError) as exc:
        report["status"] = "FAIL"
        report["error"] = {
            "type": type(exc).__name__,
            "code": exc.code.value if isinstance(exc, AppError) else None,
            "request_id": exc.provider_request_id if isinstance(exc, AppError) else None,
            "provider_code": exc.provider_code if isinstance(exc, AppError) else None,
        }
    finally:
        if budget is not None:
            report["usage"] = budget.usage().model_dump(mode="json")
        atomic_json(output, report)
        lock.close()
        lock_path.unlink()
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("status", "command", "collection", "row_count", "error")
                if k in report
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "command",
        choices=(
            "preview",
            "import",
            "accept",
            "search",
            "evaluate",
            "verify",
            "activate",
            "rollback",
            "delete",
        ),
    )
    result.add_argument("--live", action="store_true")
    result.add_argument(
        "--source",
        type=Path,
        help="UTF-8 JSONL/CSV or mapped headerless XLSX; default M02 synthetic corpus",
    )
    result.add_argument(
        "--columns", type=Path, help="JSON mapping from field name to XLSX column letter"
    )
    result.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    result.add_argument("--namespace", default="m05_synthetic")
    result.add_argument("--dataset-version", default="m05-smoke-v1")
    result.add_argument("--manifest", default=".local/m05/import.json")
    result.add_argument("--pointer", default=".local/m05/active.json")
    result.add_argument("--budget-state", default=".local/m05/session-budget.json")
    result.add_argument("--output", default=".local/m05/report.json")
    result.add_argument("--timeout", type=float, default=300)
    result.add_argument("--query", default="优惠券不能用，帮我查下原因。")
    result.add_argument("--top-k", type=int, default=3)
    result.add_argument("--allow-delete-synthetic", action="store_true")
    result.add_argument("--doc-id", action="append", default=[])
    return result


def main() -> None:
    args = parser().parse_args()
    columns = json.loads(args.columns.read_text(encoding="utf-8")) if args.columns else None
    preview = read_corpus(args.source, columns=columns) if args.source else frozen_preview()
    if args.command == "preview":
        print(json.dumps(preview.summary(), ensure_ascii=False, indent=2))
        raise SystemExit(int(bool(preview.errors)))
    preview.require_valid()
    if not args.live:
        raise ConfigurationError("Remote M05 commands require explicit --live")
    raise SystemExit(asyncio.run(run_live(args, preview)))


if __name__ == "__main__":
    main()
