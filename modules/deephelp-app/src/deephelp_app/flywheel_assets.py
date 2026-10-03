"""Prepare three traceable routes; explicit local demo activation follows content gates."""

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import httpx

from deephelp_app.asset_integrity import file_digest
from deephelp_app.assets import asset_path
from deephelp_app.corpus import digest, read_corpus, validate_records
from deephelp_app.dense import DenseImporter, atomic_json, load_json, verify_manifest_rows
from deephelp_app.domain.models import DenseScope
from deephelp_app.errors import ConfigurationError
from deephelp_app.execution import AsyncCalls
from deephelp_app.fasttext_runtime import LABELS, FastTextClassifier, pointer_manifest
from deephelp_app.flywheel import FeedbackCandidate, neutral, snapshot
from deephelp_app.flywheel_store import FeedbackStore
from deephelp_app.milvus_dense import MilvusDenseStore, SSHCapacity, create_client
from deephelp_app.mvp_runtime import BudgetSession, LiveAssembly
from deephelp_app.providers import ProviderConfig, create_gateway

DEMO_DATA = Path(__file__).parent / "assets/evaluation/m18_demo.json"


def read_data(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], load_json(path))


def asset_hashes(output: Path) -> dict[str, str]:
    return {str(p.resolve()): file_digest(p) for p in sorted(output.rglob("*")) if p.is_file()}


def exports(rows: list[FeedbackCandidate]) -> dict[str, Any]:
    source = snapshot(rows)
    corpus = [
        r.model_dump(mode="json") for r in read_corpus(asset_path("m09_corpus.jsonl")).index_records
    ]
    training = json.loads(asset_path("m13_fasttext.json").read_text(encoding="utf-8"))
    rules = []
    forbidden = {neutral(r["text"]) for r in read_data(DEMO_DATA)["release"]}
    seen: set[str] = set()
    for raw in source["candidates"].values():
        candidate = FeedbackCandidate.model_validate(raw)
        key = neutral(candidate.text)
        if key in forbidden or key in seen:
            raise ConfigurationError("Duplicate feedback or independent release input leaked")
        seen.add(key)
        for route, review in candidate.reviews.items():
            if review.state != "approved":
                continue
            assert review.label
            provenance = dict(
                candidate_id=candidate.candidate_id,
                revision=candidate.revision,
                reviewer=review.reviewer,
                method=review.method,
                state=review.state,
            )
            if route == "corpus":
                corpus.append(
                    dict(
                        doc_id="FB-" + candidate.candidate_id[:32],
                        content=candidate.text,
                        intent_code=review.label.value,
                        split="reference",
                        synthetic=True,
                        source_group=candidate.source_group,
                        variant_group=candidate.variant_group,
                        metadata={
                            "feedback_candidate": candidate.candidate_id,
                            "review_revision": str(candidate.revision),
                        },
                    )
                )
            elif route == "fasttext":
                training.append(
                    dict(
                        sample_id="FB-" + candidate.candidate_id[:32],
                        text=candidate.text,
                        label=next(k for k, v in LABELS.items() if v == review.label),
                        split="train",
                        synthetic=True,
                        reviewed=True,
                        source="reviewed-flywheel-m18-v1",
                        source_group=candidate.source_group,
                        variant_group=candidate.variant_group,
                        feedback_review=provenance,
                    )
                )
            else:
                rules.append(
                    dict(
                        candidate_id=candidate.candidate_id,
                        revision=candidate.revision,
                        review=review.model_dump(mode="json"),
                    )
                )
    preview = validate_records(list(enumerate(corpus, 1)), digest(corpus))
    preview.require_valid()
    return dict(source=source, corpus=corpus, training=training, rules=rules)


