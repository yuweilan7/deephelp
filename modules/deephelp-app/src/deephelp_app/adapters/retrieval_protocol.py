"""Frozen retrieval assets and the shared, bounded HTTP MCP contract."""

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from mcp.types import Tool, ToolAnnotations

from deephelp_app.adapters.mcp_protocol import canonical
from deephelp_app.adapters.providers import ProviderConfig
from deephelp_app.application.corpus import read_corpus
from deephelp_app.application.dense import load_json
from deephelp_app.application.hybrid import normalized_fusion, result_for
from deephelp_app.application.retrieval_policy import validate_published_selection
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models import (
    CorpusRecord,
    DenseResult,
    DenseScope,
    ErrorCode,
    HybridScope,
    RetrievalAllowance,
    RetrievalQuery,
    RetrievalWire,
)

VERSION = "m20-retrieval-v1"
HEALTH_URI = "deephelp://m20/retrieval/health"
NAMES = ("retrieve_intents", "compare_intents")


@dataclass(frozen=True)
class RetrievalBinding:
    scope: DenseScope
    top_k: int
    weight: float
    corpus_digest: str
    records: tuple[CorpusRecord, ...]
    config: ProviderConfig

    @property
    def profile_id(self) -> str:
        return hashlib.sha256(
            canonical(
                {
                    "version": VERSION,
                    "scope": self.scope.model_dump(mode="json"),
                    "top_k": self.top_k,
                    "weight": self.weight,
                    "corpus_digest": self.corpus_digest,
                    "embedding": self.config.embedding.model_dump(mode="json"),
                    "provider": self.config.provider,
                    "allow_zero_embedding_indices": self.config.allow_zero_embedding_indices,
                    "cache_entries": self.config.cache_entries,
                    "cache_bytes": self.config.cache_bytes,
                }
            )
        ).hexdigest()

    def allowance(self, text: str, seconds: float) -> RetrievalAllowance:
        # Exact framing used by the existing one-text embedding adapter. No output truncation.
        body = {
            "model": self.config.embedding.model,
            "input": [unicodedata.normalize("NFC", text).strip()],
            "dimensions": self.scope.signature.dimension,
            "encoding_format": "float",
        }
        tokens = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()) + 1024
        cost = self.config.embedding.input_cny_per_million * tokens / Decimal(1000000)
        cost = max(cost, Decimal("0.000001"))
        return RetrievalAllowance(
            attempts=6 if isinstance(self.scope, HybridScope) else 10,
            tokens=tokens,
            cost=cost,
            seconds=min(60, seconds),
        )

    def health(self) -> dict[str, Any]:
        return {
            "source": VERSION,
            "scope": self.scope.model_dump(mode="json"),
            "profile_id": self.profile_id,
            "corpus_digest": self.corpus_digest,
            "top_k": self.top_k,
            "weight": self.weight,
        }


def load_binding(
    pointer: dict[str, Any], config: ProviderConfig, corpus_path: Path | None
) -> RetrievalBinding:
    active = pointer.get("active")
    scope = (
        HybridScope.model_validate(active)
        if isinstance(active, dict) and active.get("index_kind") == "intent_hybrid"
        else DenseScope.model_validate(active)
    )
    top_k, weight = 3, 0.5
    corpus = read_corpus(corpus_path) if corpus_path else None
    if corpus:
        corpus.require_valid()
        if pointer.get("corpus_digest") != corpus.index_digest:
            raise ConfigurationError("Published corpus digest mismatch")
    if isinstance(scope, HybridScope):
        if corpus is None:
            raise ConfigurationError("Hybrid runtime requires the explicit published corpus path")
        selection = pointer.get("selection")
        if not isinstance(selection, dict) or pointer.get("format") != "m09-pointer-v1":
            raise ConfigurationError("Hybrid pointer requires a frozen retrieval policy")
        selected_k = selection.get("candidate_budget")
        if type(selected_k) is not int or not 1 <= selected_k <= 20:
            raise ConfigurationError("Hybrid candidate budget must be an integer 1..20")
        top_k = selected_k
        validate_published_selection(selection, scope, corpus.index_digest, top_k)
        if pointer.get("dense_weight") != selection["dense_weight"]:
            raise ConfigurationError("Hybrid pointer corpus/weight mismatch")
        weight = float(selection["dense_weight"])
    if scope.signature != config.signature():
        raise ConfigurationError("Active collection embedding signature differs")
    return RetrievalBinding(
        scope,
        top_k,
        weight,
        str(pointer.get("corpus_digest", "")),
        corpus.index_records if corpus else (),
        config,
    )


