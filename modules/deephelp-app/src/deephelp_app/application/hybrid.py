"""Same-query server-side retrieval. Fusion is a ranking score, never confidence."""

import asyncio
import math
import time
from typing import Literal, Protocol

from deephelp_app.application.dense import validate_vector
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import (
    DenseCandidate,
    DenseHit,
    DenseResult,
    DenseScope,
    ErrorCode,
    HybridScope,
    RerankDocument,
    RerankPolicy,
    RerankRequest,
    RetrievalRerank,
)
from deephelp_app.domain.ports import EmbeddingPort, RerankPort

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


async def rerank_result(
    text: str,
    baseline: DenseResult,
    port: RerankPort,
    policy: RerankPolicy,
    budget: ExecutionBudget,
    *,
    top_k: int,
) -> DenseResult:
    """Reorder immutable recalled documents; preserve all raw fusion evidence scores."""
    original = baseline.hits
    if (
        type(top_k) is not int
        or not 1 <= top_k <= policy.candidate_budget
        or len(original) > policy.candidate_budget
        or baseline.retrieval_mode != "hybrid"
        or len({h.doc_id for h in original}) != len(original)
    ):
        raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Rerank needs bounded Hybrid documents")
    before, started = budget.attempts_used, time.perf_counter()
    ids = tuple(hit.doc_id for hit in original)
    result = None
    reason = None
    error_code = None
    status: Literal["passed", "not_run", "fallback"] = "not_run"
    if not original or not policy.enabled:
        reason = "empty_recall" if not original else "disabled"
    else:
        documents = tuple(
            RerankDocument(
                doc_id=hit.doc_id,
                content=hit.content,
                intent_code=hit.intent_code,
                source=hit.scope.dataset_version,
            )
            for hit in original
        )
        request = RerankRequest(
            query=text, documents=documents, top_n=len(documents), instruct=policy.instruct
        )
        try:
            result = await port.rerank(request, budget)
            # Validate custom ports as strictly as the provider adapter.
            if (
                result.model != policy.model
                or result.instruct != policy.instruct
                or (
                    len(result.hits) != len(documents)
                    or any(
                        hit.index >= len(documents) or hit.document != documents[hit.index]
                        for hit in result.hits
                    )
                )
            ):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Rerank port evidence mismatch")
            result = type(result).model_validate(result.model_dump())
            status = "passed"
        except AppError as exc:
            if policy.failure_policy != "hybrid_on_unavailable" or exc.code not in {
                ErrorCode.TIMEOUT,
                ErrorCode.UPSTREAM_UNAVAILABLE,
                ErrorCode.RATE_LIMITED,
            }:
                raise
            status, reason, error_code = "fallback", "upstream_unavailable", exc.code
    hits = tuple(original[hit.index] for hit in result.hits) if result else original
    best: dict[str, DenseHit] = {}
    for hit in hits:
        best.setdefault(hit.intent_code.value, hit)  # Ranked best evidence, never class sums.
    ordered = list(best.values())[:top_k]
    return DenseResult(
        scope=baseline.scope,
        hits=hits,
        candidates=tuple(
            DenseCandidate(
                intent_code=hit.intent_code,
                raw_score=hit.raw_score,
                score_kind=hit.score_kind,
                rank=i,
                evidence_doc_id=hit.doc_id,
            )
            for i, hit in enumerate(ordered, 1)
        ),
        policy_version="m31-rerank-v1",
        retrieval_mode="hybrid_rerank",
        candidate_budget=policy.candidate_budget,
        dense_weight=baseline.dense_weight,
        elapsed_ms=(baseline.elapsed_ms or 0) + (time.perf_counter() - started) * 1000,
        rerank=RetrievalRerank(
            policy=policy,
            status=status,
            reason=reason,
            input_doc_ids=ids,
            result=result,
            elapsed_ms=(time.perf_counter() - started) * 1000,
            attempts=budget.attempts_used - before,
            error_code=error_code,
        ),
    )


class HybridRetriever:
    def __init__(
        self,
        embeddings: EmbeddingPort,
        store: HybridStore,
        *,
        dense_weight: float = 0.5,
        reranker: RerankPort | None = None,
        rerank_policy: RerankPolicy | None = None,
        candidate_budget: int | None = None,
    ) -> None:
        if (
            isinstance(dense_weight, bool)
            or not math.isfinite(dense_weight)
            or not 0 <= dense_weight <= 1
        ):
            raise ValueError("Dense weight must be finite within 0..1")
        self.embeddings, self.store, self.dense_weight = embeddings, store, dense_weight
        if (reranker is None) != (rerank_policy is None):
            raise ValueError("Reranker and frozen policy must be configured together")
        if candidate_budget is not None and (
            type(candidate_budget) is not int or not 1 <= candidate_budget <= 20
        ):
            raise ValueError("Candidate budget must be 1..20")
        self.reranker, self.rerank_policy = reranker, rerank_policy
        self.candidate_budget = (
            rerank_policy.candidate_budget if rerank_policy else candidate_budget
        )

    async def compare(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> dict[str, DenseResult]:
        if not isinstance(scope, HybridScope):
            raise AppError(
                ErrorCode.VERSION_CONFLICT, "Hybrid requires a signed intent HybridScope"
            )
        if not text.strip() or len(text) > 2000 or type(top_k) is not int or not 1 <= top_k <= 20:
            raise ValueError("Hybrid query requires nonblank <=2000 chars and top_k 1..20")
        limit = (
            top_k
            if self.rerank_policy and not self.rerank_policy.enabled
            else self.candidate_budget or top_k
        )
        if limit < top_k:
            raise ValueError("Recall budget is below final top_k")
        async with asyncio.timeout(budget.remaining_seconds()):
            embedded = await self.embeddings.embed([text], budget)
            if embedded.signature != scope.signature or len(embedded.vectors) != 1:
                raise AppError(
                    ErrorCode.VERSION_CONFLICT, "Hybrid query embedding signature mismatch"
                )
            vector = embedded.vectors[0]
            validate_vector(vector, scope)
            started = time.perf_counter()
            routes = await self.store.routes(scope, text, vector, limit, self.dense_weight)
            if set(routes) != {"dense", "bm25", "hybrid"} or any(
                len(hits) > limit or any(h.scope != scope for h in hits) for hits in routes.values()
            ):
                raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Hybrid route scope/budget mismatch")
            elapsed = (time.perf_counter() - started) * 1000
            results = {
                mode: result_for(scope, mode, hits, limit, self.dense_weight, elapsed)
                for mode, hits in routes.items()
            }
            if self.reranker is not None and self.rerank_policy is not None:
                results["hybrid_rerank"] = await rerank_result(
                    text, results["hybrid"], self.reranker, self.rerank_policy, budget, top_k=top_k
                )
            return results

    async def retrieve(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> DenseResult:
        results = await self.compare(text, scope, budget, top_k=top_k)
        return results[
            "hybrid_rerank" if self.rerank_policy and self.rerank_policy.enabled else "hybrid"
        ]
