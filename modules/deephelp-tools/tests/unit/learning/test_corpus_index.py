"""原Hybrid核心证明同向量与可信引用，固定响应不替代真实Embedding/Milvus验收。"""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from deephelp_app.adapters.mcp.retrieval.retrieval_protocol import RetrievalBinding
from deephelp_app.adapters.model_gateway.providers import ProviderConfig
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.model_io import EmbeddingResult, ModelUsage
from deephelp_app.domain.models.retrieval import CorpusRecord, DenseHit, HybridScope
from deephelp_tools.evaluation.retrieval.corpus_candidate import compare_bindings, immutable
from deephelp_tools.learning.workbench.evaluation import JobInput


def factory():
    return ExecutionBudget.start(20, 40, 0)


async def test_same_actual_vector_routes_versions_and_foreign_source_rejection(monkeypatch):
    config = ProviderConfig.load(Path("modules/deephelp-app/providers.example.json"))
    scope = HybridScope(
        namespace="m44_test",
        dataset_version="v1",
        signature=config.signature(),
        registry_version="complaints-v3",
        index_kind="intent_hybrid",
    )
    record = CorpusRecord(
        doc_id="proof",
        content="订单关联活动查询",
        intent_code="ORDER_ACTIVITY_QUERY",
        source_group="proof",
        variant_group="proof",
        split="reference",
        synthetic=True,
        registry_version="complaints-v3",
        metadata={"source": "人工合成"},
    )
    current = RetrievalBinding(scope, 3, 0.5, "a" * 64, (record,), config)
    candidate = replace(current, scope=scope.model_copy(update={"dataset_version": "v2"}))
    vectors, calls = [], []

    class Gateway:
        async def embed(self, texts, budget):
            calls.append(texts)
            return EmbeddingResult(
                signature=scope.signature,
                vectors=[[1.0] + [0.0] * (scope.signature.dimension - 1)],
                usage=ModelUsage(input_tokens=7, output_tokens=0),
            )

    class Store:
        foreign = False

        def __init__(self, client, budget):
            self.latencies = {m: 1.0 for m in ("dense", "bm25", "hybrid")}

        async def validate(self, target, corpus_digest):
            assert corpus_digest == "a" * 64

        async def routes(self, target, text, vector, limit, weight):
            vectors.append(vector)
            assert text == "订单活动" and limit == 3 and weight == 0.5
            return {
                m: [
                    DenseHit(
                        scope=target,
                        doc_id="proof",
                        content="偷换来源" if self.foreign else record.content,
                        intent_code=record.intent_code,
                        metadata=record.metadata,
                        raw_score=0.7,
                        score_kind=k,
                        **(
                            dict(
                                raw_dense=0.7,
                                raw_sparse=None,
                                fusion_score=0.7,
                                matched_sources=("dense",),
                            )
                            if m == "hybrid"
                            else {}
                        ),
                    )
                ]
                for m, k in (("dense", "cosine"), ("bm25", "bm25"), ("hybrid", "fusion"))
            }

    monkeypatch.setattr(
        "deephelp_tools.evaluation.retrieval.corpus_candidate.MilvusHybridStore", Store
    )
    assembly = SimpleNamespace(retrieval_binding=current, gateway=Gateway())
    result = await compare_bindings(assembly, None, candidate, "订单活动", factory)
    assert calls == [["订单活动"]] and vectors[0] is vectors[1]
    assert len(result["query_vector_sha256"]) == 64
    assert all(
        v["rerank"] == {"status": "not_run", "reason": "not_configured"}
        for v in result["results"].values()
    )
    with pytest.raises(ConfigurationError):
        await compare_bindings(assembly, None, replace(candidate, weight=0.75), "订单活动", factory)
    assert len(calls) == 1  # 不同条件在付费前拒绝。
    Store.foreign = True
    with pytest.raises(AppError):
        await compare_bindings(assembly, None, candidate, "订单活动", factory)


def test_immutable_bytes_and_explicit_job_contract(tmp_path):
    path = tmp_path / "review.json"
    immutable(path, b"reviewed")
    immutable(path, b"reviewed")
    with pytest.raises(ConfigurationError):
        immutable(path, b"modified")
    assert path.read_bytes() == b"reviewed"
    with pytest.raises(ValueError):
        JobInput(action="corpus_compare", corpus_version="a" * 64, query=" ", confirmed=True)
    with pytest.raises(ValueError):
        JobInput(action="corpus_build", corpus_version="a" * 64, confirmed=False)
