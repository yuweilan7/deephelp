"""Prepare only after exact content validation; never a serving dependency."""

from pathlib import Path

from deephelp_app.adapters.asset_integrity import PACKAGE, file_digest, provenance
from deephelp_app.adapters.fasttext_runtime import FastTextClassifier, pointer_manifest
from deephelp_app.application.corpus import digest
from deephelp_app.application.sop_governance import SOPRegistry
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models import ReleaseManifest
from deephelp_tools.evaluation.flywheel_assets import read_data, verify_files
from deephelp_tools.integrity import evaluation_provenance


def prepare_manifest(
    assets: Path,
    providers: Path,
    registry: SOPRegistry,
    version: str,
    *,
    validation_path: Path | None = None,
) -> ReleaseManifest:
    data = verify_files(assets, validation_inputs=True)
    validation_path = validation_path or assets.parent / "validation.json"
    validation = read_data(validation_path)
    if validation.get("status") != "PASS" or validation.get("assets_digest") != digest(data):
        raise ConfigurationError("Complete release requires this asset's successful regression")
    if data["providers_digest"] != file_digest(providers):
        raise ConfigurationError("Provider configuration changed")
    dense = read_data(Path(data["dense_pointer"]))
    classifier = FastTextClassifier(pointer_manifest(Path(data["fasttext_pointer"])))
    package = PACKAGE
    prompts = {str(p.relative_to(package)): file_digest(p) for p in sorted(package.rglob("*.txt"))}
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
