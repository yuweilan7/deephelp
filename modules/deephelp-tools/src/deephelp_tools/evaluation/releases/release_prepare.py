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
) -> ReleaseManifest:
    data = verify_files(assets, validation_inputs=True)
    validation_path = validation_path or assets.parent / "validation.json"
    validation = read_data(validation_path)
    if validation.get("status") != "PASS" or validation.get("assets_digest") != digest(data):
        raise ConfigurationError("Complete release requires this asset's successful regression")
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
    files[str(providers.resolve())] = file_digest(providers)
    files[str(judge.resolve())] = file_digest(judge)
    files[str(validation_path.resolve())] = file_digest(validation_path)
    if observed := validation.get("observed_source"):
        # 只读复核报告也绑定原采集字节；原FAIL保留，不把采集器错误改写成当时PASS。
        source_path = Path(observed["path"])
        if file_digest(source_path) != observed["sha256"]:
            raise ConfigurationError("Reviewed original content report changed")
        files[str(source_path.resolve())] = observed["sha256"]
    if prompt_artifact:
        prompt = read_prompt(prompt_artifact)
        if validation.get("intent_prompt_request_digest") != prompt.request_digest:
            raise ConfigurationError("Prompt request changed since the exact content gate")
        fingerprint = file_digest(prompt_artifact)
        prompts[ROLE] = fingerprint
        files[str(prompt_artifact.resolve())] = fingerprint
    elif validation.get("intent_prompt_request_digest") not in {
        None,
        digest([default_system(), fallback_contract()]),
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
    )
