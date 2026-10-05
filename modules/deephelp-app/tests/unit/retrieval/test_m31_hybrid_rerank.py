import asyncio

import pytest
from pydantic import ValidationError

from deephelp_app.application.cascade import retrieval_reason
from deephelp_app.application.corpus import digest
from deephelp_app.application.dense import reference_matches, row_for
from deephelp_app.application.hybrid import HybridRetriever, rerank_result, result_for
from deephelp_app.application.retrieval_policy import validate_published_selection
from deephelp_app.application.text_entity import TextPolicy, clean_text
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.common import ErrorCode
from deephelp_app.domain.models.intents import IntentCode
from deephelp_app.domain.models.model_io import (
    EmbeddingResult,
    EmbeddingSignature,
    ModelUsage,
    RerankHit,
    RerankResult,
)
from deephelp_app.domain.models.retrieval import DenseHit, HybridScope, RerankPolicy
from deephelp_app.domain.models.text import DemandType, TextEntityResult
from deephelp_tools.evaluation.retrieval.hybrid_eval import dataset, query_digest
from deephelp_tools.evaluation.retrieval.rerank_cli import OPTIONS, choose

pytestmark = pytest.mark.unit
SIG = EmbeddingSignature(provider="synthetic", model="offline-only", dimension=3)
SCOPE = HybridScope(
    namespace="synthetic", dataset_version="unit-v1", signature=SIG, index_kind="intent_hybrid"
)
POLICY = RerankPolicy(candidate_budget=6, top_n=6, instruct="similar")


def baseline():
    codes = [IntentCode.DISCOUNT_MISSING] * 4 + [
        IntentCode.COUPON_UNUSABLE,
        IntentCode.ORDER_ACTIVITY_QUERY,
    ]
    hits = [
        DenseHit(
            doc_id=f"doc-{i}",
            content=f"reference {i}",
            intent_code=code,
            scope=SCOPE,
            raw_score=0.9 - i / 100,
            score_kind="fusion",
            fusion_score=0.9 - i / 100,
            raw_dense=0.8 - i / 100,
            matched_sources=("dense",),
            metadata={},
        )
        for i, code in enumerate(codes)
    ]
    return result_for(SCOPE, "hybrid", hits, 6, 0.5, 1)


class Reranker:
    def __init__(self, error=None):
        self.requests = []
        self.error = error

    async def rerank(self, request, budget):
        self.requests.append(request)
        budget.claim_attempt(retry=False, tokens=100)
        if self.error:
            raise self.error
        return RerankResult(
            model=POLICY.model,
            instruct=request.instruct,
            usage=None,
            hits=tuple(
                RerankHit(index=i, document=request.documents[i], score=float(10 - rank))
                for rank, i in enumerate([4, 5, 2, 0, 1, 3])
            ),
        )


def budget():
    return ExecutionBudget.start(5, 20, 0, token_limit=10000)


async def test_recall_documents_before_intent_grouping_and_keep_raw_scores():
    port, original = Reranker(), baseline()
    run = budget()
    result = await rerank_result("coupon", original, port, POLICY, run, top_k=3)
    assert len(port.requests[0].documents) == 6 and port.requests[0].top_n == 6
    assert [c.evidence_doc_id for c in result.candidates] == ["doc-4", "doc-5", "doc-2"]
    assert result.candidates[2].raw_score == original.hits[2].raw_score
    assert result.hits[0] is original.hits[4]
    assert result.hits[0].raw_dense == original.hits[4].raw_dense
    assert result.rerank.result.hits[0].score == 10
    assert run.attempts_used == result.rerank.attempts == 1
    prepared = TextEntityResult(
        message_id="m",
        clean=clean_text("coupon", TextPolicy()),
        demand_type=DemandType.MAIN,
        layers=[{"layer": "regex", "elapsed_ms": 0, "accepted": 0}],
    )
    assert retrieval_reason(result, prepared, None) == "rerank_requires_semantic_confirmation"


@pytest.mark.parametrize("disabled,empty", [(True, False), (False, True)])
async def test_disabled_or_empty_has_zero_rerank_calls(disabled, empty):
    original = baseline()
    if empty:
        original = result_for(SCOPE, "hybrid", [], 6, 0.5, 0)
    policy = POLICY.model_copy(update={"enabled": not disabled})
    port, run = Reranker(), budget()
    result = await rerank_result("query", original, port, policy, run, top_k=3)
    assert result.rerank.status == "not_run" and result.rerank.result is None
    assert not port.requests and run.attempts_used == 0


@pytest.mark.parametrize("code", list(ErrorCode))
async def test_only_published_availability_fallback_retains_reservation(code):
    policy = POLICY.model_copy(update={"failure_policy": "hybrid_on_unavailable"})
    port, run = Reranker(AppError(code, "synthetic")), budget()
    allowed = {ErrorCode.TIMEOUT, ErrorCode.UPSTREAM_UNAVAILABLE, ErrorCode.RATE_LIMITED}
    if code in allowed:
        result = await rerank_result("query", baseline(), port, policy, run, top_k=3)
        assert result.rerank.status == "fallback" and result.rerank.error_code == code
        assert result.hits == baseline().hits
    else:
        with pytest.raises(AppError):
            await rerank_result("query", baseline(), port, policy, run, top_k=3)
    assert run.uncertain_attempts == 1 and run.token_upper_bound == 100


