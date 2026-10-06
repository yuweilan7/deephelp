"""语料候选的受影响分类门禁与原资产闭包，沿唯一LearningJobs显式执行。

先复核冻结split/同向量观察，再借原IntentService及当前级联策略分类。
逐条结果先落盘，恢复跳过已确认模型结果；代码/配置/输入变化必须新门禁。
这里只准备资产，完整业务验收、ReleaseManifest和PG CAS继续原发布入口。
"""

import asyncio
import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from deephelp_app.adapters.releases.asset_integrity import file_digest, provenance
from deephelp_app.adapters.releases.catalog_binding import catalog_binding
from deephelp_app.adapters.releases.runtime_assets import read_data as load_json
from deephelp_app.application.cascade import CascadePolicy
from deephelp_app.application.corpus import digest
from deephelp_app.application.corpus_authoring import feedback_record
from deephelp_app.application.dense import atomic_json
from deephelp_app.bootstrap.runtime.assembly import LiveAssembly
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.feedback import FeedbackCandidate
from deephelp_app.domain.models.retrieval import DenseResult, HybridScope
from deephelp_tools.evaluation.feedback.flywheel import snapshot as feedback_snapshot
from deephelp_tools.evaluation.feedback.flywheel_assets import verify_files
from deephelp_tools.evaluation.retrieval.corpus_candidate import freeze_files, immutable
from deephelp_tools.evaluation.retrieval.hybrid_eval import classify, dataset
from deephelp_tools.integrity import evaluation_provenance


def metric_changes(retrieval: dict[str, Any]) -> list[dict[str, Any]]:
    """输出可比较指标的实际差值；耗时只展示，不以单次波动代替质量门禁。"""
    rows = []
    for split, values in retrieval["metrics"].items():
        for route, candidate in values["candidate"].items():
            baseline = values["published"][route]
            for name in ("recall_1", "recall_3", "candidate_recall_1", "mrr"):
                if name in candidate and name in baseline:
                    change = candidate[name] - baseline[name]
                    rows.append(
                        dict(
                            split=split,
                            route=route,
                            metric=name,
                            published=baseline[name],
                            candidate=candidate[name],
                            delta=change,
                            result="improved"
                            if change > 1e-9
                            else "regressed"
                            if change < -1e-9
                            else "unchanged",
                        )
                    )
    return rows


async def quality_gate(
    root: Path,
    snapshot: dict[str, Any],
    assembly: LiveAssembly,
    factory: Callable[[], ExecutionBudget],
    baseline_assets: Path,
) -> dict[str, Any]:
    """候选/正式分类消费同一冻结检索观察，无新Embedding；必须所有盲测及未知正确。"""
    directory = freeze_files(root, snapshot, assembly)
    assets = verify_files(baseline_assets, validation_inputs=True)
    catalog = catalog_binding(assets, snapshot["catalog_version"])
    if catalog is None:
        raise ConfigurationError("Corpus release requires the current catalog policy")
    _, queries = dataset(
        directory / "corpus.jsonl", {s: directory / (s + ".json") for s in ("dev", "test")}
    )
    retrieval = load_json(directory / "retrieval-gate.json")
    if retrieval.get("status") != "PASS":
        raise ConfigurationError(
            "Corpus classification requires completed same-vector observations"
        )
    gateway = assembly.gateway
    if gateway is None:
        raise ConfigurationError("Original Chat gateway unavailable")
    changes = metric_changes(retrieval)
    contract = dict(
        version_hash=snapshot["version_hash"],
        retrieval_digest=digest(retrieval),
        code_digest=provenance()["package_digest"],
        tooling_digest=evaluation_provenance()["tooling_digest"],
        providers_digest=assets["providers_digest"],
        baseline_assets_digest=digest(assets),
        cascade_sha256=file_digest(Path(catalog["cascade_policy"])),
    )
    path = directory / "classification-gate.json"
    progress: dict[str, Any] = (
        load_json(path) if path.exists() else dict(contract=contract, rows=[], status="PENDING")
    )
    if progress["contract"] != contract:
        raise ConfigurationError("Corpus classification input/code/config changed")
    if not changes or any(row["delta"] < -1e-9 for row in changes):
        # 已知召回退化先留真实拒绝，不消耗Chat为一个不能准备的候选补分类故事。
        progress.update(
            status="FAIL",
            checks=dict(retrieval_no_regression=False),
            version_hash=snapshot["version_hash"],
            retrieval_digest=digest(retrieval),
            metric_changes=changes,
            classification_accuracy=dict(candidate=None, published=None),
            unverified=[
                "classification skipped: retrieval quality rejected",
                "full business content not run",
                "independent customer distribution",
            ],
            active_changed=False,
        )
        atomic_json(path, progress)
        return progress
    policy = CascadePolicy.load(Path(catalog["cascade_policy"]))
    try:
        for query in queries["test"]:
            observation = next(
                o for o in retrieval["observations"] if o["key"] == "test/" + query.query_id
            )
            for version in ("published", "candidate"):
                key = version + "/" + query.query_id
                if any(row["key"] == key for row in progress["rows"]):
                    continue
                routes = observation["comparison"]["results"][version]["routes"]
                route = "hybrid_rerank" if "hybrid_rerank" in routes else "hybrid"
                result = DenseResult.model_validate(routes[route])
                # 同阈值/兜底策略只重绑定各自源hash/scope，绝不根据盲测放宽门槛。
                from dataclasses import replace

                pointer = (
                    load_json(directory / "pointer.json")
                    if version == "candidate"
                    else load_json(Path(assets["dense_pointer"]))
                )
                bound = replace(
                    policy,
                    scope_fingerprint=result.scope.fingerprint,
                    corpus_digest=pointer["corpus_digest"],
                )
                budget = factory()
                decision = await classify(query, result, gateway, budget, policy=bound)
                progress["rows"].append(
                    dict(
                        key=key,
                        version=version,
                        query_id=query.query_id,
                        gold=query.gold.value if query.gold else None,
                        route=route,
                        **decision,
                        usage=budget.snapshot().model_dump(mode="json"),
                    )
                )
                progress["status"] = "IN_PROGRESS"
                atomic_json(path, progress)
        candidate = [r for r in progress["rows"] if r["version"] == "candidate"]
        baseline = [r for r in progress["rows"] if r["version"] == "published"]
        checks = dict(
            retrieval_no_regression=bool(changes) and all(r["delta"] >= -1e-9 for r in changes),
            classification_complete=len(candidate) == len(baseline) == len(queries["test"]),
            candidate_classification_correct=all(r["classification_correct"] for r in candidate),
            classification_no_regression=sum(r["classification_correct"] for r in candidate)
            >= sum(r["classification_correct"] for r in baseline),
            unknown_boundary=any(r["gold"] is None for r in candidate)
            and all(r["final_code"] is None for r in candidate if r["gold"] is None),
        )
        progress.update(
            status="PASS" if all(checks.values()) else "FAIL",
            checks=checks,
            version_hash=snapshot["version_hash"],
            retrieval_digest=digest(retrieval),
            metric_changes=changes,
            classification_accuracy={
                v: sum(r["classification_correct"] for r in rows) / len(rows)
                for v, rows in (("candidate", candidate), ("published", baseline))
            },
            unverified=[
                "independent customer distribution",
                "unconfigured Rerank",
                "full business content before validate",
            ],
            active_changed=False,
        )
        atomic_json(path, progress)
        return progress
    except BaseException as exc:
        progress.update(
            status="CANCELLED" if isinstance(exc, asyncio.CancelledError) else "PARTIAL",
            error_code=type(exc).__name__,
        )
        atomic_json(path, progress)
        raise


