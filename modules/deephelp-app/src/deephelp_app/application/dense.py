"""Idempotent immutable-version import and Dense candidate retrieval, without intent takeover."""

import asyncio
import hashlib
import json
import math
import struct
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from deephelp_app.application.corpus import CorpusPreview, content_hash, digest, normalized
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import (
    CorpusRecord,
    DenseCandidate,
    DenseHit,
    DenseResult,
    DenseScope,
    ErrorCode,
    IntentCode,
)
from deephelp_app.domain.ports import EmbeddingPort
from deephelp_app.domain.registry import complaint_registry

GiB = 1024**3


@dataclass(frozen=True)
class Capacity:
    root_free: int
    milvus_data: int
    memory_used: int
    memory_limit: int
    host_available: int

    def admit(self, rows: int, dimension: int) -> dict[str, int]:
        # FP32 + index/work buffers + scalar/metadata allowance; deliberately conservative.
        data = rows * (dimension * 4 + 16384)
        peak = data * 3 + 32 * 1024**2
        if (
            self.root_free - data < 10 * GiB
            or self.milvus_data + data >= 6 * GiB
            or self.memory_limit <= 0
            or self.memory_used + peak >= self.memory_limit * 0.8
            or self.host_available < peak + 512 * 1024**2
        ):
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Milvus capacity gate denied new batch")
        return {
            "rows": rows,
            "fp32_bytes": rows * dimension * 4,
            "estimated_bytes": data,
            "index_peak_bytes": peak,
        }


class DenseStore(Protocol):
    async def ensure(self, scope: DenseScope, corpus_digest: str) -> None: ...
    async def write(self, scope: DenseScope, rows: list[dict[str, object]]) -> None: ...
    async def read(self, scope: DenseScope, ids: list[str]) -> list[dict[str, object]]: ...
    async def finish(self, scope: DenseScope) -> None: ...
    async def search(
        self, scope: DenseScope, vector: list[float], limit: int, code: IntentCode | None = None
    ) -> list[DenseHit]: ...


def atomic_json(path: Path, data: object) -> None:
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2)
    if len(encoded.encode()) > 2 * 1024**2:
        raise ValueError("Manifest size limit exceeded")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(encoded)
        stream.flush()
        import os

        os.fsync(stream.fileno())
    # Windows scanners can briefly open the destination without FILE_SHARE_DELETE.
    # Keep the old file intact and bound this synchronous commit to 80ms of retry.
    for attempt in range(5):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02)


def load_json(path: Path) -> dict[str, object]:
    """Bounded local state; synchronous saves cannot outlive a cancelled writer."""
    if path.stat().st_size > 2 * 1024**2:
        raise ValueError("Manifest size limit exceeded")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Control state must be an object")
    return value


def existing_json(path: Path) -> dict[str, object] | None:
    return load_json(path) if path.exists() else None


def row_for(
    record: CorpusRecord, preview: CorpusPreview, scope: DenseScope, vector: list[float]
) -> dict[str, object]:
    return {
        "doc_id": record.doc_id,
        "content": record.content,
        "intent_code": record.intent_code.value,
        "namespace": scope.namespace,
        "dataset_version": scope.dataset_version,
        "model_signature": scope.signature.fingerprint,
        "split": record.split,
        "content_hash": content_hash(record),
        "vector": vector,
        "metadata": {
            "source_group": record.source_group,
            "variant_group": record.variant_group,
            "synthetic": True,
            "record_hash": digest(record.model_dump(mode="json")),
            "source_file_sha256": preview.file_sha256,
            "batch_id": digest([preview.file_sha256, scope.fingerprint]),
            "corpus_digest": preview.index_digest,
            "registry_version": scope.registry_version,
            "attributes": record.metadata,
        },
    }


