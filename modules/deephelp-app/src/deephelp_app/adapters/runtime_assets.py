"""Verify consumed prepared runtime files/vectors without reading frozen inputs."""

import asyncio
from pathlib import Path
from typing import Any, cast

from deephelp_app.adapters.asset_integrity import file_digest
from deephelp_app.adapters.fasttext_runtime import FastTextClassifier, pointer_manifest
from deephelp_app.adapters.milvus_dense import MilvusDenseStore, create_client
from deephelp_app.application.corpus import digest
from deephelp_app.application.dense import load_json, verify_manifest_rows
from deephelp_app.bootstrap.mvp_runtime import BudgetSession
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.models import DenseScope


def read_data(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], load_json(path))


def verify_files(path: Path, *, validation_inputs: bool = False) -> dict[str, Any]:
    data = read_data(path)
    if data.get("format") != "m18-demo-assets-v1" or data.get("status") != "PREPARED":
        raise ConfigurationError("Only complete prepared demo assets can be used")
    if validation_inputs:
        raise ConfigurationError("Frozen validation requires the independent tools package")
    if data["source"]["digest"] != digest(data["source"]["candidates"]):
        raise ConfigurationError("Feedback review snapshot changed")
    required = {
        str(Path(data[k]).resolve()) for k in ("dense_pointer", "fasttext_pointer", "rules")
    }
    dense = read_data(Path(data["dense_pointer"]))
    model_path = pointer_manifest(Path(data["fasttext_pointer"]))
    model = FastTextClassifier(model_path)
    required.update(
        {
            str(Path(dense["manifest"]).resolve()),
            str(model_path.resolve()),
            str((model_path.parent / model.manifest.model_file).resolve()),
        }
    )
    if not required.issubset(data["files"]):
        raise ConfigurationError("Every consumed pointer/manifest/model/rule needs a fingerprint")
    if not data["files"] or any(file_digest(Path(p)) != h for p, h in data["files"].items()):
        raise ConfigurationError("Feedback artifact content changed")
    return data


async def verify_remote(data: dict[str, Any], gate: BudgetSession) -> None:
    pointer = read_data(Path(data["dense_pointer"]))
    scope = DenseScope.model_validate(pointer["active"])
    client = create_client(await asyncio.to_thread(Path.cwd))
    try:
        store = MilvusDenseStore(client, gate.request())
        state = read_data(Path(pointer["manifest"]))
        await store.validate(scope, str(pointer["corpus_digest"]))
        rows = await store.read(scope, list(state["completed_ids"]))
        verify_manifest_rows(state, rows)
        if await store.count(scope, filtered=False) != len(rows):
            raise ConfigurationError("Feedback vector count mismatch")
    finally:
        await client.close()
