"""Explicit M31 frozen-candidate dev selection and held-out acceptance, resumable per action."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from deephelp_app.adapters.milvus_dense import create_client
from deephelp_app.adapters.milvus_hybrid import MilvusHybridStore
from deephelp_app.adapters.providers import ProviderConfig, create_gateway
from deephelp_app.application.corpus import digest
from deephelp_app.application.dense import atomic_json, load_json
from deephelp_app.application.hybrid import ANALYZER, HybridRetriever, rerank_result, result_for
from deephelp_app.application.retrieval_policy import validate_published_selection
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.bootstrap.mvp_runtime import BudgetSession, validate_control_paths
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.execution import AsyncCalls
from deephelp_app.domain.models.retrieval import DenseResult, HybridScope, RerankPolicy
from deephelp_tools.evaluation.hybrid_eval import (
    RetrievalQuery,
    classify,
    dataset,
    query_digest,
    query_text,
    ranking,
)

SEMANTIC = "Retrieve semantically similar text."
OPTIONS = {
    f"n{n}-{label}": RerankPolicy(candidate_budget=n, top_n=n, instruct=instruct)
    for n in (6, 12)
    for label, instruct in (("semantic", SEMANTIC), ("default", None))
}


def observation(q: RetrievalQuery, result: DenseResult) -> dict[str, Any]:
    return {
        **ranking(q, result),
        "recall_n": int(q.gold is not None and any(h.intent_code == q.gold for h in result.hits)),
        "candidate_ids": [h.doc_id for h in result.hits],
        "candidate_hash": digest(
            sorted((h.model_dump(mode="json") for h in result.hits), key=lambda h: h["doc_id"])
        ),
        "server_elapsed_ms": result.elapsed_ms or 0,
    }


def summarize(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    selected = [row["metrics"][key] for row in rows]
    labelled = [s for s in selected if s["eligible"]]
    metrics: dict[str, Any] = {"queries": len(selected), "labelled": len(labelled)}
    for name in ("recall_n", "recall_1", "recall_3", "candidate_recall_1", "mrr"):
        metrics[name] = sum(s[name] for s in labelled) / len(labelled) if labelled else None
    times = sorted(s["server_elapsed_ms"] for s in selected)
    metrics["latency_p50_ms"] = times[len(times) // 2] if times else None
    metrics["latency_max_ms"] = max(times, default=None)
    metrics["top1_failures"] = [
        r["query_id"]
        for r in rows
        if r["metrics"][key]["eligible"] and not r["metrics"][key]["candidate_recall_1"]
    ]
    return metrics


def choose(dev: dict[str, dict[str, Any]]) -> str:
    # Predetermined objective; smaller bounded set, semantic instruction break exact ties.
    return max(
        OPTIONS,
        key=lambda key: (
            dev[key]["candidate_recall_1"],
            dev[key]["mrr"],
            -OPTIONS[key].candidate_budget,
            OPTIONS[key].instruct == SEMANTIC,
        ),
    )


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        raise ConfigurationError("Rerank comparison requires explicit --live")
    corpus, queries = dataset()
    paths = {
        name: local_path(getattr(args, name))
        for name in ("pointer", "selection", "output", "budget_state")
    }
    validate_control_paths(
        [paths[n] for n in ("pointer", "budget_state")] + [Path(args.providers)],
        output=paths["output"],
    )
    pointer = load_json(paths["pointer"])
    scope = HybridScope.model_validate(pointer["active"])
    config = ProviderConfig.load(Path(args.providers))
    if config.rerank is None or config.signature() != scope.signature:
        raise ConfigurationError("Rerank configuration/embedding signature mismatch")
    weight = float(str(pointer["dense_weight"]))
    split = "dev" if args.command == "tune" else "test"
    selection = None if split == "dev" else load_json(paths["selection"])
    if selection:
        validate_published_selection(selection, scope, corpus.index_digest, 3)
        if selection["dev_digest"] != query_digest(queries["dev"]) or selection[
            "test_digest"
        ] != query_digest(queries["test"]):
            raise ConfigurationError("Frozen split digest mismatch")
    policies = (
        OPTIONS
        if split == "dev"
        else {
            "selected": RerankPolicy.model_validate(selection["rerank_policy"])  # type: ignore[index]
        }
    )
    identity = {
        "format": "m31-comparison-v1",
        "split": split,
        "scope": scope.model_dump(mode="json"),
        "index_digest": corpus.index_digest,
        "query_digest": query_digest(queries[split]),
        "weight": weight,
        "top_k": 3,
        "policies": {key: p.model_dump(mode="json") for key, p in policies.items()},
        "provider_digest": digest(config.model_dump(mode="json")),
        "candidate_source": "prefix of the same native max-12 recall; one query vector",
    }
    report: dict[str, Any] = (
        load_json(paths["output"])
        if paths["output"].exists()
        else {"identity": identity, "status": "IN_PROGRESS", "observations": []}
    )
    if report["identity"] != identity or report["status"] == "PASS":
        raise ConfigurationError("Resume identity mismatch or already completed report")
    records_dir = paths["output"].with_suffix(".records")
    records_dir.mkdir(exist_ok=True)

    def save_row(row: dict[str, Any]) -> None:
        atomic_json(records_dir / (row["query_id"] + ".json"), row)
        compact = {k: v for k, v in row.items() if k not in {"recall", "results"}}
        compact["record"] = str(records_dir / (row["query_id"] + ".json"))
        report["observations"] = [
            compact if r["query_id"] == row["query_id"] else r for r in report["observations"]
        ]
        atomic_json(paths["output"], report)

    # Migrate bounded old in-progress rows without retransmitting successful model calls.
    for previous in list(report["observations"]):
        if "recall" in previous:
            save_row(previous)
    atomic_json(paths["output"], report)
    session = BudgetSession(paths["budget_state"])
    session.open()
    client = create_client(Path.cwd())
    try:
        async with (
            asyncio.timeout(args.timeout),
            httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(retries=0), timeout=45, trust_env=False
            ) as http,
        ):
            gateway = create_gateway(http, config, AsyncCalls(1, 45))
            store = MilvusHybridStore(client, session.request())
            await store.validate(scope, corpus.index_digest)
            for q in queries[split]:
                row = next((r for r in report["observations"] if r["query_id"] == q.query_id), None)
                record_path = records_dir / (q.query_id + ".json")
                if record_path.exists():
                    row = load_json(record_path)
                if row is None:
                    row = {
                        "query_id": q.query_id,
                        "category": q.category,
                        "metrics": {},
                        "results": {},
                        "classification": {},
                    }
                    report["observations"].append(row)
                run_budget = session.request()
                if "recall" not in row:
                    store.budget = run_budget
                    routes = await HybridRetriever(
                        gateway, store, dense_weight=weight, candidate_budget=12
                    ).compare(query_text(q), scope, run_budget, top_k=3)
                    row["recall"] = {k: r.model_dump(mode="json") for k, r in routes.items()}
                    save_row(row)
                for n in sorted({p.candidate_budget for p in policies.values()}):
                    for mode in ("dense", "bm25", "hybrid"):
                        key = f"{mode}-n{n}"
                        recalled = DenseResult.model_validate(row["recall"][mode])
                        base = result_for(
                            scope,
                            mode,
                            list(recalled.hits[:n]),
                            n,
                            weight,
                            recalled.elapsed_ms or 0,
                        )
                        row["metrics"][key] = observation(q, base)
                        if mode == "hybrid":
                            row["results"][key] = base.model_dump(mode="json")
                for key, policy in policies.items():
                    if key not in row["results"]:
                        base = DenseResult.model_validate(
                            row["results"][f"hybrid-n{policy.candidate_budget}"]
                        )
                        ranked = await rerank_result(
                            query_text(q), base, gateway, policy, run_budget, top_k=3
                        )
                        row["results"][key] = ranked.model_dump(mode="json")
                        row["metrics"][key] = observation(q, ranked)
                        save_row(row)
                if split == "test" and q.gold is None:
                    policy = policies["selected"]
                    for key in (f"hybrid-n{policy.candidate_budget}", "selected"):
                        if key not in row["classification"]:
                            row["classification"][key] = await classify(
                                q,
                                DenseResult.model_validate(row["results"][key]),
                                gateway,
                                run_budget,
                            )
                            save_row(row)
                save_row(row)
                print(
                    json.dumps(
                        {"query": q.query_id, "split": split, "completed": len(row["results"])}
                    ),
                    flush=True,
                )
            keys = sorted(report["observations"][0]["metrics"])
            report["metrics"] = {key: summarize(report["observations"], key) for key in keys}
            if split == "dev":
                selected_key = choose(report["metrics"])
                policy = policies[selected_key]
                selected = {
                    "format": "m31-dev-selection-v1",
                    "scope": scope.model_dump(mode="json"),
                    "index_digest": corpus.index_digest,
                    "dev_digest": query_digest(queries["dev"]),
                    "test_digest": query_digest(queries["test"]),
                    "dense_weight": weight,
                    "candidate_budget": policy.candidate_budget,
                    "top_k": 3,
                    "analyzer": ANALYZER,
                    "rerank_policy": policy.model_dump(mode="json"),
                    "rerank_fingerprint": policy.fingerprint,
                    "objective": "intent_hit1,mrr,lower_N,semantic_tie",
                    "default_activation_gate": (
                        "dev improves and test does not regress; rejection preserved"
                    ),
                }
                selected["selection_digest"] = digest(selected)
                if paths["selection"].exists() and load_json(paths["selection"]) != selected:
                    raise ConfigurationError("Frozen dev selection cannot be replaced")
                atomic_json(paths["selection"], selected)
                report["selected"] = selected_key
            else:
                rejected = [r for r in report["observations"] if r["classification"]]
                report["rejection"] = {
                    key: {
                        "queries": len(rejected),
                        "correct": sum(
                            r["classification"][key]["classification_correct"] for r in rejected
                        ),
                    }
                    for key in rejected[0]["classification"]
                }
            report["status"] = "PASS"
            report["budget"] = session.state
            atomic_json(paths["output"], report)
    except BaseException as exc:
        report["status"] = "INTERRUPTED" if isinstance(exc, asyncio.CancelledError) else "FAILED"
        report["error_type"] = type(exc).__name__
        report["budget"] = session.state
        atomic_json(paths["output"], report)
        raise
    finally:
        await client.close()
        session.close()
    print(
        json.dumps({"status": report["status"], "metrics": report["metrics"]}, ensure_ascii=False)
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("tune", "evaluate"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--providers", default=".local/providers.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--selection", default=".local/m31/selection.json")
    parser.add_argument("--budget-state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=1500)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
