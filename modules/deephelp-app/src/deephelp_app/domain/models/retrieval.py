"""语料、索引签名、召回候选及重排证据的值。

不同score_kind有独立取值范围，融合原分数与重排顺序并存。
RerankPolicy要求排完整召回候选，最终K在用例中按意图裁剪；scope防止混向量空间。
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    Field,
    StrictBool,
    model_validator,
)

from .common import DTO, REGISTRY_VERSION, Count, ErrorCode, Identifier, PositiveCount, RawText
from .intents import IntentCode
from .model_io import EmbeddingSignature, RerankResult


class ScoreKind(StrEnum):
    COSINE = "cosine"
    BM25 = "bm25"
    FUSION = "fusion"
    CLASSIFIER_PROBABILITY = "classifier_probability"
    RULE = "rule"
    MODEL_CHOICE = "model_choice"


class IntentCandidate(DTO):
    code: IntentCode
    rank: PositiveCount
    score: float = Field(allow_inf_nan=False)
    score_kind: ScoreKind

    @model_validator(mode="after")
    def valid_candidate(self) -> IntentCandidate:
        if not self.code.actionable:
            raise ValueError("Candidates must be actionable codes")
        if self.score_kind == ScoreKind.COSINE and not -1 <= self.score <= 1:
            raise ValueError("Cosine score must be in [-1, 1]")
        if self.score_kind in {ScoreKind.CLASSIFIER_PROBABILITY, ScoreKind.RULE}:
            if not 0 <= self.score <= 1:
                raise ValueError("Probability/rule score must be in [0, 1]")
        elif self.score_kind != ScoreKind.COSINE and self.score < 0:
            raise ValueError("BM25/fusion scores must be nonnegative")
        return self


class CorpusRecord(DTO):
    doc_id: Identifier
    content: Annotated[str, Field(strict=True, min_length=1, max_length=2000, pattern=r"\S")]
    intent_code: IntentCode
    split: Literal["reference", "train", "dev", "test", "regression"]
    source_group: Identifier
    variant_group: Identifier
    synthetic: Literal[True]
    label_path: tuple[Identifier, ...] | None = None
    metadata: dict[str, str] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def registered_label(self) -> CorpusRecord:
        from deephelp_app.domain.registry import complaint_registry

        definition = complaint_registry().get(self.intent_code)
        levels = tuple(x for x in (definition.l1, definition.l2, definition.l3, definition.l4) if x)
        if len(self.doc_id.encode("utf-8")) > 128:
            raise ValueError("doc_id exceeds Milvus byte limit")
        if not definition.is_actionable or (
            self.label_path is not None and self.label_path != levels
        ):
            raise ValueError("Unknown/non-actionable code or conflicting label hierarchy")
        if any(len(k) > 128 or len(v) > 512 for k, v in self.metadata.items()):
            raise ValueError("Metadata exceeds bounded string sizes")
        return self


class DenseScope(DTO):
    namespace: Annotated[str, Field(strict=True, pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    dataset_version: Identifier
    signature: EmbeddingSignature
    registry_version: Literal["complaints-v1"] = REGISTRY_VERSION

    @model_validator(mode="after")
    def milvus_byte_limits(self) -> DenseScope:
        if len(self.dataset_version.encode("utf-8")) > 128:
            raise ValueError("dataset_version exceeds Milvus byte limit")
        return self

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()

    @property
    def collection_name(self) -> str:
        return "m05_intent_" + self.fingerprint[:32]


class HybridScope(DenseScope):
    """Immutable intent corpus; future event/history indexes need a separate scope/Port."""

    analyzer_version: Literal["jieba-search-cn-lower-v1"] = "jieba-search-cn-lower-v1"
    index_kind: Literal["intent_hybrid"]

    @property
    def collection_name(self) -> str:
        return "m09_intent_" + self.fingerprint[:32]


class RerankPolicy(DTO):
    version: Literal["m31-rerank-v1"] = "m31-rerank-v1"
    model: Literal["qwen3.7-text-rerank"] = "qwen3.7-text-rerank"
    enabled: StrictBool = True
    candidate_budget: Annotated[int, Field(strict=True, ge=3, le=20)]
    top_n: Annotated[int, Field(strict=True, ge=3, le=20)]
    instruct: RawText | None
    failure_policy: Literal["fail_closed", "hybrid_on_unavailable"] = "fail_closed"

    @model_validator(mode="after")
    def complete_ranking(self) -> RerankPolicy:
        if self.top_n != self.candidate_budget:
            raise ValueError("Rank all recalled documents before per-intent aggregation")
        return self

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()


class RetrievalRerank(DTO):
    policy: RerankPolicy
    status: Literal["passed", "not_run", "fallback"]
    reason: Identifier | None = None
    input_doc_ids: tuple[Identifier, ...]
    result: RerankResult | None = None
    elapsed_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0
    attempts: Count = 0
    error_code: ErrorCode | None = None

    @model_validator(mode="after")
    def bound_result(self) -> RetrievalRerank:
        if len(set(self.input_doc_ids)) != len(self.input_doc_ids):
            raise ValueError("Duplicate rerank input IDs")
        if (self.status == "passed") != (self.result is not None):
            raise ValueError("Only passed rerank carries a model result")
        if len(self.input_doc_ids) > self.policy.candidate_budget:
            raise ValueError("Rerank exceeds published candidate budget")
        if self.status == "passed" and (
            not self.policy.enabled or not self.input_doc_ids or self.reason or self.error_code
        ):
            raise ValueError("Passed rerank requires enabled nonempty input without an error")
        if self.status == "not_run" and (
            self.attempts != 0
            or self.error_code is not None
            or self.reason != ("empty_recall" if not self.input_doc_ids else "disabled")
            or (self.input_doc_ids and self.policy.enabled)
        ):
            raise ValueError("Rerank skip reason must match the input and disabled policy")
        if self.status == "fallback" and (
            not self.policy.enabled
            or self.policy.failure_policy != "hybrid_on_unavailable"
            or self.reason != "upstream_unavailable"
            or self.error_code
            not in {ErrorCode.TIMEOUT, ErrorCode.UPSTREAM_UNAVAILABLE, ErrorCode.RATE_LIMITED}
        ):
            raise ValueError("Rerank fallback is limited to published availability failures")
        if self.result is not None and (
            self.result.model != self.policy.model
            or self.result.instruct != self.policy.instruct
            or len(self.result.hits) != len(self.input_doc_ids)
            or any(
                hit.index >= len(self.input_doc_ids)
                or hit.document.doc_id != self.input_doc_ids[hit.index]
                for hit in self.result.hits
            )
        ):
            raise ValueError("Rerank result/profile/input mismatch")
        return self


class DenseHit(DTO):
    doc_id: Identifier
    intent_code: IntentCode
    content: str
    raw_score: Annotated[float, Field(allow_inf_nan=False)]
    score_kind: Literal["cosine", "bm25", "fusion"] = "cosine"
    metadata: dict[str, object]
    scope: HybridScope | DenseScope
    raw_dense: Annotated[float, Field(allow_inf_nan=False)] | None = None
    raw_sparse: Annotated[float, Field(allow_inf_nan=False, ge=0)] | None = None
    fusion_score: Annotated[float, Field(allow_inf_nan=False, ge=0, le=1.001)] | None = None
    rank: PositiveCount | None = None
    matched_sources: tuple[Literal["dense", "bm25"], ...] = ()

    @model_validator(mode="after")
    def score_range(self) -> DenseHit:
        if self.score_kind == "cosine" and not -1.001 <= self.raw_score <= 1.001:
            raise ValueError("Cosine score outside range")
        if self.score_kind == "bm25" and self.raw_score < 0:
            raise ValueError("BM25 score must be nonnegative")
        if self.score_kind == "fusion" and (
            self.fusion_score != self.raw_score or not self.matched_sources
        ):
            raise ValueError("Fusion requires score and actual route provenance")
        return self


class DenseCandidate(DTO):
    intent_code: IntentCode
    raw_score: Annotated[float, Field(allow_inf_nan=False)]
    score_kind: Literal["cosine", "bm25", "fusion"] = "cosine"
    rank: PositiveCount
    evidence_doc_id: Identifier
    aggregation: Literal["per_intent_max_v1"] = "per_intent_max_v1"


class DenseResult(DTO):
    scope: HybridScope | DenseScope
    hits: tuple[DenseHit, ...]
    candidates: tuple[DenseCandidate, ...]
    policy_version: Literal["dense-baseline-v1", "hybrid-weighted-v1", "m31-rerank-v1"] = (
        "dense-baseline-v1"
    )
    retrieval_mode: Literal["dense", "bm25", "hybrid", "hybrid_rerank"] = "dense"
    candidate_budget: PositiveCount | None = None
    dense_weight: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None
    elapsed_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    rerank: RetrievalRerank | None = None

    @model_validator(mode="after")
    def rerank_evidence(self) -> DenseResult:
        if self.retrieval_mode == "hybrid_rerank":
            if self.rerank is None or self.policy_version != "m31-rerank-v1":
                raise ValueError("Rerank mode needs its policy and execution status")
            if self.rerank.result is not None:
                ranked = self.rerank.result.hits
                if [hit.doc_id for hit in self.hits] != [
                    hit.document.doc_id for hit in ranked
                ] or any(
                    hit.content != rank.document.content
                    or hit.intent_code != rank.document.intent_code
                    for hit, rank in zip(self.hits, ranked, strict=True)
                ):
                    raise ValueError("Rerank output must retain local evidence mapping")
        elif self.rerank is not None:
            raise ValueError("Legacy modes cannot carry rerank evidence")
        return self
