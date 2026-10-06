"""把PG审核快照投影到独立Hybrid集合，复用原导入、检索与发布指针守卫。

工作台唯一LearningJobs拥有任务，gateway/累计预算借自应用；本入口只拥有Milvus客户端。
冻结资料、split和已完成观察持久保留；取消关闭客户端，恢复不重生成已确认的导入向量。
candidate pointer仅供候选诊断/后续完整发布，不能替换应用active/previous。
"""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deephelp_app.adapters.mcp.retrieval.retrieval_protocol import RetrievalBinding, load_binding
from deephelp_app.adapters.retrieval.milvus_dense import create_client
from deephelp_app.adapters.retrieval.milvus_hybrid import MilvusHybridStore, publish_hybrid
from deephelp_app.application.corpus import digest
from deephelp_app.application.corpus_authoring import corpus_bytes, verify_snapshot
from deephelp_app.application.dense import DenseImporter, atomic_json, load_json, vector_hash
from deephelp_app.application.hybrid import ANALYZER, HybridRetriever
from deephelp_app.application.retrieval_policy import validate_published_selection
from deephelp_app.bootstrap.runtime.assembly import LiveAssembly
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.retrieval import HybridScope
from deephelp_tools.evaluation.retrieval.hybrid_eval import (
    dataset,
    metrics,
    query_digest,
    query_text,
    ranking,
    validate_selection,
)
from deephelp_tools.probes.retrieval.capacity import SSHCapacity


def immutable(path: Path, data: bytes) -> None:
    """同一候选只认首次冻结字节；已存在时精确核对，不能借恢复覆盖原件。"""
    if path.exists():
        if path.read_bytes() != data:
            raise ConfigurationError("Frozen candidate bytes changed; use a new version")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            import os

            os.fsync(stream.fileno())


def freeze_files(root: Path, snapshot: dict[str, Any], assembly: LiveAssembly) -> Path:
    """PG审核原件决定路径/源hash，配置签名决定独立集合；浏览器不能给任意路径。"""
    verify_snapshot(snapshot)
    directory: Path = root / "corpus" / str(snapshot["version_hash"])
    immutable(
        directory / "review.json", json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode()
    )
    immutable(directory / "corpus.jsonl", corpus_bytes(snapshot))
    scope = HybridScope(
        namespace="m44_corpus",
        dataset_version="m44-" + snapshot["version_hash"][:32],
        signature=assembly.config.signature(),
        registry_version=snapshot["catalog_version"],
        index_kind="intent_hybrid",
    )
    if scope == assembly.scope:
        raise ConfigurationError("Candidate construction cannot target the active scope")
    immutable(
        directory / "scope.json", json.dumps(scope.model_dump(mode="json"), sort_keys=True).encode()
    )
    return directory


async def compare_bindings(
    assembly: LiveAssembly,
    client: Any,
    candidate: RetrievalBinding,
    query: str,
    factory: Callable[[], ExecutionBudget],
) -> dict[str, Any]:
    """两个版本共享一次真实Embedding；同签名、K、权重与Rerank配置才允许对照。

    每路仍沿原reference_matches拒绝陌生文本/来源，score_kind保持独立语义。
    内部端口接受实际EmbeddingResult，浏览器不提交向量或覆盖正式索引配置。
    """
    current = assembly.retrieval_binding
    if (
        not isinstance(current.scope, HybridScope)
        or candidate.scope.signature != current.scope.signature
        or candidate.top_k != current.top_k
        or candidate.weight != current.weight
        or candidate.rerank_policy != current.rerank_policy
    ):
        raise ConfigurationError(
            "Same-condition comparison needs matching signature/K/weight/rerank"
        )
    gateway = assembly.gateway
    if gateway is None:
        raise ConfigurationError("Original Embedding gateway unavailable")
    budget = factory()
    embedded = await gateway.embed([query], budget)
    embedding_attempts = budget.attempts_used
    results = {}
    for name, binding in (("published", current), ("candidate", candidate)):
        store = MilvusHybridStore(client, budget)
        await store.validate(binding.scope, binding.corpus_digest)
        routes = await HybridRetriever(
            gateway,
            store,
            dense_weight=binding.weight,
            reranker=gateway if binding.rerank_policy else None,
            rerank_policy=binding.rerank_policy,
            candidate_budget=binding.candidate_budget,
            records=binding.records,
            corpus_digest=binding.corpus_digest,
        ).compare_embedded(query, binding.scope, budget, embedded, top_k=binding.top_k)
        rerank = routes.get("hybrid_rerank")
        if binding.rerank_policy and (rerank is None or rerank.rerank is None):
            raise ConfigurationError("Configured Rerank observation missing")
        results[name] = dict(
            scope=binding.scope.model_dump(mode="json"),
            profile_id=binding.profile_id,
            routes={mode: value.model_dump(mode="json") for mode, value in routes.items()},
            route_elapsed_ms=dict(store.latencies),
            rerank=dict(status="not_run", reason="not_configured")
            if binding.rerank_policy is None
            else rerank.rerank.model_dump(mode="json")
            if rerank and rerank.rerank
            else None,
        )
    return dict(
        query=query,
        query_vector_sha256=vector_hash(embedded.vectors[0]),
        embedding_requests=1,
        embedding_attempts=embedding_attempts,
        embedding_usage=embedded.usage.model_dump(mode="json") if embedded.usage else None,
        usage=budget.snapshot().model_dump(mode="json"),
        results=results,
        interpretation="Retrieval evidence only; not a final intent or business fact",
    )


