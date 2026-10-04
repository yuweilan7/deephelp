"""Prepare and verify a complete immutable version before publication transactions."""

from pathlib import Path
from typing import Any

from deephelp_app.adapters.asset_integrity import PACKAGE, file_digest, provenance
from deephelp_app.adapters.fasttext_runtime import FastTextClassifier, pointer_manifest
from deephelp_app.adapters.runtime_assets import read_data, verify_files, verify_remote
from deephelp_app.application.corpus import digest
from deephelp_app.application.sop_governance import SOPRegistry
from deephelp_app.bootstrap.mvp_runtime import BudgetSession
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models import ReleaseManifest


def release_hash(manifest: ReleaseManifest) -> str:
    return digest(manifest.model_dump(mode="json"))


def verify_release(manifest: ReleaseManifest) -> dict[str, Any]:
    if any(file_digest(Path(path)) != value for path, value in manifest.files.items()):
        raise ConfigurationError("Immutable release artifact changed or disappeared")
    data = verify_files(Path(manifest.assets_path), validation_inputs=False)
    dense = read_data(Path(data["dense_pointer"]))
    classifier = FastTextClassifier(pointer_manifest(Path(data["fasttext_pointer"])))
    registry = SOPRegistry.model_validate(manifest.sop_registry)
    package = PACKAGE
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
        or validation.get("code_integrity_scope") != "runtime-v2"
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
