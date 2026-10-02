"""Same-query server-side retrieval. Fusion is a ranking score, never confidence."""

import asyncio
import math
import time
from typing import Protocol

from deephelp_app.dense import validate_vector
from deephelp_app.domain.models import (
    DenseCandidate,
    DenseHit,
    DenseResult,
    DenseScope,
    ErrorCode,
    HybridScope,
)
from deephelp_app.errors import AppError
from deephelp_app.execution import ExecutionBudget
from deephelp_app.ports import EmbeddingPort

ANALYZER: dict[str, object] = {"tokenizer": "jieba", "filter": ["cnalphanumonly", "lowercase"]}


def normalized_fusion(dense: float | None, sparse: float | None, weight: float) -> float:
    """Milvus WeightedRanker norm_score=True, absent route contributes zero."""
    return weight * ((1 + dense) / 2 if dense is not None else 0) + (1 - weight) * (
        2 * math.atan(sparse) / math.pi if sparse is not None else 0
    )


class HybridStore(Protocol):
    async def routes(
        self, scope: HybridScope, text: str, vector: list[float], limit: int, weight: float
    ) -> dict[str, list[DenseHit]]: ...


def result_for(
    scope: HybridScope, mode: str, hits: list[DenseHit], limit: int, weight: float, elapsed: float
) -> DenseResult:
    # Max evidence per intent, never summed by class population. All routes use the same K.
    best: dict[str, DenseHit] = {}
    for hit in hits:
        code = hit.intent_code.value
        if code not in best or hit.raw_score > best[code].raw_score:
            best[code] = hit
    ordered = sorted(best.values(), key=lambda h: (-h.raw_score, h.intent_code.value, h.doc_id))
    return DenseResult(
        scope=scope,
        hits=tuple(hits),
        candidates=tuple(
            DenseCandidate(
                intent_code=h.intent_code,
                raw_score=h.raw_score,
                score_kind=h.score_kind,
                rank=i,
                evidence_doc_id=h.doc_id,
            )
            for i, h in enumerate(ordered, 1)
        ),
        policy_version="hybrid-weighted-v1",
        retrieval_mode=mode,  # type: ignore[arg-type]
        candidate_budget=limit,
        dense_weight=weight,
        elapsed_ms=elapsed,
    )


class HybridRetriever:
    def __init__(
        self, embeddings: EmbeddingPort, store: HybridStore, *, dense_weight: float = 0.5
    ) -> None:
        if (
            isinstance(dense_weight, bool)
            or not math.isfinite(dense_weight)
            or not 0 <= dense_weight <= 1
        ):
            raise ValueError("Dense weight must be finite within 0..1")
        self.embeddings, self.store, self.dense_weight = embeddings, store, dense_weight

    async def compare(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> dict[str, DenseResult]:
        if not isinstance(scope, HybridScope):
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Hybrid requires a signed intent HybridScope"
            )
        if not text.strip() or len(text) > 2000 or not 1 <= top_k <= 20:
            raise ValueError("Hybrid query requires nonblank <=2000 chars and top_k 1..20")
        async with asyncio.timeout(budget.remaining_seconds()):
            embedded = await self.embeddings.embed([text], budget)
            if embedded.signature != scope.signature or len(embedded.vectors) != 1:
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Hybrid query embedding signature mismatch"
                )
            vector = embedded.vectors[0]
            validate_vector(vector, scope)
            started = time.perf_counter()
            routes = await self.store.routes(scope, text, vector, top_k, self.dense_weight)
            if set(routes) != {"dense", "bm25", "hybrid"} or any(
                len(hits) > top_k or any(h.scope != scope for h in hits) for hits in routes.values()
            ):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Hybrid route scope/budget mismatch")
            elapsed = (time.perf_counter() - started) * 1000
            return {
                mode: result_for(scope, mode, hits, top_k, self.dense_weight, elapsed)
                for mode, hits in routes.items()
            }

    async def retrieve(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> DenseResult:
        return (await self.compare(text, scope, budget, top_k=top_k))["hybrid"]
