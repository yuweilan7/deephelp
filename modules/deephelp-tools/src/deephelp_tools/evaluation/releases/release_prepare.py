"""只有精确内容验证完成后才构造完整不可变发布清单。

prepare_manifest绑定本次runtime/tooling、providers、资产、审核来源与报告目标。
准备不等于切active，不能借同hash或旧报告绕过必要内容门禁；生产不依赖此工具。
"""

from pathlib import Path

from deephelp_app.adapters.mcp.retrieval.retrieval_protocol import binding_from_files
from deephelp_app.adapters.model_gateway.fasttext_runtime import (
    FastTextClassifier,
    pointer_manifest,
)
from deephelp_app.adapters.model_gateway.providers import ProviderConfig
from deephelp_app.adapters.releases.asset_integrity import PACKAGE, file_digest, provenance
from deephelp_app.adapters.releases.catalog_binding import catalog_binding
from deephelp_app.adapters.releases.corpus_binding import corpus_binding, require_content
from deephelp_app.adapters.releases.memory_binding import (
    memory_policy_binding,
    require_memory_content,
)
from deephelp_app.adapters.releases.skill_store import read_skill_review
from deephelp_app.adapters.releases.sop_drafts import read_sop_review
from deephelp_app.application.collaboration import policy_release_binding
from deephelp_app.application.corpus import digest
from deephelp_app.application.intent_prompt import (
    ROLE,
    default_system,
    fallback_contract,
    read_prompt,
)
from deephelp_app.application.sop_governance import SOPRegistry
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models.release import ReleaseManifest
from deephelp_tools.evaluation.feedback.flywheel_assets import read_data, verify_files
from deephelp_tools.integrity import evaluation_provenance


