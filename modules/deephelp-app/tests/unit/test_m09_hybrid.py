import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from deephelp_app.corpus import digest
from deephelp_app.dense import atomic_json, load_json
from deephelp_app.domain.models import (
    ChatResult,
    DenseHit,
    DenseResult,
    DenseScope,
    EmbeddingResult,
    EmbeddingSignature,
    ErrorCode,
    HybridScope,
    IntentCode,
    ModelUsage,
)
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.evaluation.hybrid_cli import validate_selection
from deephelp_app.evaluation.hybrid_eval import (
    choose_weight,
    classify,
    dataset,
    query_digest,
    query_text,
    ranking,
)
from deephelp_app.execution import ExecutionBudget
from deephelp_app.hybrid import ANALYZER, HybridRetriever, normalized_fusion, result_for
from deephelp_app.milvus_dense import FIELDS
from deephelp_app.milvus_hybrid import MilvusHybridStore, publish_hybrid
from deephelp_app.providers import ProviderConfig

pytestmark = pytest.mark.unit
SIG = EmbeddingSignature(provider="synthetic", model="offline-only", dimension=3)
SCOPE = HybridScope(
    namespace="synthetic", dataset_version="unit-v1", signature=SIG, index_kind="intent_hybrid"
)


def budget():
    return ExecutionBudget.start(10, 100, 0)


def hit(doc="a", code=IntentCode.DISCOUNT_MISSING, score=0.8, **kw):
    return DenseHit(
        doc_id=doc,
        intent_code=code,
        content="synthetic",
        raw_score=score,
        metadata={},
        scope=SCOPE,
        **kw,
    )


class Embeddings:
    def __init__(self, signature=SIG):
        self.signature = signature
        self.calls = []

    async def embed(self, texts, run_budget):
        self.calls.append((texts, run_budget))
        run_budget.claim_attempt(retry=False)
        return EmbeddingResult(
            signature=self.signature, vectors=[[1.0, 0.0, 0.0]], usage=ModelUsage()
        )


class Routes:
    def __init__(self):
        self.calls = []

    async def routes(self, scope, text, vector, limit, weight):
        self.calls.append((scope, text, vector, limit, weight))
        return {
            "dense": [hit()],
            "bm25": [hit(score=9, score_kind="bm25")],
            "hybrid": [
                hit(
                    score=0.9,
                    score_kind="fusion",
                    fusion_score=0.9,
                    raw_dense=0.8,
                    raw_sparse=9,
                    matched_sources=("dense", "bm25"),
                )
            ],
        }


def test_legacy_scope_and_roundtrip_do_not_silently_become_hybrid():
    config = ProviderConfig.load(Path("modules/deephelp-app/providers.example.json"))
    legacy = DenseScope(
        namespace="m05_synthetic", dataset_version="m05-smoke-v1", signature=config.signature()
    )
    assert legacy.collection_name.startswith("m05_intent_")
    assert "analyzer_version" not in legacy.model_dump()
    result = DenseResult(scope=legacy, hits=(), candidates=())
    restored = DenseResult.model_validate_json(result.model_dump_json())
    assert type(restored.scope) is DenseScope
    assert restored.scope.fingerprint == legacy.fingerprint
    hybrid = DenseResult(scope=SCOPE, hits=(), candidates=())
    assert isinstance(DenseResult.model_validate_json(hybrid.model_dump_json()).scope, HybridScope)
    assert SCOPE.collection_name.startswith("m09_intent_")
    assert (
        SCOPE.fingerprint
        != DenseScope(
            namespace=SCOPE.namespace, dataset_version=SCOPE.dataset_version, signature=SIG
        ).fingerprint
    )


@pytest.mark.parametrize(
    "kw",
    [
        {"raw_score": 2},
        {"raw_score": -1, "score_kind": "bm25"},
        {"raw_score": 0.8, "score_kind": "fusion"},
        {"raw_score": float("nan")},
        {"raw_score": 0.8, "raw_sparse": -1},
    ],
)
def test_score_kinds_keep_separate_invariants(kw):
    with pytest.raises(ValidationError):
        DenseHit(
            doc_id="a",
            intent_code=IntentCode.DISCOUNT_MISSING,
            content="synthetic",
            metadata={},
            scope=SCOPE,
            **kw,
        )


