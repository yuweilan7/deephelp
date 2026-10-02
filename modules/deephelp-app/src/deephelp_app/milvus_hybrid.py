"""Native Milvus Jieba/BM25 + COSINE schema. Existing M05 collections stay immutable."""

import json
import math
import time
from pathlib import Path
from typing import Any, Literal

from pymilvus import (  # type: ignore[import-untyped]
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    WeightedRanker,
)

from deephelp_app.dense import atomic_json, existing_json, load_json, verify_manifest_rows
from deephelp_app.domain.models import DenseHit, DenseScope, ErrorCode, HybridScope
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.hybrid import ANALYZER, normalized_fusion
from deephelp_app.milvus_dense import FIELDS, OUTPUT, MilvusDenseStore


class MilvusHybridStore(MilvusDenseStore):
    latencies: dict[str, float]

    def _description(self, scope: DenseScope, corpus_digest: str) -> str:
        return json.dumps(
            {
                "format": "m09-hybrid-fp32-bm25-v1",
                "scope": scope.model_dump(mode="json"),
                "corpus_digest": corpus_digest,
                "analyzer": ANALYZER,
            },
            sort_keys=True,
        )

    async def validate(self, scope: DenseScope, corpus_digest: str | None = None) -> str:
        if not isinstance(scope, HybridScope):
            raise AppError(ErrorCode.VERSION_CONFLICT, "Hybrid intent scope required")
        details = await self._rpc(
            lambda: self.client.describe_collection(
                scope.collection_name, timeout=None, retry_times=0
            )
        )
        try:
            desc = json.loads(details["description"])
            fields = {f["name"]: f for f in details["fields"]}
            if (
                desc["format"] != "m09-hybrid-fp32-bm25-v1"
                or desc["scope"] != scope.model_dump(mode="json")
                or desc["analyzer"] != ANALYZER
                or (corpus_digest is not None and desc["corpus_digest"] != corpus_digest)
                or details["auto_id"]
                or details["enable_dynamic_field"]
                or set(fields) != set(FIELDS) | {"metadata", "vector", "sparse"}
            ):
                raise ValueError
            for key, (kind, length) in FIELDS.items():
                if (
                    int(fields[key]["type"]) != int(kind)
                    or int(fields[key]["params"]["max_length"]) != length
                ):
                    raise ValueError
            params = fields["content"]["params"]
            analyzer = params["analyzer_params"]
            if isinstance(analyzer, str):
                analyzer = json.loads(analyzer)
            if analyzer != ANALYZER or str(params["enable_analyzer"]).lower() != "true":
                raise ValueError
            if (
                not fields["doc_id"].get("is_primary")
                or int(fields["metadata"]["type"]) != int(DataType.JSON)
                or int(fields["vector"]["type"]) != int(DataType.FLOAT_VECTOR)
                or int(fields["vector"]["params"]["dim"]) != scope.signature.dimension
                or int(fields["sparse"]["type"]) != int(DataType.SPARSE_FLOAT_VECTOR)
            ):
                raise ValueError
            functions = details.get("functions", [])
            if (
                len(functions) != 1
                or functions[0]["input_field_names"] != ["content"]
                or functions[0]["output_field_names"] != ["sparse"]
                or int(functions[0]["type"]) != int(FunctionType.BM25)
            ):
                raise ValueError
            return str(desc["corpus_digest"])
        except ValueError, KeyError, TypeError:
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Hybrid schema/analyzer/signature mismatch"
            ) from None

    async def ensure(self, scope: DenseScope, corpus_digest: str) -> None:
        if not isinstance(scope, HybridScope):
            raise AppError(ErrorCode.VERSION_CONFLICT, "Hybrid intent scope required")
        exists = await self._rpc(
            lambda: self.client.has_collection(scope.collection_name, timeout=None, retry_times=0)
        )
        if not exists:
            collections = await self._rpc(
                lambda: self.client.list_collections(timeout=None, retry_times=0)
            )
            if sum(c.startswith("m09_intent_") for c in collections) >= 2:
                raise AppError(
                    ErrorCode.BUDGET_EXHAUSTED, "Two Hybrid versions allowed; maintenance required"
                )
            schema = MilvusClient.create_schema(
                auto_id=False,
                enable_dynamic_field=False,
                description=self._description(scope, corpus_digest),
            )
            for name, (kind, length) in FIELDS.items():
                extras = (
                    {"enable_analyzer": True, "analyzer_params": ANALYZER}
                    if name == "content"
                    else {}
                )
                schema.add_field(
                    name, kind, max_length=length, is_primary=name == "doc_id", **extras
                )
            schema.add_field("metadata", DataType.JSON)
            schema.add_field("vector", DataType.FLOAT_VECTOR, dim=scope.signature.dimension)
            schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)
            schema.add_function(
                Function(
                    name="content_bm25",
                    input_field_names=["content"],
                    output_field_names=["sparse"],
                    function_type=FunctionType.BM25,
                )
            )
            await self._rpc(
                lambda: self.client.create_collection(
                    scope.collection_name,
                    schema=schema,
                    num_shards=1,
                    consistency_level="Strong",
                    timeout=None,
                    retry_times=0,
                )
            )
        await self.validate(scope, corpus_digest)
        names = await self._rpc(
            lambda: self.client.list_indexes(scope.collection_name, timeout=None, retry_times=0)
        )
        params = MilvusClient.prepare_index_params()
        if "vector" not in names:
            params.add_index("vector", index_name="vector", index_type="FLAT", metric_type="COSINE")
        if "sparse" not in names:
            params.add_index(
                "sparse",
                index_name="sparse",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
                params={"inverted_index_algo": "DAAT_MAXSCORE", "bm25_k1": 1.2, "bm25_b": 0.75},
            )
        if {"vector", "sparse"} - set(names):
            await self._rpc(
                lambda: self.client.create_index(
                    scope.collection_name, index_params=params, timeout=None, retry_times=0
                )
            )
        for name, kind, metric in (
            ("vector", "FLAT", "COSINE"),
            ("sparse", "SPARSE_INVERTED_INDEX", "BM25"),
        ):
            index = await self._rpc(
                lambda name=name: self.client.describe_index(  # type: ignore[misc]
                    scope.collection_name, name, timeout=None, retry_times=0
                )
            )
            if index.get("index_type") != kind or index.get("metric_type") != metric:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Hybrid index/metric mismatch")
        await self._rpc(
            lambda: self.client.load_collection(scope.collection_name, timeout=None, retry_times=0)
        )

    async def analyze(self, texts: list[str], scope: HybridScope | None = None) -> list[list[str]]:
        kw: dict[str, object] = (
            {"collection_name": scope.collection_name, "field_name": "content"}
            if scope
            else {"analyzer_params": ANALYZER}
        )
        rows = await self._rpc(
            lambda: self.client.run_analyzer(texts, timeout=None, retry_times=0, **kw)
        )
        return [list(r.tokens) for r in rows]

    def _hits(
        self, scope: HybridScope, rows: list[dict[str, Any]], kind: Literal["cosine", "bm25"]
    ) -> list[DenseHit]:
        hits = []
        for rank, row in enumerate(rows, 1):
            entity = row["entity"]
            if (
                entity["namespace"] != scope.namespace
                or entity["dataset_version"] != scope.dataset_version
                or entity["model_signature"] != scope.signature.fingerprint
                or entity["split"] not in {"reference", "train"}
            ):
                raise AppError(ErrorCode.VERSION_CONFLICT, "Hybrid result scope mismatch")
            hits.append(
                DenseHit(
                    doc_id=entity["doc_id"],
                    intent_code=entity["intent_code"],
                    content=entity["content"],
                    raw_score=row["distance"],
                    score_kind=kind,
                    metadata=entity["metadata"],
                    scope=scope,
                    rank=rank,
                    matched_sources=("dense",) if kind == "cosine" else ("bm25",),
                )
            )
        return hits

    async def routes(
        self, scope: HybridScope, text: str, vector: list[float], limit: int, weight: float
    ) -> dict[str, list[DenseHit]]:
        await self.validate(scope)
        expression = self.filter(scope)
        kw = dict(output_fields=OUTPUT, consistency_level="Strong", timeout=None, retry_times=0)
        results: dict[str, list[DenseHit]] = {}
        self.latencies = {}
        for mode, field, data, metric in (
            ("dense", "vector", [vector], "COSINE"),
            ("bm25", "sparse", [text], "BM25"),
        ):
            started = time.perf_counter()
            rows = await self._rpc(
                lambda data=data, field=field, metric=metric: self.client.search(  # type: ignore[misc]
                    scope.collection_name,
                    data=data,
                    anns_field=field,
                    filter=expression,
                    limit=limit,
                    search_params={"metric_type": metric, "params": {}},
                    **kw,
                )
            )
            self.latencies[mode] = (time.perf_counter() - started) * 1000
            results[mode] = self._hits(scope, rows[0], "cosine" if mode == "dense" else "bm25")
        reqs = [
            AnnSearchRequest([vector], "vector", {"metric_type": "COSINE"}, limit, expr=expression),
            AnnSearchRequest([text], "sparse", {"metric_type": "BM25"}, limit, expr=expression),
        ]
        started = time.perf_counter()
        rows = await self._rpc(
            lambda: self.client.hybrid_search(
                scope.collection_name,
                reqs=reqs,
                ranker=WeightedRanker(weight, 1 - weight, norm_score=True),
                limit=limit,
                **kw,
            )
        )
        self.latencies["hybrid"] = (time.perf_counter() - started) * 1000
        dense = {h.doc_id: h.raw_score for h in results["dense"]}
        sparse = {h.doc_id: h.raw_score for h in results["bm25"]}
        fused = []
        # Hybrid candidates come from these exact bounded ANN routes. Verify every score.
        for h in self._hits(scope, rows[0], "cosine"):
            d, s = dense.get(h.doc_id), sparse.get(h.doc_id)
            expected = normalized_fusion(d, s, weight)
            if (d is None and s is None) or not math.isclose(h.raw_score, expected, abs_tol=2e-5):
                raise AppError(
                    ErrorCode.MODEL_OUTPUT_INVALID, "Server fusion score/provenance mismatch"
                )
            fused.append(
                h.model_copy(
                    update={
                        "score_kind": "fusion",
                        "raw_dense": d,
                        "raw_sparse": s,
                        "fusion_score": h.raw_score,
                        "matched_sources": tuple(
                            m for m, v in (("dense", d), ("bm25", s)) if v is not None
                        ),
                    }
                )
            )
        results["hybrid"] = fused
        return results