def verify_rows(expected: list[dict[str, object]], actual: list[dict[str, object]]) -> None:
    by_id = {str(row["doc_id"]): row for row in actual}
    if len(by_id) != len(expected) or len(by_id) != len(actual):
        raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Milvus batch readback count mismatch")
    for row in expected:
        stored = by_id.get(str(row["doc_id"]), {})
        if any(stored.get(key) != value for key, value in row.items() if key != "vector"):
            raise AppError(ErrorCode.VERSION_CONFLICT, "Milvus row readback/version mismatch")
        vector = row["vector"]
        actual_vector = stored.get("vector", [])
        if isinstance(vector, list) and vector:
            if (
                not isinstance(actual_vector, list)
                or len(vector) != len(actual_vector)
                or any(
                    not math.isclose(a, b, abs_tol=1e-6, rel_tol=1e-5)
                    for a, b in zip(vector, actual_vector, strict=True)
                )
            ):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "FP32 vector readback mismatch")


def vector_hash(vector: object) -> str:
    """Hash every persisted FP32 component, independent of JSON float formatting."""
    if (
        not isinstance(vector, list)
        or not vector
        or any(not isinstance(v, int | float) or not math.isfinite(v) for v in vector)
    ):
        raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Invalid persisted vector")
    try:
        return hashlib.sha256(struct.pack("<" + "f" * len(vector), *vector)).hexdigest()
    except OverflowError:
        raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Persisted vector exceeds FP32") from None


class DenseImporter:
    def __init__(
        self,
        embeddings: EmbeddingPort,
        store: DenseStore,
        capacity: Callable[[], Awaitable[Capacity]],
        *,
        batch_size: int = 8,
    ) -> None:
        if not 1 <= batch_size <= 20:
            raise ValueError("Import batch size must be 1..20")
        self.embeddings, self.store, self.capacity = embeddings, store, capacity
        self.batch_size = batch_size

    async def import_corpus(
        self, preview: CorpusPreview, scope: DenseScope, budget: ExecutionBudget, manifest: Path
    ) -> dict[str, object]:
        preview.require_valid()
        if not preview.index_records:
            raise ValueError("No authorized train/reference records")
        manifest.parent.mkdir(parents=True, exist_ok=True)
        lock_path = manifest.with_suffix(".lock")
        try:
            lock = lock_path.open("x", encoding="utf-8")
        except FileExistsError:
            raise ValueError("Import locked; inspect crashed writer before recovery") from None
        state: dict[str, object] = {
            "format": "m05-import-v1",
            "scope": scope.model_dump(mode="json"),
            "file_sha256": preview.file_sha256,
            "corpus_digest": preview.index_digest,
            "completed_ids": [],
            "status": "PENDING",
            "vector_hashes": {},
            "split_manifest": [
                {
                    "doc_id": r.doc_id,
                    "split": r.split,
                    "source_group": r.source_group,
                    "variant_group": r.variant_group,
                    "content_hash": content_hash(r),
                    "record_hash": digest(r.model_dump(mode="json")),
                    "intent_code": r.intent_code.value,
                }
                for r in preview.records
            ],
        }
        try:
            previous = existing_json(manifest)
            if previous is not None:
                if any(
                    previous.get(k) != state[k]
                    for k in ("format", "scope", "file_sha256", "corpus_digest", "split_manifest")
                ):
                    raise AppError(ErrorCode.VERSION_CONFLICT, "Import manifest binding changed")
                state = previous
            vector_hashes = state.get("vector_hashes")
            if not isinstance(vector_hashes, dict):
                raise ValueError("Invalid persisted vector hashes")
            state["vector_hashes"] = vector_hashes
            needs_finish = state.get("status") != "VERIFIED"
            completed = state["completed_ids"]
            if (
                not isinstance(completed, list)
                or len(completed) != len(set(completed))
                or not set(completed) <= {r.doc_id for r in preview.index_records}
                or set(vector_hashes) != set(completed)
            ):
                raise ValueError("Invalid completed IDs in import manifest")
            async with asyncio.timeout(budget.remaining_seconds()):
                capacity = await self.capacity()
                state["estimate"] = capacity.admit(
                    len(preview.index_records), scope.signature.dimension
                )
                await self.store.ensure(scope, preview.index_digest)
                atomic_json(manifest, state)
                for start in range(0, len(preview.index_records), self.batch_size):
                    records = preview.index_records[start : start + self.batch_size]
                    # Completed local IDs still need remote readback on every rerun.
                    expected = [row_for(r, preview, scope, []) for r in records]
                    if all(r.doc_id in completed for r in records):
                        actual = await self.store.read(scope, [r.doc_id for r in records])
                        verify_rows(expected, actual)
                        for row in actual:
                            hashed = vector_hash(row["vector"])
                            if (
                                row["doc_id"] in vector_hashes
                                and vector_hashes[row["doc_id"]] != hashed
                            ):
                                raise AppError(
                                    ErrorCode.VERSION_CONFLICT, "Persisted vector hash mismatch"
                                )
                            vector_hashes[row["doc_id"]] = hashed
                        continue
                    budget.remaining_seconds()
                    (await self.capacity()).admit(len(records), scope.signature.dimension)
                    embedded = await self.embeddings.embed([r.content for r in records], budget)
                    if embedded.signature != scope.signature or len(embedded.vectors) != len(
                        records
                    ):
                        raise AppError(
                            ErrorCode.VERSION_CONFLICT, "Embedding signature/row count mismatch"
                        )
                    for vector in embedded.vectors:
                        validate_vector(vector, scope)
                    rows = [
                        row_for(r, preview, scope, v)
                        for r, v in zip(records, embedded.vectors, strict=True)
                    ]
                    # Unknown write outcome: leave batch pending; replay deterministic PK upserts.
                    state["status"] = "IN_PROGRESS"
                    state["pending_ids"] = [r.doc_id for r in records]
                    atomic_json(manifest, state)
                    await self.store.write(scope, rows)
                    needs_finish = True
                    verify_rows(rows, await self.store.read(scope, [r.doc_id for r in records]))
                    for row in rows:
                        vector_hashes[row["doc_id"]] = vector_hash(row["vector"])
                    completed.extend(r.doc_id for r in records if r.doc_id not in completed)
                    state["pending_ids"] = []
                    atomic_json(manifest, state)
                # A verified rerun only reads data. Repeated flushes hit Milvus' 0.1/s
                # limiter and add no durability when this run has not written anything.
                if needs_finish:
                    await self.store.finish(scope)
                state["status"] = "VERIFIED"
                atomic_json(manifest, state)
                return state
        finally:
            lock.close()
            lock_path.unlink()