async def test_cancel_propagates():
    port = Reranker(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await rerank_result("query", baseline(), port, POLICY, budget(), top_k=3)


async def test_four_routes_use_one_vector_and_explicit_wider_budget():
    class Embeddings:
        calls = 0

        async def embed(self, texts, run):
            self.calls += 1
            return EmbeddingResult(signature=SIG, vectors=[[1.0, 0.0, 0.0]], usage=ModelUsage())

    class Store:
        async def routes(self, scope, text, vector, limit, weight):
            assert limit == 6 and vector == [1.0, 0.0, 0.0]
            return {mode: list(baseline().hits) for mode in ("dense", "bm25", "hybrid")}

    embeddings, port = Embeddings(), Reranker()
    results = await HybridRetriever(
        embeddings, Store(), reranker=port, rerank_policy=POLICY
    ).compare("query", SCOPE, budget(), top_k=3)
    assert embeddings.calls == len(port.requests) == 1
    assert set(results) == {"dense", "bm25", "hybrid", "hybrid_rerank"}
    assert len(results["hybrid_rerank"].candidates) == 3
    assert len(results["hybrid_rerank"].hits) == 6


@pytest.mark.parametrize("change", [{"top_n": 3}, {"candidate_budget": True}])
def test_policy_requires_complete_document_ranking(change):
    with pytest.raises(ValidationError):
        RerankPolicy.model_validate({**POLICY.model_dump(), **change})


async def test_disabled_policy_matches_legacy_recall_budget_and_results():
    class Embeddings:
        async def embed(self, texts, run):
            return EmbeddingResult(signature=SIG, vectors=[[1.0, 0.0, 0.0]], usage=ModelUsage())

    class Store:
        calls = []

        async def routes(self, scope, text, vector, limit, weight):
            self.calls.append(limit)
            return {mode: list(baseline().hits[:limit]) for mode in ("dense", "bm25", "hybrid")}

    store, port = Store(), Reranker()
    legacy = await HybridRetriever(Embeddings(), store).retrieve("q", SCOPE, budget())
    disabled = await HybridRetriever(
        Embeddings(),
        store,
        reranker=port,
        rerank_policy=POLICY.model_copy(update={"enabled": False}),
    ).retrieve("q", SCOPE, budget())
    assert store.calls == [3, 3] and not port.requests
    assert legacy.hits == disabled.hits and legacy.candidates == disabled.candidates


def test_dev_objective_uses_frozen_tiebreak_without_test_labels():
    values = {key: {"candidate_recall_1": 0.8, "mrr": 0.9} for key in OPTIONS}
    assert choose(values) == "n6-semantic"
    values["n12-default"]["mrr"] = 0.95
    assert choose(values) == "n12-default"


def test_published_selection_checks_policy_fingerprint_and_distinct_final_budget():
    corpus, queries = dataset()
    value = {
        "format": "m31-dev-selection-v1",
        "scope": SCOPE.model_dump(mode="json"),
        "index_digest": corpus.index_digest,
        "dev_digest": query_digest(queries["dev"]),
        "test_digest": query_digest(queries["test"]),
        "candidate_budget": 6,
        "top_k": 3,
        "dense_weight": 0.5,
        "analyzer": {"tokenizer": "jieba", "filter": ["cnalphanumonly", "lowercase"]},
        "rerank_policy": POLICY.model_dump(mode="json"),
        "rerank_fingerprint": POLICY.fingerprint,
    }
    value["selection_digest"] = digest(value)
    validate_published_selection(value, SCOPE, corpus.index_digest, 3)
    value["rerank_fingerprint"] = "0" * 64
    value["selection_digest"] = digest({k: v for k, v in value.items() if k != "selection_digest"})
    from deephelp_app.domain.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        validate_published_selection(value, SCOPE, corpus.index_digest, 3)


def test_import_provenance_wrapper_keeps_owned_attributes_and_rejects_injected_constraints():
    corpus, _ = dataset()
    record = corpus.index_records[0]
    metadata = row_for(record, corpus, SCOPE, [1.0, 0.0, 0.0])["metadata"]
    hit = DenseHit(
        doc_id=record.doc_id,
        content=record.content,
        intent_code=record.intent_code,
        metadata=metadata,
        scope=SCOPE,
        raw_score=0.8,
    )
    assert reference_matches(hit, record, corpus.index_digest)
    assert not reference_matches(hit, record, "0" * 64)
    changed = hit.model_copy(
        update={"metadata": {**metadata, "entity_constraints": {"order_id": "x"}}}
    )
    assert not reference_matches(changed, record, corpus.index_digest)


async def test_foreign_recall_text_is_rejected_before_any_rerank_dispatch():
    corpus, _ = dataset()

    class Embeddings:
        async def embed(self, texts, run):
            return EmbeddingResult(signature=SIG, vectors=[[1.0, 0.0, 0.0]], usage=ModelUsage())

    class Store:
        async def routes(self, *args):
            return {mode: list(baseline().hits) for mode in ("dense", "bm25", "hybrid")}

    port = Reranker()
    with pytest.raises(AppError) as error:
        await HybridRetriever(
            Embeddings(),
            Store(),
            reranker=port,
            rerank_policy=POLICY,
            records=corpus.index_records,
            corpus_digest=corpus.index_digest,
        ).compare("q", SCOPE, budget())
    assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID and not port.requests