async def publish_hybrid(
    store: MilvusHybridStore,
    scope: HybridScope,
    manifest: Path,
    pointer: Path,
    selection: dict[str, object],
) -> None:
    """Publish the verified corpus and its complete frozen policy in one atomic replacement."""
    state = load_json(manifest)
    if state.get("status") != "VERIFIED" or state.get("scope") != scope.model_dump(mode="json"):
        raise ConfigurationError("Hybrid publication requires this scope's verified manifest")
    await store.validate(scope, str(state["corpus_digest"]))
    ids = state.get("completed_ids")
    if not isinstance(ids, list):
        raise ConfigurationError("Invalid publication IDs")
    rows = await store.read(scope, ids)
    verify_manifest_rows(state, rows)
    if await store.count(scope, filtered=False) != len(rows):
        raise ConfigurationError("Hybrid publication count mismatch")
    previous = existing_json(pointer)
    active = {
        "format": "m09-pointer-v1",
        "active": scope.model_dump(mode="json"),
        "manifest": str(manifest),
        "corpus_digest": state["corpus_digest"],
        "dense_weight": selection["dense_weight"],
        "selection": selection,
        "previous": (
            {k: v for k, v in previous.items() if k != "previous"}
            if previous and previous.get("active") != scope.model_dump(mode="json")
            else previous.get("previous")
            if previous
            else None
        ),
    }
    atomic_json(pointer, active)