def verify_manifest_rows(state: dict[str, object], rows: list[dict[str, object]]) -> None:
    manifest_rows = state.get("split_manifest")
    if not isinstance(manifest_rows, list):
        raise ValueError("Invalid split manifest")
    expected = {r["doc_id"]: r for r in manifest_rows if r["split"] in {"reference", "train"}}
    completed = state.get("completed_ids")
    hashes = state.get("vector_hashes")
    if (
        not isinstance(completed, list)
        or not isinstance(hashes, dict)
        or len(rows) != len(expected)
        or set(completed) != set(expected)
        or set(hashes) != set(expected)
    ):
        raise ValueError("Incomplete verified manifest")
    for row in rows:
        label = expected.get(row["doc_id"])
        metadata = row.get("metadata")
        if (
            not label
            or not isinstance(metadata, dict)
            or any(row.get(k) != label[k] for k in ("intent_code", "split", "content_hash"))
            or any(
                metadata.get(k) != label[k]
                for k in ("source_group", "variant_group", "record_hash")
            )
        ):
            raise AppError(ErrorCode.VERSION_CONFLICT, "Activation content/manifest mismatch")
        if digest(normalized(str(row["content"]))) != label["content_hash"]:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Activation content hash mismatch")
        if vector_hash(row["vector"]) != hashes[row["doc_id"]]:
            raise AppError(ErrorCode.VERSION_CONFLICT, "Persisted vector hash mismatch")