def test_native_normalization_matches_live_probe_and_missing_route():
    assert normalized_fusion(1, 1.3365864753723145, 0.5) == pytest.approx(0.7955394387, abs=1e-7)
    assert normalized_fusion(0, None, 0.5) == 0.25
    assert normalized_fusion(None, 0, 0.5) == 0
    assert normalized_fusion(-1, 0, 0.5) == 0
    assert normalized_fusion(1, 10**9, 0.5) < 1


def test_class_population_never_sums_scores():
    hits = [
        hit("a", score=0.8),
        hit("b", score=0.8),
        hit("c", code=IntentCode.COUPON_UNUSABLE, score=0.9),
    ]
    result = result_for(SCOPE, "dense", hits, 3, 0.5, 0)
    assert len(result.candidates) == 2
    assert result.candidates[0].intent_code == IntentCode.COUPON_UNUSABLE
    assert result.candidates[1].raw_score == 0.8


async def test_three_routes_share_one_embedding_same_text_and_budget():
    embeddings, store = Embeddings(), Routes()
    run_budget = budget()
    result = await HybridRetriever(embeddings, store).compare(
        "订单00123456未到账", SCOPE, run_budget, top_k=3
    )
    assert len(embeddings.calls) == 1
    assert embeddings.calls[0][1] is run_budget
    assert store.calls[0][1] == "订单00123456未到账"
    assert set(result) == {"dense", "bm25", "hybrid"}
    assert {r.candidate_budget for r in result.values()} == {3}
    assert result["hybrid"].hits[0].raw_sparse == 9
    assert (
        await HybridRetriever(embeddings, store).retrieve("同一查询", SCOPE, run_budget)
    ).retrieval_mode == "hybrid"


@pytest.mark.parametrize("text,k", [("", 3), (" ", 3), ("a" * 2001, 3), ("a", 0), ("a", 21)])
async def test_bad_query_denied_before_embedding(text, k):
    embeddings = Embeddings()
    with pytest.raises(ValueError):
        await HybridRetriever(embeddings, Routes()).compare(text, SCOPE, budget(), top_k=k)
    assert not embeddings.calls


@pytest.mark.parametrize("weight", [True, float("nan"), float("inf"), -1, 1.1])
def test_bad_weight(weight):
    with pytest.raises(ValueError):
        HybridRetriever(Embeddings(), Routes(), dense_weight=weight)


async def test_same_dimension_other_signature_denied():
    changed = SIG.model_copy(update={"model": "other"})
    with pytest.raises(AppError) as raised:
        await HybridRetriever(Embeddings(changed), Routes()).compare("query", SCOPE, budget())
    assert raised.value.code == ErrorCode.VERSION_CONFLICT


async def test_dense_or_event_scope_cannot_enter_hybrid():
    embeddings = Embeddings()
    scope = DenseScope(namespace="event_history", dataset_version="unit", signature=SIG)
    with pytest.raises(AppError):
        await HybridRetriever(embeddings, Routes()).compare("query", scope, budget())
    assert not embeddings.calls
    with pytest.raises(ValidationError):
        HybridScope(
            namespace="synthetic", dataset_version="unit", signature=SIG, index_kind="event"
        )


async def test_external_cancel_propagates_and_stops_routes():
    entered = asyncio.Event()

    class Blocked(Routes):
        async def routes(self, *args):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        HybridRetriever(Embeddings(), Blocked()).compare("query", SCOPE, budget())
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_shared_deadline_bounds_store():
    class Blocked(Routes):
        async def routes(self, *args):
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await HybridRetriever(Embeddings(), Blocked()).compare(
            "query", SCOPE, ExecutionBudget.start(0.02, 10, 0)
        )


