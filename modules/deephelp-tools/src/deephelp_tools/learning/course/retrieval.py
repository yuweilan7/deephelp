"""固定向量/排序端口，复用生产Hybrid与每意图归并；不证明模型质量。"""

from deephelp_app.application.hybrid import HybridRetriever, normalized_fusion
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import (
    EmbeddingResult,
    EmbeddingSignature,
    ModelUsage,
    RerankHit,
    RerankRequest,
    RerankResult,
)
from deephelp_app.domain.models.retrieval import DenseHit, HybridScope, RerankPolicy


async def retrieval_demo() -> dict[str, object]:
    signature = EmbeddingSignature(provider="synthetic", model="course-vector", dimension=2)
    scope = HybridScope(
        namespace="course",
        dataset_version="synthetic-v1",
        signature=signature,
        index_kind="intent_hybrid",
    )
    hits = [
        DenseHit(
            doc_id="discount-a",
            content="优惠未到账",
            intent_code=IntentCode.DISCOUNT_MISSING,
            raw_score=normalized_fusion(0.8, 4.0, 0.5),
            score_kind="fusion",
            raw_dense=0.8,
            raw_sparse=4.0,
            fusion_score=normalized_fusion(0.8, 4.0, 0.5),
            matched_sources=("dense", "bm25"),
            metadata={},
            scope=scope,
        ),
        DenseHit(
            doc_id="discount-b",
            content="支付没有减价",
            intent_code=IntentCode.DISCOUNT_MISSING,
            raw_score=normalized_fusion(0.6, 2.0, 0.5),
            score_kind="fusion",
            raw_dense=0.6,
            raw_sparse=2.0,
            fusion_score=normalized_fusion(0.6, 2.0, 0.5),
            matched_sources=("dense", "bm25"),
            metadata={},
            scope=scope,
        ),
        DenseHit(
            doc_id="coupon",
            content="券用不了",
            intent_code=IntentCode.COUPON_UNUSABLE,
            raw_score=normalized_fusion(0.4, 1.0, 0.5),
            score_kind="fusion",
            raw_dense=0.4,
            raw_sparse=1.0,
            fusion_score=normalized_fusion(0.4, 1.0, 0.5),
            matched_sources=("dense", "bm25"),
            metadata={},
            scope=scope,
        ),
    ]

    class Embeddings:
        calls = 0

        async def embed(self, texts: list[str], budget: ExecutionBudget) -> EmbeddingResult:
            self.calls += 1
            budget.claim_attempt(retry=False)
            return EmbeddingResult(vectors=[[1.0, 0.0]], signature=signature, usage=ModelUsage())

    class Store:
        async def routes(
            self, scope: HybridScope, text: str, vector: list[float], limit: int, weight: float
        ) -> dict[str, list[DenseHit]]:
            assert vector == [1.0, 0.0] and limit == 3
            return {
                "dense": [
                    DenseHit.model_validate(
                        h.model_copy(
                            update={"score_kind": "cosine", "raw_score": h.raw_dense}
                        ).model_dump()
                    )
                    for h in hits
                ],
                "bm25": [
                    DenseHit.model_validate(
                        h.model_copy(
                            update={"score_kind": "bm25", "raw_score": h.raw_sparse}
                        ).model_dump()
                    )
                    for h in hits
                ],
                "hybrid": hits,
            }

    class FixedReranker:
        calls = 0

        async def rerank(self, request: RerankRequest, budget: ExecutionBudget) -> RerankResult:
            self.calls += 1
            budget.claim_attempt(retry=False)
            assert tuple(d.doc_id for d in request.documents) == tuple(h.doc_id for h in hits)
            # 固定反转顺序，让读者看到第二份优惠证据如何成为该意图最佳证据。
            return RerankResult(
                model="qwen3.7-text-rerank",
                instruct=request.instruct,
                usage=None,
                hits=tuple(
                    RerankHit(index=i, document=request.documents[i], score=score)
                    for i, score in ((2, 0.95), (1, 0.8), (0, 0.2))
                ),
            )

    embeddings, reranker = Embeddings(), FixedReranker()
    retriever = HybridRetriever(
        embeddings,
        Store(),
        reranker=reranker,
        rerank_policy=RerankPolicy(candidate_budget=3, top_n=3, instruct=None),
    )
    results = await retriever.compare("券用不了", scope, ExecutionBudget.start(10, 4, 0), top_k=2)
    ranked = results["hybrid_rerank"]
    assert embeddings.calls == reranker.calls == 1
    assert [c.evidence_doc_id for c in ranked.candidates] == ["coupon", "discount-b"]
    assert {h.doc_id for h in ranked.hits} == {h.doc_id for h in hits}
    assert all(
        next(h for h in hits if h.doc_id == r.doc_id).raw_score == r.raw_score for r in ranked.hits
    )
    return {
        "evidence_kind": "synthetic_ranking_ports",
        "remote_model_calls": 0,
        "embedding_actions": embeddings.calls,
        "rerank_actions": reranker.calls,
        "candidate_ids": [h.doc_id for h in hits],
        "routes": {
            mode: [c.evidence_doc_id for c in result.candidates] for mode, result in results.items()
        },
        "fusion_example": normalized_fusion(0.8, 4.0, 0.5),
        "raw_scores_preserved": True,
    }