def prepare_manifest(
    assets: Path,
    providers: Path,
    registry: SOPRegistry,
    version: str,
    *,
    validation_path: Path | None = None,
    prompt_artifact: Path | None = None,
    skill_review: Path | None = None,
    sop_review: Path | None = None,
    quality_review: Path | None = None,
    memory_report: Path | None = None,
) -> ReleaseManifest:
    quality_files = {}
    if quality_review:
        from deephelp_tools.evaluation.quality.improvement import require_eligible

        quality_files = require_eligible(quality_review, prompt_artifact)
    elif prompt_artifact and read_prompt(prompt_artifact, registry.catalog_version).name.startswith(
        "m45-reviewed-"
    ):
        raise ConfigurationError("M45 reviewed Prompt requires its independent quality closure")
    data = verify_files(assets, validation_inputs=True)
    catalog = catalog_binding(data, registry.catalog_version)
    validation_path = validation_path or assets.parent / "validation.json"
    validation = read_data(validation_path)
    authored = corpus_binding(data)
    if authored is not None:
        require_content(validation, authored)
    if validation.get("status") != "PASS" or validation.get("assets_digest") != digest(data):
        raise ConfigurationError("Complete release requires this asset's successful regression")
    if catalog and validation.get("catalog_binding_digest") != digest(catalog):
        raise ConfigurationError("Catalog binding has no matching exact content validation")
    if data["providers_digest"] != file_digest(providers):
        raise ConfigurationError("Provider configuration changed")
    dense = read_data(Path(data["dense_pointer"]))
    if dense.get("selection", {}).get("format") == "m31-dev-selection-v1":
        pointer = Path(data["dense_pointer"])
        binding = binding_from_files(
            pointer, ProviderConfig.load(providers), pointer.parent / "corpus.jsonl"
        )
        if validation.get("retrieval_profile") != binding.health():
            raise ConfigurationError("Rerank profile differs from complete content validation")
    classifier = FastTextClassifier(pointer_manifest(Path(data["fasttext_pointer"])))
    package = PACKAGE
    prompts = {
        p.relative_to(package).as_posix(): file_digest(p) for p in sorted(package.rglob("*.txt"))
    }
    collaboration = policy_release_binding()
    prompts[collaboration["resource"]] = collaboration["sha256"]
    if validation.get("collaboration_policy_digest") != collaboration["policy_digest"]:
        raise ConfigurationError("Agent policy has no matching exact content validation")
    code = provenance()
    if not code["git_commit"] or code["dirty_worktree"] is None:
        raise ConfigurationError("Complete release preparation requires source Git provenance")
    judge = providers.parent / "event-judge.example.json"
    if (
        validation.get("code_integrity_scope") != "runtime-v2"
        or validation.get("tooling_digest") != evaluation_provenance()["tooling_digest"]
        or validation.get("sop_snapshot") != registry.snapshot_hash
        or validation.get("code_digest") != code["package_digest"]
        or validation.get("judge_providers_digest") != file_digest(judge)
        or validation.get("providers_digest") != file_digest(providers)
    ):
        raise ConfigurationError("Complete release regression must match SOP/code/providers")
    files = data["files"] | {str(assets.resolve()): file_digest(assets)}
    files.update(quality_files)
    files[str(providers.resolve())] = file_digest(providers)
    files[str(judge.resolve())] = file_digest(judge)
    files[str(validation_path.resolve())] = file_digest(validation_path)
    memory_binding = None
    if memory_report:
        memory_content = require_memory_content(memory_report)
        if memory_content.get("tooling_digest") != evaluation_provenance()["tooling_digest"]:
            raise ConfigurationError("Memory content tooling changed")
        memory_binding = memory_policy_binding() | {
            "content_path": str(memory_report.resolve()),
            "content_sha256": file_digest(memory_report),
        }
        files[str(memory_report.resolve())] = file_digest(memory_report)
        source = memory_content.get("observed_source")
        visited: set[str] = set()
        while source:
            if source["path"] in visited or len(visited) >= 8:
                raise ConfigurationError("Memory content source chain is cyclic or too deep")
            visited.add(source["path"])
            if source:
                path = Path(source["path"])
                if file_digest(path) != source["sha256"]:
                    raise ConfigurationError("Original memory content report changed")
                files[str(path.resolve())] = source["sha256"]
                original = read_data(path)
                if review := original.get("runtime_review"):
                    before = Path(review["before_path"])
                    if file_digest(before) != review["before_sha256"]:
                        raise ConfigurationError("Original memory runtime source changed")
                    files[str(before.resolve())] = review["before_sha256"]
                source = original.get("observed_source")
        if review := memory_content.get("runtime_review"):
            path = Path(review["before_path"])
            if file_digest(path) != review["before_sha256"]:
                raise ConfigurationError("Memory runtime review source changed")
            files[str(path.resolve())] = review["before_sha256"]
        prompts[memory_binding["resource"]] = memory_binding["sha256"]
    if sop_review:
        try:
            reviewed_sop, _ = read_sop_review(sop_review)
        except ValueError, KeyError, OSError:
            raise ConfigurationError("SOP approved artifact invalid") from None
        review_sha = file_digest(sop_review)
        if reviewed_sop != registry or validation.get("sop_review_digest") != review_sha:
            raise ConfigurationError("SOP approval has no matching exact content validation")
        files[str(sop_review.resolve())] = review_sha
        prompts["sop:review"] = review_sha
    elif validation.get("sop_review_digest"):
        raise ConfigurationError("Validated SOP must provide its approved artifact")
    skills = None
    if skill_review:
        skills, _ = read_skill_review(skill_review)
        review_sha = file_digest(skill_review)
        if (
            validation.get("skills_digest") != skills.digest
            or validation.get("skills_review_digest") != review_sha
        ):
            raise ConfigurationError("Skill approval has no matching exact content validation")
        files[str(skill_review.resolve())] = review_sha
        prompts["skills:registry"] = skills.digest
        prompts["skills:review"] = review_sha
    elif validation.get("skills_digest"):
        raise ConfigurationError("Validated Skill must provide its approved artifact")
    if observed := validation.get("observed_source"):
        # 只读复核报告也绑定原采集字节；原FAIL保留，不把采集器错误改写成当时PASS。
        source_path = Path(observed["path"])
        if file_digest(source_path) != observed["sha256"]:
            raise ConfigurationError("Reviewed original content report changed")
        files[str(source_path.resolve())] = observed["sha256"]
    for source, fingerprint in validation.get("content_sources", {}).items():
        # 修复采集器只重跑受影响行；合并报告消费的每份原件也进入完整字节闭包。
        source_path = Path(source)
        if file_digest(source_path) != fingerprint:
            raise ConfigurationError("Partial content source report changed")
        files[str(source_path.resolve())] = fingerprint
    if prompt_artifact:
        prompt = read_prompt(prompt_artifact, registry.catalog_version)
        if validation.get("intent_prompt_request_digest") != prompt.request_digest:
            raise ConfigurationError("Prompt request changed since the exact content gate")
        fingerprint = file_digest(prompt_artifact)
        prompts[ROLE] = fingerprint
        files[str(prompt_artifact.resolve())] = fingerprint
    elif validation.get("intent_prompt_request_digest") not in {
        None,
        digest(
            [default_system(registry.catalog_version), fallback_contract(registry.catalog_version)]
        ),
    }:
        raise ConfigurationError("Validated custom Prompt requires its immutable artifact")
    return ReleaseManifest(
        version=version,
        assets_path=str(assets.resolve()),
        assets_digest=digest(data),
        files=files,
        corpus_digest=dense["corpus_digest"],
        embedding_signature=dense["active"]["signature"],
        fasttext=classifier.manifest.model_dump(mode="json"),
        rules_digest=file_digest(Path(data["rules"])),
        sop_registry=registry.model_dump(mode="json"),
        sop_snapshot=registry.snapshot_hash,
        prompts=prompts,
        providers_path=str(providers.resolve()),
        providers_digest=file_digest(providers),
        judge_providers_path=str(judge.resolve()),
        judge_providers_digest=file_digest(judge),
        code_digest=code["package_digest"],
        code_commit=code["git_commit"],
        code_dirty=code["dirty_worktree"],
        review_snapshot=data["source"],
        validation_path=str(validation_path.resolve()),
        validation_digest=digest(validation),
        skill_registry=skills.content() if skills else None,
        skill_review_path=str(skill_review.resolve()) if skill_review else None,
        sop_review_path=str(sop_review.resolve()) if sop_review else None,
        memory_binding=memory_binding,
    )