def selection_for(scope=SCOPE):
    corpus, queries = dataset()
    value = {
        "format": "m09-dev-selection-v1",
        "scope": scope.model_dump(mode="json"),
        "index_digest": corpus.index_digest,
        "dev_digest": query_digest(queries["dev"]),
        "test_digest": query_digest(queries["test"]),
        "candidate_budget": 3,
        "dense_weight": 0.5,
        "analyzer": ANALYZER,
    }
    value["selection_digest"] = digest(value)
    return value


def test_frozen_selection_binding_and_dev_tie_rule():
    corpus, queries = dataset()
    value = selection_for()
    validate_selection(value, SCOPE, corpus.index_digest, queries, 3)
    for key, bad in [
        ("dense_weight", 0.75),
        ("candidate_budget", 5),
        ("test_digest", "changed"),
        ("index_digest", "changed"),
    ]:
        changed = copy.deepcopy(value)
        changed[key] = bad
        with pytest.raises(ConfigurationError):
            validate_selection(changed, SCOPE, corpus.index_digest, queries, 3)
    assert (
        choose_weight(
            {
                0.25: {"candidate_recall_1": 1, "mrr": 1},
                0.5: {"candidate_recall_1": 1, "mrr": 1},
                0.75: {"candidate_recall_1": 0.9, "mrr": 1},
            }
        )
        == 0.5
    )


def test_frozen_splits_and_unknown_denominator():
    corpus, queries = dataset()
    assert len(corpus.index_records) == 12
    assert len(queries["dev"]) == 18 and len(queries["test"]) == 24
    assert len([q for q in queries["test"] if q.gold is not None]) == 18
    unknown = next(q for q in queries["test"] if q.gold is None)
    assert ranking(unknown, DenseResult(scope=SCOPE, hits=(), candidates=()))["eligible"] is False


async def test_comparison_classification_uses_real_recorded_route_and_same_cleaning():
    q = dataset()[1]["test"][0]
    assert query_text(q) != q.text  # M04 fullwidth ASCII punctuation normalization only.
    result = result_for(SCOPE, "dense", [hit()], 3, 0.5, 0)
    model = AsyncMock()
    model.chat.return_value = ChatResult(
        structured={"code": "DISCOUNT_MISSING", "reason": "supported"},
        usage=ModelUsage(),
        finish_reason="stop",
        model="synthetic",
    )
    selected = await classify(q, result, model, budget())
    assert selected["classification_correct"] is True
    assert model.chat.await_count == 1
    payload = json.loads(model.chat.call_args.args[0].messages[1].content)
    assert payload["text"] == query_text(q)
    assert payload["candidates"][0]["score_kind"] == "cosine"


async def test_publication_failure_keeps_previous_pointer(tmp_path):
    pointer, manifest = tmp_path / "active.json", tmp_path / "manifest.json"
    atomic_json(pointer, {"keep": "prior"})
    atomic_json(manifest, {"status": "IN_PROGRESS", "scope": SCOPE.model_dump(mode="json")})
    store = AsyncMock()
    with pytest.raises(ConfigurationError):
        await publish_hybrid(store, SCOPE, manifest, pointer, selection_for())
    assert load_json(pointer) == {"keep": "prior"}
    assert store.validate.await_count == 0


