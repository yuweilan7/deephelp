"""M09 reproducible native retrieval and dev/test comparison. Default preview is offline."""

import argparse
import asyncio
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from deephelp_app.dense import DenseImporter, atomic_json, load_json, verify_manifest_rows
from deephelp_app.domain.models import HybridScope
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.evaluation.hybrid_eval import (
    choose_weight,
    classify,
    dataset,
    metrics,
    query_digest,
    query_text,
    ranking,
    validate_selection,
)
from deephelp_app.execution import AsyncCalls
from deephelp_app.hybrid import ANALYZER, HybridRetriever
from deephelp_app.local_paths import local_path
from deephelp_app.milvus_dense import SSHCapacity
from deephelp_app.milvus_hybrid import MilvusHybridStore, publish_hybrid
from deephelp_app.mvp_runtime import BudgetSession, validate_control_paths
from deephelp_app.providers import ProviderConfig, create_gateway


async def run(args: argparse.Namespace) -> int:
    if args.command == "init":
        state_path = local_path(args.budget_state)
        if state_path.exists():
            raise ConfigurationError("Existing cumulative budget cannot be reset")
        atomic_json(
            state_path,
            {
                "max_calls": args.max_calls,
                "max_tokens": args.max_tokens,
                "max_cost_cny": args.max_cost,
                "attempts": 0,
                "tokens": 0,
                "charged_tokens": 0,
                "cost_upper_cny": "0",
                "uncertain_attempts": 0,
            },
        )
        print(json.dumps({"status": "INITIALIZED", "budget": str(state_path)}))
        return 0
    corpus, queries = dataset()
    if args.command == "preview":
        print(
            json.dumps(
                {
                    "corpus": corpus.summary(),
                    "queries": {s: len(q) for s, q in queries.items()},
                    "analyzer": ANALYZER,
                },
                ensure_ascii=False,
            )
        )
        return 0
    if not args.live:
        raise ConfigurationError("This command requires --live and a local cumulative budget")
    from deephelp_app.milvus_dense import create_client

    paths = [
        local_path(p) for p in (args.budget_state, args.manifest, args.pointer, args.selection)
    ]
    output = local_path(args.output)
    if output.exists():
        raise ConfigurationError("Use a new report path to preserve prior evidence")
    validate_control_paths(paths + [Path(args.providers)], output=output)
    budget_path, manifest, pointer, selection_path = paths
    config = ProviderConfig.load(Path(args.providers))
    scope = HybridScope(
        namespace="m09_synthetic",
        dataset_version=args.dataset_version,
        signature=config.signature(),
        index_kind="intent_hybrid",
    )
    report: dict[str, Any] = {
        "status": "PENDING",
        "command": args.command,
        "scope": scope.model_dump(mode="json"),
        "index_digest": corpus.index_digest,
        "query_reformulation": False,
        "candidate_budget": args.top_k,
        "analyzer": ANALYZER,
    }
    session = BudgetSession(budget_path)
    session.open()
    client = create_client(Path.cwd())
    try:
        async with (
            asyncio.timeout(args.timeout),
            httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(retries=0), timeout=30, trust_env=False
            ) as http,
        ):
            gateway = create_gateway(http, config, AsyncCalls(1, 30))
            store = MilvusHybridStore(client, session.request())
            if args.command == "import":
                report["import"] = await DenseImporter(
                    gateway, store, SSHCapacity(Path.cwd())
                ).import_corpus(corpus, scope, store.budget, manifest)
                report["rows"] = await store.count(scope, filtered=False)
            elif args.command == "activate":
                selected = load_json(selection_path)
                validate_selection(selected, scope, corpus.index_digest, queries, args.top_k)
                await publish_hybrid(store, scope, manifest, pointer, selected)
                report["active"] = load_json(pointer)
            elif args.command == "rollback":
                value = load_json(pointer)
                previous = value.get("previous")
                if not isinstance(previous, dict):
                    raise ConfigurationError("No previous Hybrid version")
                prior = HybridScope.model_validate(previous["active"])
                validate_selection(
                    previous["selection"], prior, corpus.index_digest, queries, args.top_k
                )
                await publish_hybrid(
                    store, prior, Path(previous["manifest"]), pointer, previous["selection"]
                )
                report["active"] = load_json(pointer)
            elif args.command == "verify":
                state = load_json(manifest)
                await store.validate(scope, corpus.index_digest)
                if state["scope"] != scope.model_dump(mode="json") or state["status"] != "VERIFIED":
                    raise ConfigurationError("Verify requires this scope's verified manifest")
                ids = state["completed_ids"]
                if not isinstance(ids, list):
                    raise ConfigurationError("Invalid verified IDs")
                rows = await store.read(scope, ids)
                verify_manifest_rows(state, rows)
                if await store.count(scope, filtered=False) != len(rows):
                    raise ConfigurationError("Unexpected out-of-scope records")
                report["verified_rows"] = len(rows)
            elif args.command == "delete":
                if not args.allow_delete_synthetic or not args.doc_id:
                    raise ConfigurationError("Explicit synthetic deletion flag and doc-id required")
                await store.delete_synthetic(scope, [args.doc_id])
                report["deleted"] = args.doc_id
                report["routes_after_delete"] = {
                    m: r.model_dump(mode="json")
                    for m, r in (
                        await HybridRetriever(gateway, store).compare(
                            args.query, scope, store.budget, top_k=args.top_k
                        )
                    ).items()
                }
                if any(
                    h["doc_id"] == args.doc_id
                    for r in report["routes_after_delete"].values()
                    for h in r["hits"]
                ):
                    raise ConfigurationError("Deleted ID remained in search")
            elif args.command in {"analyze", "compare"}:
                await store.validate(scope, corpus.index_digest)
                report["tokens"] = await store.analyze([args.query], scope)
                if args.command == "compare":
                    report["routes"] = {
                        m: r.model_dump(mode="json")
                        for m, r in (
                            await HybridRetriever(
                                gateway, store, dense_weight=args.dense_weight
                            ).compare(args.query, scope, store.budget, top_k=args.top_k)
                        ).items()
                    }
                    report["server_latency_ms"] = store.latencies
            elif args.command in {"tune", "evaluate"}:
                selected = None
                weights = [0.25, 0.5, 0.75] if args.command == "tune" else []
                split = "dev" if args.command == "tune" else "test"
                if split == "test":
                    selected = load_json(selection_path)
                    validate_selection(selected, scope, corpus.index_digest, queries, args.top_k)
                    weights = [float(str(selected["dense_weight"]))]
                by_weight: dict[float, dict[str, Any]] = {}
                for weight in weights:
                    observations = []
                    for q in queries[split]:
                        store = MilvusHybridStore(client, session.request())
                        routes = await HybridRetriever(gateway, store, dense_weight=weight).compare(
                            query_text(q), scope, store.budget, top_k=args.top_k
                        )
                        observation: dict[str, Any] = {
                            "query_id": q.query_id,
                            "category": q.category,
                            "query_text": query_text(q),
                            "gold": q.gold.value if q.gold else None,
                            "routes": {},
                        }
                        for mode, result in routes.items():
                            info = {
                                **ranking(q, result),
                                "result": result.model_dump(mode="json"),
                                "server_elapsed_ms": store.latencies[mode],
                            }
                            if args.classify and split == "test":
                                info.update(await classify(q, result, gateway, session.request()))
                            observation["routes"][mode] = info
                        observations.append(observation)
                        report["progress"] = {
                            "weight": weight,
                            "completed": len(observations),
                            "total": len(queries[split]),
                        }
                        report["observations_in_progress"] = observations
                        atomic_json(output, report)
                    by_weight[weight] = {
                        "metrics": {
                            m: metrics(observations, m) for m in ("dense", "bm25", "hybrid")
                        },
                        "per_category": {
                            c: {
                                m: metrics([o for o in observations if o["category"] == c], m)
                                for m in ("dense", "bm25", "hybrid")
                            }
                            for c in sorted({q.category for q in queries[split]})
                        },
                        "per_intent": {
                            code: {
                                m: metrics([o for o in observations if o["gold"] == code], m)
                                for m in ("dense", "bm25", "hybrid")
                            }
                            for code in sorted({q.gold.value for q in queries[split] if q.gold})
                        },
                        "observations": observations,
                    }
                report.update(
                    split=split,
                    query_digest=query_digest(queries[split]),
                    by_weight=by_weight,
                    selection=selected,
                )
                report.pop("observations_in_progress", None)
                if split == "dev":
                    weight = choose_weight(
                        {w: v["metrics"]["hybrid"] for w, v in by_weight.items()}
                    )
                    selected = {
                        "format": "m09-dev-selection-v1",
                        "scope": scope.model_dump(mode="json"),
                        "index_digest": corpus.index_digest,
                        "dev_digest": query_digest(queries["dev"]),
                        "test_digest": query_digest(queries["test"]),
                        "candidate_budget": args.top_k,
                        "dense_weight": weight,
                        "objective": "candidate_recall_1,mrr,nearest_0.5,lower_weight",
                        "analyzer": ANALYZER,
                    }
                    from deephelp_app.corpus import digest

                    selected["selection_digest"] = digest(selected)
                    if selection_path.exists() and load_json(selection_path) != selected:
                        raise ConfigurationError(
                            "Frozen selection cannot be overwritten; use a new version"
                        )
                    atomic_json(selection_path, selected)
                    report["selection"] = selected
            report["status"] = "PASS"
    except Exception as exc:
        report.update(
            status="FAIL",
            error_code=exc.code.value if isinstance(exc, AppError) else type(exc).__name__,
        )
        raise
    finally:
        await client.close()
        session.close()
        report["cumulative_budget"] = session.state
        atomic_json(output, report)
    print(
        json.dumps(
            {"status": report["status"], "command": args.command, "report": str(output)},
            ensure_ascii=False,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "preview",
            "init",
            "import",
            "analyze",
            "compare",
            "tune",
            "evaluate",
            "verify",
            "activate",
            "rollback",
            "delete",
        ],
        nargs="?",
        default="preview",
    )
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--classify",
        action="store_true",
        help="test routes each call the same 600 schema classifier",
    )
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--budget-state", default=".local/m09/session-budget.json")
    parser.add_argument("--max-calls", type=int)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--max-cost")
    parser.add_argument("--manifest", default=".local/m09/import.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--selection", default=".local/m09/selection.json")
    parser.add_argument("--output", default=".local/m09/report.json")
    parser.add_argument("--dataset-version", default="m09-ab-v1")
    parser.add_argument("--query", default="优惠券不能使用，订单00123457")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--dense-weight", type=float, default=0.5)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--doc-id")
    parser.add_argument("--allow-delete-synthetic", action="store_true")
    args = parser.parse_args()
    if args.command == "init" and (
        args.max_calls is None
        or args.max_calls < 1
        or args.max_tokens is None
        or args.max_tokens < 1
        or args.max_cost is None
    ):
        parser.error("init requires positive max-calls, max-tokens and max-cost")
    if args.command == "init":
        try:
            cost = Decimal(args.max_cost)
            if not cost.is_finite() or cost <= 0:
                raise ValueError
        except ValueError, ArithmeticError:
            parser.error("max-cost must be a positive finite decimal")
    if not 1 <= args.top_k <= 20 or not 0 <= args.dense_weight <= 1 or args.timeout <= 0:
        parser.error("top-k 1..20, weight 0..1 and positive timeout required")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
