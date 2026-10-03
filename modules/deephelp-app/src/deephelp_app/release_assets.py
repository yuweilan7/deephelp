"""Prepare and verify a complete immutable version before publication transactions."""

from pathlib import Path
from typing import Any

from deephelp_app.corpus import digest
from deephelp_app.domain.models import ReleaseManifest
from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation import file_digest
from deephelp_app.evaluation_cli import provenance
from deephelp_app.fasttext_runtime import FastTextClassifier, pointer_manifest
from deephelp_app.flywheel_assets import read_data, verify_files, verify_remote
from deephelp_app.mvp_runtime import BudgetSession
from deephelp_app.sop_governance import SOPRegistry


def release_hash(manifest: ReleaseManifest) -> str:
    return digest(manifest.model_dump(mode="json"))


def prepare_manifest(
    assets: Path,
    providers: Path,
    registry: SOPRegistry,
    version: str,
    *,
    validation_path: Path | None = None,
) -> ReleaseManifest:
    data = verify_files(assets)
    validation_path = validation_path or assets.parent / "validation.json"
    validation = read_data(validation_path)
    if validation.get("status") != "PASS" or validation.get("assets_digest") != digest(data):
        raise ConfigurationError("Complete release requires this asset's successful regression")
    if data["providers_digest"] != file_digest(providers):
        raise ConfigurationError("Provider configuration changed")
    dense = read_data(Path(data["dense_pointer"]))
    classifier = FastTextClassifier(pointer_manifest(Path(data["fasttext_pointer"])))
    package = Path(__file__).parent
    prompts = {str(p.relative_to(package)): file_digest(p) for p in sorted(package.rglob("*.txt"))}
    code = provenance()
    judge = providers.parent / "event-judge.example.json"
    if (
        validation.get("sop_snapshot") != registry.snapshot_hash
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


def verify_release(manifest: ReleaseManifest) -> dict[str, Any]:
    if any(file_digest(Path(path)) != value for path, value in manifest.files.items()):
        raise ConfigurationError("Immutable release artifact changed or disappeared")
    data = verify_files(Path(manifest.assets_path))
    dense = read_data(Path(data["dense_pointer"]))
    classifier = FastTextClassifier(pointer_manifest(Path(data["fasttext_pointer"])))
    registry = SOPRegistry.model_validate(manifest.sop_registry)
    package = Path(__file__).parent
    validation = read_data(Path(manifest.validation_path))
    prompts = {str(p.relative_to(package)): file_digest(p) for p in sorted(package.rglob("*.txt"))}
    if (
        digest(data) != manifest.assets_digest
        or dense["corpus_digest"] != manifest.corpus_digest
        or dense["active"]["signature"] != manifest.embedding_signature
        or classifier.manifest.model_dump(mode="json") != manifest.fasttext
        or registry.snapshot_hash != manifest.sop_snapshot
        or file_digest(Path(data["rules"])) != manifest.rules_digest
        or file_digest(Path(manifest.providers_path)) != manifest.providers_digest
        or file_digest(Path(manifest.judge_providers_path)) != manifest.judge_providers_digest
        or manifest.prompts != prompts
        or provenance()["package_digest"] != manifest.code_digest
        or data["source"] != manifest.review_snapshot
        or digest(validation) != manifest.validation_digest
        or validation.get("status") != "PASS"
        or validation.get("assets_digest") != manifest.assets_digest
        or validation.get("sop_snapshot") != manifest.sop_snapshot
        or validation.get("code_digest") != manifest.code_digest
        or validation.get("providers_digest") != manifest.providers_digest
        or validation.get("judge_providers_digest") != manifest.judge_providers_digest
    ):
        raise ConfigurationError("Complete release versions/configuration do not match")
    required = {
        str(Path(data[key]).resolve()) for key in ("dense_pointer", "fasttext_pointer", "rules")
    } | {
        manifest.providers_path,
        manifest.judge_providers_path,
        manifest.assets_path,
        manifest.validation_path,
    }
    if not required.issubset(manifest.files):
        raise ConfigurationError("A consumed release artifact has no fingerprint")
    return data


async def verify_complete(manifest: ReleaseManifest, gate: BudgetSession) -> None:
    await verify_remote(verify_release(manifest), gate)