async def test_native_calls_use_bm25_text_and_same_filtered_candidate_budget():
    client = AsyncMock()
    entity = {
        "doc_id": "a",
        "content": "synthetic",
        "intent_code": "DISCOUNT_MISSING",
        "metadata": {},
        "namespace": SCOPE.namespace,
        "dataset_version": SCOPE.dataset_version,
        "model_signature": SIG.fingerprint,
        "split": "reference",
    }
    client.search.side_effect = [
        [[{"id": "a", "distance": 0.8, "entity": entity}]],
        [[{"id": "a", "distance": 2.0, "entity": entity}]],
    ]
    score = normalized_fusion(0.8, 2.0, 0.5)
    client.hybrid_search.return_value = [[{"id": "a", "distance": score, "entity": entity}]]
    store = MilvusHybridStore(client, budget())
    store.validate = AsyncMock(return_value="digest")
    result = await store.routes(SCOPE, "not 未到账 00123456", [1.0, 0.0, 0.0], 3, 0.5)
    dense, sparse = client.search.call_args_list
    assert dense.kwargs["anns_field"] == "vector"
    assert sparse.kwargs["anns_field"] == "sparse"
    assert sparse.kwargs["data"] == ["not 未到账 00123456"]
    assert dense.kwargs["filter"] == sparse.kwargs["filter"]
    assert dense.kwargs["limit"] == sparse.kwargs["limit"] == 3
    assert all(
        call.kwargs["retry_times"] == 0 and call.kwargs["timeout"] is None
        for call in client.search.call_args_list
    )
    assert client.hybrid_search.call_args.kwargs["ranker"].dict()["params"] == {
        "weights": [0.5, 0.5],
        "norm_score": True,
    }
    assert result["hybrid"][0].raw_dense == 0.8 and result["hybrid"][0].raw_sparse == 2.0
    client.hybrid_search.return_value[0][0]["distance"] = 0.99
    client.search.side_effect = [
        [[{"id": "a", "distance": 0.8, "entity": entity}]],
        [[{"id": "a", "distance": 2.0, "entity": entity}]],
    ]
    with pytest.raises(AppError) as raised:
        await store.routes(SCOPE, "query", [1.0, 0.0, 0.0], 3, 0.5)
    assert raised.value.code == ErrorCode.MODEL_OUTPUT_INVALID


@pytest.mark.parametrize("tamper", ["analyzer", "function", "dimension", "scope", "dynamic"])
async def test_signed_schema_refuses_same_dimension_wrong_analyzer_or_function(tamper):
    from pymilvus import DataType, FunctionType

    client = AsyncMock()
    store = MilvusHybridStore(client, budget())
    fields = [
        {
            "name": name,
            "type": kind,
            "params": {"max_length": length},
            "is_primary": name == "doc_id",
        }
        for name, (kind, length) in FIELDS.items()
    ]
    fields[1]["params"].update(enable_analyzer=True, analyzer_params=json.dumps(ANALYZER))
    fields.extend(
        [
            {"name": "metadata", "type": DataType.JSON},
            {"name": "vector", "type": DataType.FLOAT_VECTOR, "params": {"dim": 3}},
            {"name": "sparse", "type": DataType.SPARSE_FLOAT_VECTOR},
        ]
    )
    details = {
        "description": store._description(SCOPE, "digest"),
        "fields": fields,
        "auto_id": False,
        "enable_dynamic_field": False,
        "functions": [
            {
                "type": FunctionType.BM25,
                "input_field_names": ["content"],
                "output_field_names": ["sparse"],
            }
        ],
    }
    client.describe_collection.return_value = details
    assert await store.validate(SCOPE, "digest") == "digest"
    if tamper == "analyzer":
        fields[1]["params"]["analyzer_params"] = json.dumps({"tokenizer": "standard"})
    elif tamper == "function":
        details["functions"][0]["output_field_names"] = ["vector"]
    elif tamper == "dimension":
        fields[-2]["params"]["dim"] = 4
    elif tamper == "scope":
        payload = json.loads(details["description"])
        payload["scope"]["signature"]["model"] = "same-dimension-other-model"
        details["description"] = json.dumps(payload)
    else:
        details["enable_dynamic_field"] = True
    with pytest.raises(AppError) as raised:
        await store.validate(SCOPE, "digest")
    assert raised.value.code == ErrorCode.VERSION_CONFLICT


async def test_query_route_scope_mismatch_cannot_publish_candidates():
    class WrongRoutes(Routes):
        async def routes(self, *args):
            rows = await super().routes(*args)
            rows["dense"] = [
                hit().model_copy(update={"scope": SCOPE.model_copy(update={"namespace": "other"})})
            ]
            return rows

    with pytest.raises(AppError) as raised:
        await HybridRetriever(Embeddings(), WrongRoutes()).compare("query", SCOPE, budget())
    assert raised.value.code == ErrorCode.MODEL_OUTPUT_INVALID
