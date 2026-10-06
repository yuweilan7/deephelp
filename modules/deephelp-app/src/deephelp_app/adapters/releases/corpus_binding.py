"""完整发布的语料审核闭包，运行时只读原件/hash，不调用模型或打开盲测。

作者PG快照是审核事实，文件是不可变投影；当前反馈revision由ReleaseStore另行复核。
本守卫把候选指针、原审核与检索/分类门禁连接起来，不能仅凭PASS字符串切换。
"""

import json
from pathlib import Path
from typing import Any, cast

from deephelp_app.adapters.releases.asset_integrity import file_digest, provenance
from deephelp_app.application.artifact_paths import resolve_artifact
from deephelp_app.application.corpus import digest
from deephelp_app.application.corpus_authoring import feedback_record, verify_snapshot
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.feedback import FeedbackCandidate
from deephelp_app.domain.registry import complaint_registry


def require_quality(
    gate: dict[str, Any], retrieval: dict[str, Any], catalog: str, deployed_route: str = "hybrid"
) -> None:
    """重算正式路线非退化与分类覆盖；诊断路线差值保留，不能改选路线骗过发布。"""
    if set(retrieval["metrics"]) != {"dev", "test"} or any(
        not {"dense", "bm25", "hybrid", deployed_route}.issubset(v["candidate"])
        or not {"dense", "bm25", "hybrid", deployed_route}.issubset(v["published"])
        for v in retrieval["metrics"].values()
    ):
        raise ConfigurationError("Corpus retrieval coverage is incomplete")
    if gate.get("contract", {}).get("deployed_route") != deployed_route:
        raise ConfigurationError("Corpus gate differs from the deployed retrieval route")
    expected = {o["query_id"]: o["gold"] for o in retrieval["observations"] if o["split"] == "test"}
    labels = {c.value for c in complaint_registry(catalog).actionable_codes}
    candidate = {r["query_id"]: r for r in gate["rows"] if r["version"] == "candidate"}
    baseline = {r["query_id"]: r for r in gate["rows"] if r["version"] == "published"}
    if (
        set(expected.values()) != labels | {None}
        or candidate.keys() != expected.keys()
        or baseline.keys() != expected.keys()
        or len(gate["rows"]) != 2 * len(expected)
        or any(r["route"] != deployed_route for r in gate["rows"])
        or any(
            r["gold"] != expected[q]
            or r["final_code"] != expected[q]
            or r["classification_correct"] is not True
            for q, r in candidate.items()
        )
    ):
        raise ConfigurationError(
            "Corpus classification omitted a leaf/unknown or has incorrect gold"
        )
    for values in retrieval["metrics"].values():
        for mode, current in values["candidate"].items():
            for metric in ("recall_1", "recall_3", "candidate_recall_1", "mrr"):
                old = values["published"][mode][metric]
                new = current[metric]
                if (
                    old is None
                    or new is None
                    or not 0 <= old <= 1
                    or not 0 <= new <= 1
                    or (mode == deployed_route and new < old - 1e-9)
                ):
                    raise ConfigurationError("Corpus retrieval quality regressed or is unknown")


def read(path: str | Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(resolve_artifact(path).read_text(encoding="utf-8")))


def require_content(validation: dict[str, Any], binding: dict[str, Any]) -> None:
    """原完整业务报告必须覆盖六叶子和未知范围，反馈采集或检索PASS不能替代。"""
    required = {
        "order-adjacent",
        "coupon-adjacent",
        "discount-adjacent",
        "eligible",
        "attempt",
        "refund-pending",
        "unsupported-logistics",
        "unsupported-refund",
    }
    if (
        validation.get("corpus_binding_digest") != digest(binding)
        or validation.get("phase") != "release"
        or validation.get("status") != "PASS"
        or not required.issubset({r["case_id"] for r in validation.get("rows", [])})
        or not validation.get("checks")
        or not all(v is True for v in validation["checks"].values())
    ):
        raise ConfigurationError("Corpus release needs exact six-leaf/unknown business content")


def corpus_binding(data: dict[str, Any]) -> dict[str, Any] | None:
    """旧资产继续原合同；有作者绑定时每个消费字节、审核和本次质量门禁必须一致。"""
    if not (path := data.get("corpus_binding")):
        return None
    binding = read(path)
    if binding.get("format") != "m44-corpus-binding-v1":
        raise ConfigurationError("Unsupported corpus release binding")
    files = binding["files"] | {str(path): file_digest(Path(path))}
    resolved_files = {str(resolve_artifact(p)): h for p, h in data["files"].items()}
    if any(
        resolved_files.get(str(resolve_artifact(p))) != h or file_digest(Path(p)) != h
        for p, h in files.items()
    ):
        raise ConfigurationError("Corpus release consumed artifact has no exact fingerprint")
    snapshot = read(binding["review"])
    verify_snapshot(snapshot)
    gate = read(binding["classification_gate"])
    retrieval = read(binding["retrieval_gate"])
    pointer = read(data["dense_pointer"])
    selection = pointer["selection"]
    deployed_route = (
        "hybrid_rerank"
        if selection["format"] == "m31-dev-selection-v1" and selection["rerank_policy"]["enabled"]
        else "hybrid"
    )
    require_quality(gate, retrieval, snapshot["catalog_version"], deployed_route)
    source = {}
    for entry in snapshot["entries"].values():
        if raw := entry.get("feedback"):
            candidate = FeedbackCandidate.model_validate(raw)
            if (
                feedback_record(candidate, snapshot["catalog_version"]).model_dump(mode="json")
                != entry["record"]
            ):
                raise ConfigurationError(
                    "Corpus feedback record differs from original human review"
                )
            source[candidate.candidate_id] = raw
    if (
        source != data["source"]["candidates"]
        or binding["version_hash"] != snapshot["version_hash"]
        or snapshot["corpus_sha256"] != file_digest(Path(binding["corpus"]))
        or pointer["corpus_digest"] != binding["index_digest"]
        or retrieval.get("status") != "PASS"
        or gate.get("status") != "PASS"
        or gate.get("version_hash") != snapshot["version_hash"]
        or gate.get("retrieval_digest") != digest(retrieval)
        or gate.get("contract", {}).get("code_digest") != provenance()["package_digest"]
        or gate.get("contract", {}).get("providers_digest") != data["providers_digest"]
        or not gate.get("checks")
        or not all(v is True for v in gate["checks"].values())
    ):
        raise ConfigurationError("Corpus approval/index/quality binding does not match")
    return binding
