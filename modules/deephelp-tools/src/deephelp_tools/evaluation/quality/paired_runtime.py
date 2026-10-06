"""冻结独立实验的真实执行器，借用原分类/检索与累计预算。

合同先核对数据、配置和源码字节，后派发。每个await前记pending，返回后同步落盘；
进程退出留下UNKNOWN，明确恢复才可重新执行只读推断。已完成观察直接复用，不能抽新样本。
CLI拥有HTTP/向量客户端；工作台借用gateway但仍关闭本函数自己的Milvus客户端。
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from deephelp_app.adapters.mcp.retrieval.retrieval_protocol import load_binding
from deephelp_app.adapters.model_gateway.providers import EndpointConfig, ProviderConfig
from deephelp_app.adapters.releases.asset_integrity import file_digest
from deephelp_app.adapters.retrieval.milvus_dense import create_client
from deephelp_app.adapters.retrieval.milvus_hybrid import MilvusHybridStore
from deephelp_app.application.cascade import CascadePolicy
from deephelp_app.application.corpus import digest
from deephelp_app.application.dense import atomic_json, vector_hash
from deephelp_app.application.hybrid import HybridRetriever
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import EmbeddingResult
from deephelp_app.domain.models.retrieval import DenseResult
from deephelp_tools.evaluation.quality.paired import Experiment, label, report, selected
from deephelp_tools.evaluation.retrieval.hybrid_eval import (
    RetrievalQuery,
    classify,
    query_text,
    ranking,
)
from deephelp_tools.integrity import evaluation_provenance
from deephelp_tools.probes.usage import api_records, summarize_usage


def source_identity() -> dict[str, str]:
    p = evaluation_provenance()
    return {k: str(p[k]) for k in ("package_digest", "tooling_digest")}


def validate_contract(spec: Experiment, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """零调用前置核验；冻结后任何输入/参数/实现变化均拒绝套用旧实验。"""
    spec.require_factor()
    rows = selected(snapshot, spec.split)
    if (
        snapshot["dataset_id"] != spec.dataset_id
        or snapshot["freeze_hash"] != spec.freeze_hash
        or digest(rows) != spec.selection_hash
        or source_identity() != spec.source
        or any(file_digest(Path(p)) != sha for p, sha in spec.files.items())
    ):
        raise ConfigurationError("Frozen experiment source/data/config bytes changed")
    return rows


def read_contract(path: Path) -> tuple[Experiment, dict[str, Any]]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    spec = Experiment.model_validate(value["contract"])
    if spec.fingerprint != value["experiment_fingerprint"]:
        raise ConfigurationError("Experiment fingerprint changed")
    return spec, value["paths"]


async def execute(
    contract_path: Path,
    snapshot: dict[str, Any],
    directory: Path,
    embedding: Any,
    classifier: Any,
    gate: Any,
    *,
    retry_uncertain: bool = False,
) -> dict[str, Any]:
    """一批固定首条消息交替先后顺序，每查询一次向量，原HybridRetriever产生同条件路由。

    directory只属于该实验fingerprint；OS锁保护CLI与工作台不能同时写同一journal。
    不拥有借用gateway/预算；取消通过finally保存原观察与实际API记录并释放自己的资源。
    """
    spec, paths = read_contract(contract_path)
    rows = validate_contract(spec, snapshot)
    directory.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240 -- bounded journal path
    lock = directory / ".writer"
    try:
        handle = lock.open("x")  # noqa: ASYNC230 -- ownership acquired before first await
    except FileExistsError as exc:
        raise ConfigurationError("Experiment writer already exists; reconcile owner first") from exc
    client = None
    state_path = directory / "journal.json"
    state = dict(
        experiment_fingerprint=spec.fingerprint,
        cache={},
        api_records=[],
        remote_attempts=0,
        attempts_start=gate.state["attempts"],
    )
    valid_state = False
    old_e = list(embedding.diagnostics)
    old_c = list(classifier.diagnostics)

    def capture(sample: str | None = None) -> None:
        nonlocal old_e, old_c
        state["api_records"].extend(
            api_records(embedding.diagnostics, phase="paired", sample=sample, previous=old_e)
        )
        old_e = list(embedding.diagnostics)
        if classifier is not embedding:
            state["api_records"].extend(
                api_records(classifier.diagnostics, phase="paired", sample=sample, previous=old_c)
            )
            old_c = list(classifier.diagnostics)

    try:
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        if state["experiment_fingerprint"] != spec.fingerprint:
            raise ConfigurationError("Journal contract changed")
        valid_state = True
        if state.get("pending") and not retry_uncertain:
            raise ConfigurationError("UNKNOWN inference requires explicit read-only recovery")
        attempts = int(state.get("executions", 0))
        # 已完成的报告直接返回；浏览/恢复不再派发模型或检索。
        if state.get("complete"):
            if state.get("report_hash") != file_digest(directory / "report.json"):
                raise ConfigurationError("Completed report bytes changed or need reconciliation")
            completed: dict[str, Any] = json.loads(
                (directory / "report.json").read_text(encoding="utf-8")
            )
            return completed
        if attempts >= 3:
            raise ConfigurationError("Three bounded experiment executions exhausted")
        state["executions"] = attempts + 1
        config = ProviderConfig.load(Path(paths["providers"]))
        binding = load_binding(
            json.loads(await asyncio.to_thread(Path(paths["pointer"]).read_text, encoding="utf-8")),
            config,
            Path(paths["corpus"]),
        )
        if getattr(embedding, "config", config).signature() != binding.scope.signature:
            raise ConfigurationError("Borrowed embedding gateway differs from frozen signature")
        judge = EndpointConfig.model_validate_json(
            await asyncio.to_thread(Path(paths["judge"]).read_text, encoding="utf-8")
        )
        if classifier.config.chat != judge or embedding.config != config:
            raise ConfigurationError("Borrowed gateways differ from frozen role configuration")
        if spec.endpoint_hashes != {
            "embedding": digest(embedding._embedding_credentials[0]),
            "classifier": digest(classifier._chat_credentials[0]),
        }:
            raise ConfigurationError("Resolved model endpoints differ from frozen experiment")
        client = create_client(Path(paths["root"]))
        store = MilvusHybridStore(client, gate.request())
        await store.validate(binding.scope, binding.corpus_digest)
        retriever = HybridRetriever(
            embedding,
            store,
            dense_weight=binding.weight,
            records=binding.records,
            corpus_digest=binding.corpus_digest,
        )
        policy = CascadePolicy.load(Path(paths["policy"]))
        for i, row in enumerate(rows):
            tid = row["turn_id"]
            cached = state["cache"].setdefault(tid, {})
            q = RetrievalQuery(
                query_id=tid,
                text=row["text"],
                category="independent-first-turn",
                gold=IntentCode(row["gold"])
                if row["gold"] not in {"unknown", "multiple"}
                else None,
                source_group=row["case_id"],
                variant_group=row["case_id"],
            )
            if "embedding" not in cached:
                state["pending"] = dict(turn_id=tid, phase="embedding")
                atomic_json(state_path, state)
                call_budget = gate.request()
                try:
                    embedded = await embedding.embed([query_text(q)], call_budget)
                finally:
                    state["remote_attempts"] += call_budget.attempts_used
                capture(tid)
                cached["embedding"] = embedded.model_dump(mode="json")
                cached["vector_hash"] = vector_hash(embedded.vectors[0])
                state["pending"] = None
                atomic_json(state_path, state)
            else:
                embedded = EmbeddingResult.model_validate(cached["embedding"])
            if "routes" not in cached:
                store.budget = gate.request()
                state["pending"] = dict(turn_id=tid, phase="retrieval")
                atomic_json(state_path, state)
                routes = await retriever.compare_embedded(
                    query_text(q), binding.scope, gate.request(), embedded, top_k=binding.top_k
                )
                cached["routes"] = {k: v.model_dump(mode="json") for k, v in routes.items()}
                cached["retrieval_latency"] = store.latencies.copy()
                state["pending"] = None
                atomic_json(state_path, state)
            for mode in ("baseline", "candidate") if i % 2 == 0 else ("candidate", "baseline"):
                if mode in cached and "error" not in cached[mode]:
                    continue
                variant = getattr(spec, mode)
                raw = cached["routes"][variant.route]
                result = DenseResult.model_validate(raw | dict(scope=binding.scope))
                state["pending"] = dict(turn_id=tid, phase=mode)
                atomic_json(state_path, state)
                started = time.perf_counter()
                call_budget = gate.request()
                try:
                    decision = await classify(
                        q,
                        result,
                        classifier,
                        call_budget,
                        policy=policy,
                        system_prompt=variant.system_prompt,
                    )
                    capture(tid + ":" + mode)
                    cached[mode] = dict(
                        label=label(decision),
                        decision=decision,
                        ranking=ranking(q, result),
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                        route=variant.route,
                    )
                except Exception as exc:
                    cached[mode] = dict(
                        error=type(exc).__name__,
                        code=exc.code.value if isinstance(exc, AppError) else None,
                        request_id=exc.provider_request_id if isinstance(exc, AppError) else None,
                    )
                    atomic_json(state_path, state)
                    raise
                finally:
                    state["remote_attempts"] += call_budget.attempts_used
                state["pending"] = None
                atomic_json(state_path, state)
        observations = [
            r
            | {m: state["cache"][r["turn_id"]][m] for m in ("baseline", "candidate")}
            | dict(
                vector_hash=state["cache"][r["turn_id"]]["vector_hash"],
                retrieval_latency=state["cache"][r["turn_id"]]["retrieval_latency"],
            )
            for r in rows
        ]
        value = report(spec, observations, [r["turn_id"] for r in rows])
        value["usage"] = summarize_usage(state["api_records"], attempts=state["remote_attempts"])
        value["shared_dependency_attempts"] = gate.state["attempts"] - state["attempts_start"]
        value["cumulative_budget"] = gate.state
        atomic_json(directory / "report.json", value)
        state["report_hash"] = file_digest(directory / "report.json")
        state["complete"] = value["status"] == "COMPLETE"
    finally:
        # API对象身份避免有界diagnostics尾部的offset错位；两gateway借用同对象时只记录一次。
        try:
            if valid_state:
                capture()
                state["cumulative_budget"] = gate.state
                if not state.get("complete") and state.get("cache"):
                    partial_rows = [
                        r
                        | {
                            m: state["cache"]
                            .get(r["turn_id"], {})
                            .get(m, {"error": "NOT_COMPLETED"})
                            for m in ("baseline", "candidate")
                        }
                        for r in rows
                    ]
                    partial = report(spec, partial_rows, [r["turn_id"] for r in rows])
                    partial["pending"] = state.get("pending")
                    partial["usage"] = summarize_usage(
                        state["api_records"], attempts=state["remote_attempts"]
                    )
                    atomic_json(directory / "report.json", partial)
                atomic_json(state_path, state)
        finally:
            try:
                if client is not None:
                    await asyncio.shield(client.close())
            finally:
                handle.close()
                lock.unlink()
    return value