async def build_candidate(
    root: Path,
    snapshot: dict[str, Any],
    assembly: LiveAssembly,
    factory: Callable[[], ExecutionBudget],
    splits: dict[str, Path],
) -> dict[str, Any]:
    """先冻结独立dev/test，再建索引、完成同条件门禁并生成候选专用pointer。

    累计writer属于应用；每批/每query用原factory的剩余预算，不新建session。
    已完成query观察逐条保存，恢复跳过其模型请求；改split/正式基线/配置直接拒绝。
    """
    if set(splits) != {"dev", "test"}:
        raise ConfigurationError("Candidate build requires configured frozen dev/test")
    directory = freeze_files(root, snapshot, assembly)
    for split, source in splits.items():
        immutable(directory / (split + ".json"), source.read_bytes())
    corpus, queries = dataset(
        directory / "corpus.jsonl", {s: directory / (s + ".json") for s in splits}
    )
    # 新反馈在任何付费请求前核对标识归一后的近重复；人工独立声明不能替代泄漏守卫。
    from difflib import SequenceMatcher

    from deephelp_tools.evaluation.feedback.flywheel import neutral

    for entry in snapshot["entries"].values():
        if not entry.get("feedback"):
            continue
        text = neutral(entry["record"]["content"])
        if any(
            SequenceMatcher(None, text, neutral(q.text), autojunk=False).ratio() >= 0.9
            for rows in queries.values()
            for q in rows
        ):
            raise ConfigurationError("Feedback is a near duplicate of frozen heldout input")
    scope = HybridScope.model_validate(load_json(directory / "scope.json"))
    current = assembly.retrieval_binding
    if current.weight not in {0.25, 0.5, 0.75}:
        raise ConfigurationError("Candidate keeps an explicitly supported baseline weight")
    binding = RetrievalBinding(
        scope,
        current.top_k,
        current.weight,
        corpus.index_digest,
        corpus.index_records,
        assembly.config,
        current.rerank_policy,
    )
    contract = dict(
        version_hash=snapshot["version_hash"],
        corpus_sha256=corpus.file_sha256,
        index_digest=corpus.index_digest,
        scope=scope.model_dump(mode="json"),
        published_profile=current.profile_id,
        candidate_budget=current.candidate_budget,
        top_k=current.top_k,
        dense_weight=current.weight,
        rerank=current.rerank_policy.model_dump(mode="json") if current.rerank_policy else None,
        splits={s: query_digest(q) for s, q in queries.items()},
        weight_candidates=[current.weight],
    )
    immutable(directory / "contract.json", json.dumps(contract, sort_keys=True).encode())
    progress_path = directory / "retrieval-gate.json"
    progress: dict[str, Any] = (
        load_json(progress_path)
        if progress_path.exists()
        else dict(contract=contract, status="PENDING", observations=[])
    )
    if progress.get("contract") != contract:
        raise ConfigurationError("Candidate retrieval gate binding changed")
    client = create_client(assembly.root)
    try:
        gateway = assembly.gateway
        if gateway is None:
            raise ConfigurationError("Original Embedding gateway unavailable")
        store = MilvusHybridStore(client, factory())
        imported = await DenseImporter(gateway, store, SSHCapacity(assembly.root)).import_corpus(
            corpus, scope, store.budget, directory / "index.json"
        )
        progress["index_status"] = imported["status"]
        # 现权重在任何heldout调用前冻结；dev负责核验这唯一候选，不从test反选权重。
        for split in ("dev", "test"):
            for query in queries[split]:
                key = split + "/" + query.query_id
                if any(o["key"] == key for o in progress["observations"]):
                    continue
                value = await compare_bindings(
                    assembly, client, binding, query_text(query), factory
                )
                observation: dict[str, Any] = dict(
                    key=key,
                    split=split,
                    query_id=query.query_id,
                    gold=query.gold.value if query.gold else None,
                    comparison=value,
                    ranks={},
                )
                from deephelp_app.domain.models.retrieval import DenseResult

                for name, version in value["results"].items():
                    observation["ranks"][name] = {
                        m: {
                            **ranking(query, DenseResult.model_validate(r)),
                            "server_elapsed_ms": version["route_elapsed_ms"].get(
                                m, r["elapsed_ms"]
                            ),
                        }
                        for m, r in version["routes"].items()
                    }
                progress["observations"].append(observation)
                progress["status"] = "IN_PROGRESS"
                atomic_json(progress_path, progress)
        selection: dict[str, Any] = dict(
            format="m09-dev-selection-v1",
            scope=scope.model_dump(mode="json"),
            index_digest=corpus.index_digest,
            dev_digest=query_digest(queries["dev"]),
            test_digest=query_digest(queries["test"]),
            candidate_budget=current.candidate_budget,
            dense_weight=current.weight,
            objective="candidate_recall_1,mrr,nearest_0.5,lower_weight",
            analyzer=ANALYZER,
            weight_candidates=[current.weight],
        )
        if current.rerank_policy:
            selection.update(
                format="m31-dev-selection-v1",
                top_k=current.top_k,
                rerank_policy=current.rerank_policy.model_dump(mode="json"),
                rerank_fingerprint=current.rerank_policy.fingerprint,
                objective="candidate_recall_1,mrr,final_classification,latency,model_calls",
            )
        selection["selection_digest"] = digest(selection)
        if current.rerank_policy is None:
            validate_selection(
                selection, scope, corpus.index_digest, queries, current.candidate_budget
            )
        validate_published_selection(selection, scope, corpus.index_digest, current.top_k)
        immutable(directory / "selection.json", json.dumps(selection, sort_keys=True).encode())
        store = MilvusHybridStore(client, factory())
        await publish_hybrid(
            store, scope, directory / "index.json", directory / "pointer.json", selection
        )
        # 汇总保留改善/退化和未知gold；无分类调用，不能用召回率替代分类门禁。
        progress["metrics"] = {
            s: {
                n: {
                    m: metrics(
                        [
                            dict(query_id=o["query_id"], routes=o["ranks"][n])
                            for o in progress["observations"]
                            if o["split"] == s
                        ],
                        m,
                    )
                    for m in ("dense", "bm25", "hybrid")
                }
                for n in ("published", "candidate")
            }
            for s in queries
        }
        progress.update(status="PASS", classification="not_run", active_changed=False)
        atomic_json(progress_path, progress)
        return dict(
            evidence_status="PASS",
            version_hash=snapshot["version_hash"],
            scope=scope.model_dump(mode="json"),
            corpus_sha256=corpus.file_sha256,
            index_digest=corpus.index_digest,
            collection=scope.collection_name,
            index_rows=len(corpus.index_records),
            metrics=progress["metrics"],
            active_changed=False,
        )
    except BaseException as exc:
        progress.update(
            status="CANCELLED" if isinstance(exc, asyncio.CancelledError) else "PARTIAL",
            error_code=type(exc).__name__,
        )
        atomic_json(progress_path, progress)
        raise
    finally:
        await client.close()


async def compare_candidate(
    root: Path,
    snapshot: dict[str, Any],
    assembly: LiveAssembly,
    query: str,
    factory: Callable[[], ExecutionBudget],
) -> dict[str, Any]:
    """明确比较已构建候选；读版本/任务不调用此函数。关闭自己创建的Milvus客户端。"""
    directory = freeze_files(root, snapshot, assembly)
    binding = load_binding(
        load_json(directory / "pointer.json"), assembly.config, directory / "corpus.jsonl"
    )
    client = create_client(assembly.root)
    try:
        value = await compare_bindings(assembly, client, binding, query, factory)
        return dict(evidence_status="PASS", version_hash=snapshot["version_hash"], **value)
    finally:
        await client.close()