def binding_from_files(pointer: Path, config: ProviderConfig, corpus: Path) -> RetrievalBinding:
    return load_binding(load_json(pointer), config, corpus)


def registered_tools() -> list[Tool]:
    return [
        Tool(
            name=name,
            description=(
                "检索发布语料的意图候选。"
                if name == NAMES[0]
                else "同一查询对照Dense、BM25与Hybrid；分数不是概率。"
            ),
            input_schema=RetrievalQuery.model_json_schema(),
            output_schema=RetrievalWire.model_json_schema(),
            annotations=ToolAnnotations(
                read_only_hint=True,
                destructive_hint=False,
                idempotent_hint=True,
                open_world_hint=False,
            ),
            _meta={"deephelp/tool_version": VERSION},
        )
        for name in NAMES
    ]


def arguments(name: str, value: object) -> RetrievalQuery:
    if (
        name not in NAMES
        or not isinstance(value, dict)
        or list(Draft202012Validator(RetrievalQuery.model_json_schema()).iter_errors(value))
    ):
        raise AppError(ErrorCode.INVALID_ARGUMENT, "Invalid retrieval tool arguments")
    return RetrievalQuery.model_validate(value)


def validate_results(
    binding: RetrievalBinding, query: RetrievalQuery, name: str, results: dict[str, DenseResult]
) -> None:
    """Check public candidates against owned frozen records, independently of the SDK schema."""
    hybrid = isinstance(binding.scope, HybridScope)
    modes = {"dense", "bm25", "hybrid"} if name == NAMES[1] else {"hybrid" if hybrid else "dense"}
    if set(results) != modes or (name == NAMES[1] and not hybrid):
        raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Retrieval result modes differ")
    records = {r.doc_id: r for r in binding.records}
    try:
        for mode, result in results.items():
            kind = {"dense": "cosine", "bm25": "bm25", "hybrid": "fusion"}[mode]
            if (
                result.scope != binding.scope
                or result.retrieval_mode != mode
                or len(result.hits) > query.top_k
                or len({h.doc_id for h in result.hits}) != len(result.hits)
                or result.policy_version
                != ("hybrid-weighted-v1" if hybrid else "dense-baseline-v1")
            ):
                raise ValueError
            for hit in result.hits:
                record = records[hit.doc_id]
                if (
                    hit.scope != binding.scope
                    or hit.score_kind != kind
                    or hit.intent_code != record.intent_code
                    or hit.content != record.content
                ):
                    raise ValueError
                if mode == "hybrid":
                    sources = tuple(
                        m
                        for m, v in (("dense", hit.raw_dense), ("bm25", hit.raw_sparse))
                        if v is not None
                    )
                    if (
                        hit.matched_sources != sources
                        or not sources
                        or not math.isclose(
                            hit.raw_score,
                            normalized_fusion(hit.raw_dense, hit.raw_sparse, binding.weight),
                            abs_tol=2e-5,
                        )
                    ):
                        raise ValueError
            candidates = result.candidates
            if (
                [c.rank for c in candidates] != list(range(1, len(candidates) + 1))
                or len({c.intent_code for c in candidates}) != len(candidates)
                or any(
                    c.score_kind != kind
                    or not c.intent_code.actionable
                    or records[c.evidence_doc_id].intent_code != c.intent_code
                    for c in candidates
                )
                or any(c.raw_score < 0 for c in candidates if kind != "cosine")
                or any(not -1.001 <= c.raw_score <= 1.001 for c in candidates if kind == "cosine")
            ):
                raise ValueError
            if hybrid:
                assert isinstance(binding.scope, HybridScope)
                if (
                    result.candidate_budget != query.top_k
                    or result.dense_weight != binding.weight
                    or [h.rank for h in result.hits] != list(range(1, len(result.hits) + 1))
                    or result_for(
                        binding.scope, mode, list(result.hits), query.top_k, binding.weight, 0
                    ).candidates
                    != candidates
                ):
                    raise ValueError
            elif list(candidates) != sorted(
                candidates, key=lambda c: (-c.raw_score, c.intent_code.value, c.evidence_doc_id)
            ):
                raise ValueError
    except KeyError, TypeError, ValueError:
        raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "Unverified retrieval candidates") from None
