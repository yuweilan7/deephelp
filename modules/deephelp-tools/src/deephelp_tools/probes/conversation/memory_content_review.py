"""M46事实比较守卫修正后的同会话内容复核，不重新发送已成功的模型请求。

只允许memory_binding这一个发布文件变化；原模型/业务/DTO/UI每个字节均比较。
新文件独立绑定原报告和差异证明，原FAIL/PASS不改写。其他变化必须重新执行。
"""

import json
from pathlib import Path
from typing import Any

from deephelp_app.adapters.releases.asset_integrity import file_digest, provenance
from deephelp_app.adapters.releases.corpus_binding import corpus_binding, require_quality
from deephelp_app.application.corpus import digest
from deephelp_app.application.dense import atomic_json
from deephelp_app.domain.errors import ConfigurationError
from deephelp_tools.integrity import evaluation_provenance


def verify_runtime_delta(before: Path) -> dict[str, Any]:
    """单文件差异是复用的必要条件，绝不允许模型或执行路径混入新字节。"""
    old = json.loads(before.read_text(encoding="utf-8"))
    now = provenance()
    prior, current = old["artifact_hashes"], now["artifact_hashes"]
    changed = {k for k in prior.keys() | current.keys() if prior.get(k) != current.get(k)}
    if (
        old["package_digest"] != digest(prior)
        or changed != {"adapters/releases/memory_binding.py"}
        or prior.keys() != current.keys()
    ):
        raise ConfigurationError("Content reuse requires unchanged model/business/DTO/UI bytes")
    return {
        "previous_digest": old["package_digest"],
        "current_digest": now["package_digest"],
        "before_path": str(before.resolve()),
        "before_sha256": file_digest(before),
        "changed_files": {k: {"before": prior[k], "after": current[k]} for k in changed},
        "unchanged_files": len(current) - len(changed),
        "scope": "Current guard verifies original same-session values; no new model execution",
    }


def review_assets(before: Path, assets: Path, content: Path, directory: Path) -> tuple[Path, Path]:
    """原分类/完整业务行由当前守卫重算，新闭包绑定所有原件，模型调用不重新计入。"""
    delta = verify_runtime_delta(before)
    directory.mkdir(parents=True, exist_ok=False)
    delta_path = directory / "runtime-review.json"
    atomic_json(delta_path, delta)
    data = json.loads(assets.read_text(encoding="utf-8"))
    for path, fingerprint in data["files"].items():
        if file_digest(Path(path)) != fingerprint:
            raise ConfigurationError("Original bound asset changed")
    old_binding_path = Path(data["corpus_binding"])
    binding = json.loads(old_binding_path.read_text(encoding="utf-8"))
    original_gate = Path(binding["classification_gate"])
    gate = json.loads(original_gate.read_text(encoding="utf-8"))
    retrieval = json.loads(Path(binding["retrieval_gate"]).read_text(encoding="utf-8"))
    review = json.loads(Path(binding["review"]).read_text(encoding="utf-8"))
    if gate["contract"]["code_digest"] != delta["previous_digest"]:
        raise ConfigurationError("Original classification runtime differs")
    require_quality(gate, retrieval, review["catalog_version"], gate["deployed_route"])
    gate["contract"] = gate["contract"] | {
        "code_digest": delta["current_digest"],
        "tooling_digest": evaluation_provenance()["tooling_digest"],
    }
    gate["observed_source"] = {"path": str(original_gate), "sha256": file_digest(original_gate)}
    gate["runtime_review"] = delta
    gate["execution"] = (
        "Current read-only quality verification; original 16 model requests retained"
    )
    new_gate = directory / "classification-reviewed.json"
    atomic_json(new_gate, gate)
    bound = {
        str(p.resolve()): file_digest(p)
        for p in (before, delta_path, original_gate, old_binding_path, assets, new_gate)
    }
    binding["classification_gate"] = str(new_gate.resolve())
    binding["files"] |= bound
    new_binding = directory / "corpus-binding.json"
    atomic_json(new_binding, binding)
    data["corpus_binding"] = str(new_binding.resolve())
    data["files"] |= bound | {str(new_binding.resolve()): file_digest(new_binding)}
    corpus_binding(data)
    new_assets = directory / "assets.json"
    atomic_json(new_assets, data)
    report = json.loads(content.read_text(encoding="utf-8"))
    if (
        report["status"] != "PASS"
        or report["code_digest"] != delta["previous_digest"]
        or not all(v is True for v in report["checks"].values())
    ):
        raise ConfigurationError("Original full content is not applicable")
    sources = report.get("content_sources", {}) | {
        str(content.resolve()): file_digest(content),
        str(before.resolve()): file_digest(before),
        str(delta_path.resolve()): file_digest(delta_path),
    }
    if source := report.get("observed_source"):
        sources[source["path"]] = source["sha256"]
    report.update(
        code_digest=delta["current_digest"],
        tooling_digest=evaluation_provenance()["tooling_digest"],
        assets_digest=digest(data),
        corpus_binding_digest=digest(binding),
        observed_source={"path": str(content.resolve()), "sha256": file_digest(content)},
        content_sources=sources,
        runtime_review=delta,
        api_observations=[],
        execution="Current read-only content verification; original 15 business runs retained",
    )
    new_content = directory / "content-reviewed.json"
    atomic_json(new_content, report)
    return new_assets, new_content