def validate_vector(vector: list[float], scope: DenseScope) -> None:
    norm = math.sqrt(sum(v * v for v in vector))
    if (
        len(vector) != scope.signature.dimension
        or not all(math.isfinite(v) for v in vector)
        or not math.isfinite(norm)
        or norm == 0
    ):
        raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Invalid Dense vector")
    if scope.signature.normalization == "l2" and abs(norm - 1) > 0.001:
        raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Dense vector normalization mismatch")


class DenseRetriever:
    def __init__(self, embeddings: EmbeddingPort, store: DenseStore) -> None:
        self.embeddings, self.store = embeddings, store

    async def retrieve(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> DenseResult:
        if not text.strip() or len(text) > 2000 or not 1 <= top_k <= 20:
            raise ValueError("Dense query requires nonblank <=2000 chars and top_k 1..20")
        async with asyncio.timeout(budget.remaining_seconds()):
            embedded = await self.embeddings.embed([text], budget)
            if embedded.signature != scope.signature or len(embedded.vectors) != 1:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Query embedding signature mismatch")
            vector = embedded.vectors[0]
            validate_vector(vector, scope)
            hits = await self.store.search(scope, vector, top_k)
            # Each class gets an independent best-match search. Counts are never summed.
            best: list[DenseHit] = []
            for code in complaint_registry().actionable_codes:
                best.extend(await self.store.search(scope, vector, 1, code))
            best.sort(key=lambda h: (-h.raw_score, h.intent_code.value, h.doc_id))
            candidates = tuple(
                DenseCandidate(
                    intent_code=h.intent_code,
                    raw_score=h.raw_score,
                    rank=rank,
                    evidence_doc_id=h.doc_id,
                )
                for rank, h in enumerate(best, 1)
            )
            return DenseResult(scope=scope, hits=tuple(hits), candidates=candidates)


async def evaluate_dev(
    preview: CorpusPreview,
    scope: DenseScope,
    retriever: DenseRetriever,
    budget: ExecutionBudget,
) -> dict[str, object]:
    preview.require_valid()
    queries = [r for r in preview.records if r.split == "dev"]
    if not queries:
        raise ValueError("No independent dev queries")
    indexed_hashes = {content_hash(r) for r in preview.index_records}
    if any(content_hash(q) in indexed_hashes for q in queries):
        raise ValueError("Dev content leaked into index")
    observations: list[dict[str, object]] = []
    totals: dict[str, dict[str, int]] = {}
    for query in queries:
        result = await retriever.retrieve(query.content, scope, budget)
        doc_codes = [h.intent_code.value for h in result.hits]
        candidate_codes = [c.intent_code.value for c in result.candidates]
        code = query.intent_code.value
        counts = totals.setdefault(
            code,
            {
                "queries": 0,
                "doc_recall_1": 0,
                "doc_recall_3": 0,
                "candidate_recall_1": 0,
                "candidate_recall_2": 0,
            },
        )
        counts["queries"] += 1
        counts["doc_recall_1"] += int(code in doc_codes[:1])
        counts["doc_recall_3"] += int(code in doc_codes[:3])
        counts["candidate_recall_1"] += int(code in candidate_codes[:1])
        counts["candidate_recall_2"] += int(code in candidate_codes[:2])
        observations.append(
            {
                "doc_id": query.doc_id,
                "gold": code,
                "hits": [h.model_dump(mode="json") for h in result.hits],
                "candidates": [c.model_dump(mode="json") for c in result.candidates],
                "top1_correct": bool(doc_codes and doc_codes[0] == code),
            }
        )
    metrics = {
        key: sum(c[key] for c in totals.values()) / len(queries)
        for key in ("doc_recall_1", "doc_recall_3", "candidate_recall_1", "candidate_recall_2")
    }
    return {
        "split": "dev",
        "queries": len(queries),
        "index_digest": preview.index_digest,
        "query_digest": digest([q.model_dump(mode="json") for q in queries]),
        "source_file_sha256": preview.file_sha256,
        "scope": scope.model_dump(mode="json"),
        "metrics": metrics,
        "per_class": totals,
        "observations": observations,
        "failures": [o["doc_id"] for o in observations if not o["top1_correct"]],
        "threshold": None,
        "final_intent_decisions": 0,
    }