def prepare_corpus_assets(
    root: Path, snapshot: dict[str, Any], assembly: LiveAssembly, baseline: Path
) -> Path:
    """只从PASS候选衍生原资产合同；保留旧规则/FastText/策略，无第二发布实现。"""
    directory = freeze_files(root, snapshot, assembly)
    original = verify_files(baseline, validation_inputs=True)
    old_catalog = catalog_binding(original, snapshot["catalog_version"])
    if old_catalog is None:
        raise ConfigurationError("Corpus candidate requires original catalog binding")
    gate = load_json(directory / "classification-gate.json")
    if gate.get("status") != "PASS" or not all(gate.get("checks", {}).values()):
        raise ConfigurationError("Corpus quality gate must be PASS before preparing assets")
    pointer = load_json(directory / "pointer.json")
    scope = HybridScope.model_validate(pointer["active"])
    policy = load_json(Path(old_catalog["cascade_policy"]))
    policy.update(
        scope_fingerprint=scope.fingerprint,
        corpus_digest=pointer["corpus_digest"],
        dense_weight=pointer["dense_weight"],
    )
    immutable(directory / "cascade.json", json.dumps(policy, sort_keys=True).encode())
    catalog = copy.deepcopy(old_catalog)
    catalog.update(
        corpus=str((directory / "corpus.jsonl").resolve()),
        cascade_policy=str((directory / "cascade.json").resolve()),
        **{s: str((directory / (s + ".json")).resolve()) for s in ("dev", "test")},
    )
    immutable(directory / "catalog-binding.json", json.dumps(catalog, sort_keys=True).encode())
    candidates = []
    for entry in snapshot["entries"].values():
        if raw := entry.get("feedback"):
            candidate = FeedbackCandidate.model_validate(raw)
            if (
                feedback_record(candidate, snapshot["catalog_version"]).model_dump(mode="json")
                != entry["record"]
            ):
                raise ConfigurationError("Feedback source has changed")
            candidates.append(candidate)
    names = (
        "review.json",
        "corpus.jsonl",
        "scope.json",
        "contract.json",
        "index.json",
        "dev.json",
        "test.json",
        "retrieval-gate.json",
        "classification-gate.json",
        "selection.json",
        "pointer.json",
        "cascade.json",
        "catalog-binding.json",
    )
    files = {str((directory / n).resolve()): file_digest(directory / n) for n in names}
    binding = dict(
        format="m44-corpus-binding-v1",
        version_hash=snapshot["version_hash"],
        index_digest=pointer["corpus_digest"],
        review=str((directory / "review.json").resolve()),
        corpus=str((directory / "corpus.jsonl").resolve()),
        retrieval_gate=str((directory / "retrieval-gate.json").resolve()),
        classification_gate=str((directory / "classification-gate.json").resolve()),
        files=files,
    )
    immutable(directory / "corpus-binding.json", json.dumps(binding, sort_keys=True).encode())
    assets = copy.deepcopy(original)
    assets.update(
        dense_pointer=str((directory / "pointer.json").resolve()),
        catalog_binding=str((directory / "catalog-binding.json").resolve()),
        corpus_binding=str((directory / "corpus-binding.json").resolve()),
        source=feedback_snapshot(candidates),
        files=original["files"]
        | files
        | {
            str((directory / "corpus-binding.json").resolve()): file_digest(
                directory / "corpus-binding.json"
            )
        },
    )
    path = directory / "assets.json"
    immutable(path, json.dumps(assets, sort_keys=True).encode())
    verify_files(path, validation_inputs=True)
    return path