async def build(
    store: FeedbackStore,
    ids: list[str],
    output: Path,
    version: str,
    gate: BudgetSession,
    providers: Path,
    original_fasttext: Path,
) -> dict[str, Any]:
    if await asyncio.to_thread(output.exists) or not version or len(version) > 64:
        raise ConfigurationError("Use a new bounded immutable asset directory/version")
    rows = await store.get_many(ids) if ids else []
    if any(not any(v.state == "approved" for v in r.reviews.values()) for r in rows):
        raise ConfigurationError("Selected build sources need at least one approved route")
    values = exports(rows)
    await store.require_current(values["source"])
    token, jobs = await store.claim(rows)
    output.mkdir(parents=True)  # noqa: ASYNC240 -- bounded CLI commit, cannot outlive cancellation
    try:
        config = ProviderConfig.load(providers)
        corpus_path, training_path = output / "corpus.jsonl", output / "training.json"
        corpus_path.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in values["corpus"]),
            encoding="utf-8",
        )
        atomic_json(training_path, values["training"])
        atomic_json(output / "source.json", values["source"])
        rules_path = output / "rules.json"
        atomic_json(
            rules_path, dict(format="m18-literal-rules-v1", version=version, rules=values["rules"])
        )
        scope = DenseScope(
            namespace="m18_synthetic", dataset_version=version, signature=config.signature()
        )
        manifest_path = output / "vectors.json"
        milvus = create_client(Path.cwd())
        try:
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                gateway = create_gateway(client, config, AsyncCalls(2, 30))
                budget = gate.request()
                vectors = await DenseImporter(
                    gateway, MilvusDenseStore(milvus, budget), SSHCapacity(Path.cwd())
                ).import_corpus(read_corpus(corpus_path), scope, budget, manifest_path)
        finally:
            await milvus.close()
        dense_pointer = output / "dense.json"
        atomic_json(
            dense_pointer,
            dict(
                format="m05-pointer-v1",
                active=scope.model_dump(mode="json"),
                manifest=str(manifest_path.resolve()),
                corpus_digest=vectors["corpus_digest"],
            ),
        )
        if any(
            r.reviews.get("fasttext") and r.reviews["fasttext"].state == "approved" for r in rows
        ):
            from deephelp_app.evaluation.fasttext_cli import bounded_train

            task = asyncio.create_task(
                asyncio.to_thread(bounded_train, output / "fasttext", training_path)
            )
            try:
                report = await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            if report.get("status") != "PASS":
                raise ConfigurationError("FastText training and quantization gates failed")
            for name in ("selected_raw", "selected_quantized"):
                manifest = Path(report[name])
                data = read_data(manifest)
                data["version"] = version + ("-quant" if name.endswith("quantized") else "-raw")
                atomic_json(manifest, data)
            model_path = Path(report["selected_raw"])
        else:
            model_path = pointer_manifest(original_fasttext)
        model = FastTextClassifier(model_path)
        fast_pointer = output / "fasttext.json"
        atomic_json(
            fast_pointer,
            dict(format="m13-pointer-v1", active=str(model_path.resolve()), previous=None),
        )
        paths = await asyncio.to_thread(asset_hashes, output)
        paths[str(model_path.resolve())] = file_digest(model_path)
        model_file = model_path.parent / model.manifest.model_file
        paths[str(model_file.resolve())] = file_digest(model_file)
        bundle = dict(
            format="m18-demo-assets-v1",
            version=version,
            source=values["source"],
            dense_pointer=str(dense_pointer.resolve()),
            fasttext_pointer=str(fast_pointer.resolve()),
            rules=str(rules_path.resolve()),
            files=paths,
            demo_data_digest=file_digest(DEMO_DATA),
            providers_digest=file_digest(providers),
            status="PREPARED",
            corpus_rows=len(values["corpus"]),
            training_rows=len(values["training"]),
            rule_rows=len(values["rules"]),
        )
        await store.require_current(values["source"])
        derivatives = []
        for candidate in rows:
            for route, approval in candidate.reviews.items():
                if approval.state == "approved":
                    route_paths = {
                        "corpus": [corpus_path, manifest_path, dense_pointer],
                        "fasttext": [training_path, fast_pointer, model_path, model_file],
                        "rule": [rules_path],
                    }[route]
                    if route == "fasttext":
                        route_paths.extend(
                            p for p in (output / "fasttext").rglob("*") if p.is_file()
                        )
                    for path in sorted(set(route_paths)):
                        derivatives.append(
                            dict(
                                candidate_id=candidate.candidate_id,
                                revision=candidate.revision,
                                route=route,
                                artifact_path=str(path.resolve()),
                                artifact_sha256=file_digest(path),
                            )
                        )
        await store.complete(token, jobs, derivatives)
        atomic_json(output / "assets.json", bundle)
        return bundle
    except BaseException:
        async with store.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "UPDATE dh_m18_tasks SET state='PENDING',lease_token=NULL,lease_until=NULL "
                "WHERE lease_token=%s",
                (token,),
            )
            await conn.commit()
        raise


def verify_files(path: Path, *, validation_inputs: bool = True) -> dict[str, Any]:
    data = read_data(path)
    if data.get("format") != "m18-demo-assets-v1" or data.get("status") != "PREPARED":
        raise ConfigurationError("Only complete prepared demo assets can be used")
    if validation_inputs and data.get("demo_data_digest") != file_digest(DEMO_DATA):
        raise ConfigurationError("Release validation data changed")
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


def assembly(data: dict[str, Any], providers: Path, collection: str) -> LiveAssembly:
    if file_digest(providers) != data["providers_digest"]:
        raise ConfigurationError("Provider configuration changed; rebuild and revalidate")
    return LiveAssembly(
        Path.cwd(),
        providers,
        Path(data["dense_pointer"]),
        fasttext_pointer=Path(data["fasttext_pointer"]),
        reviewed_rules=Path(data["rules"]),
        event_collection=collection,
    )


async def activate(store: FeedbackStore, assets: Path, pointer: Path, gate: BudgetSession) -> None:
    data = verify_files(assets)
    await store.require_current(data["source"])
    await verify_remote(data, gate)
    report = read_data(assets.parent / "validation.json")
    if report.get("status") != "PASS" or report.get("assets_digest") != digest(data):
        raise ConfigurationError(
            "Activation needs this exact asset version's successful validation"
        )
    previous = read_data(pointer) if await asyncio.to_thread(pointer.exists) else {}
    resolved = str(await asyncio.to_thread(assets.resolve))
    if previous.get("active") == resolved:
        return
    atomic_json(
        pointer,
        dict(
            format="m18-demo-pointer-v1",
            active=resolved,
            previous=previous.get("active"),
            validation_digest=digest(report),
        ),
    )
