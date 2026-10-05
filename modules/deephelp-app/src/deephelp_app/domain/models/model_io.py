"""Chat/Embedding/Rerank请求与结果的值，客户端在适配器而非DTO中。

原生tool_calls通过专门字段表达，正文JSON不会自动成为工具调用。
EmbeddingSignature绑定模型/维度/归一化空间；RerankDocument本地保留ID与来源，供应商只收content。
"""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    Field,
    StrictBool,
    model_validator,
)

from .common import DTO, Count, Identifier, PositiveCount, RawText
from .intents import IntentCode


class ModelUsage(DTO):
    input_tokens: Count = 0
    output_tokens: Count = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class RerankDocument(DTO):
    """Local immutable evidence. Only content is sent to the provider."""

    doc_id: Identifier
    content: RawText
    intent_code: IntentCode
    source: Identifier


class RerankRequest(DTO):
    query: RawText
    documents: tuple[RerankDocument, ...] = Field(min_length=1, max_length=500)
    top_n: PositiveCount
    instruct: RawText | None = None

    @model_validator(mode="after")
    def bounded_selection(self) -> RerankRequest:
        if self.top_n > len(self.documents):
            raise ValueError("Rerank top_n must not exceed document count")
        if len({doc.doc_id for doc in self.documents}) != len(self.documents):
            raise ValueError("Rerank document IDs must be unique")
        return self


class RerankHit(DTO):
    index: Count
    document: RerankDocument
    score: Annotated[float, Field(strict=True, allow_inf_nan=False)]
    score_kind: Literal["rerank"] = "rerank"


class RerankResult(DTO):
    hits: tuple[RerankHit, ...] = Field(min_length=1, max_length=500)
    model: Identifier
    instruct: RawText | None
    usage: ModelUsage | None
    provider_request_id: Identifier | None = None

    @model_validator(mode="after")
    def ordered_unique_hits(self) -> RerankResult:
        if len({hit.index for hit in self.hits}) != len(self.hits):
            raise ValueError("Rerank indices must be unique")
        if len({hit.document.doc_id for hit in self.hits}) != len(self.hits):
            raise ValueError("Rerank document IDs must be unique")
        if list(self.hits) != sorted(self.hits, key=lambda hit: (-hit.score, hit.index)):
            raise ValueError("Rerank hits require descending scores and stable input ties")
        return self


class ModelToolCall(DTO):
    call_id: Identifier
    name: Identifier
    arguments: dict[str, object]


class ChatMessage(DTO):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    tool_call_id: Identifier | None = None

    @model_validator(mode="after")
    def valid_message(self) -> ChatMessage:
        if self.role == "tool" and (not self.tool_call_id or self.content is None):
            raise ValueError("Tool result requires call ID and content")
        if self.role != "tool" and self.tool_call_id is not None:
            raise ValueError("Only tool results have a tool call ID")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("Only assistant messages contain tool calls")
        if self.content is None and not self.tool_calls:
            raise ValueError("Message requires content or structured calls")
        return self


class ModelTool(DTO):
    name: Identifier
    description: str = ""
    parameters: dict[str, object]


class ChatRequest(DTO):
    messages: list[ChatMessage] = Field(min_length=1)
    max_output_tokens: PositiveCount = 2048
    enable_thinking: StrictBool | None = None
    thinking_budget: PositiveCount | None = None
    response_format: Literal["text", "json_schema", "json_object"] = "text"
    output_schema: dict[str, object] | None = None
    tools: list[ModelTool] = Field(default_factory=list)
    tool_choice: Identifier | None = None
    repair_once: bool = False

    @model_validator(mode="after")
    def valid_request(self) -> ChatRequest:
        if self.enable_thinking is False and self.thinking_budget is not None:
            raise ValueError("Thinking budget requires thinking mode")
        if (self.response_format != "text") != (self.output_schema is not None):
            raise ValueError("Structured output requires an explicit schema")
        names = [tool.name for tool in self.tools]
        if len(set(names)) != len(names) or (self.tool_choice and self.tool_choice not in names):
            raise ValueError("Tool names must be unique and choice must be declared")
        if self.tools and self.output_schema is not None:
            raise ValueError("Tool and output schemas are separate capabilities")
        return self


class ChatResult(DTO):
    content: str | None = None
    structured: dict[str, object] | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list)
    usage: ModelUsage
    finish_reason: Identifier
    provider_request_id: Identifier | None = None
    model: Identifier


class EmbeddingSignature(DTO):
    provider: Identifier
    model: Identifier
    revision: Identifier | None = None
    dimension: PositiveCount
    normalization: Literal["l2", "none"] = "l2"
    text_normalization: Literal["nfc-strip-v1"] = "nfc-strip-v1"

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return "emb-sha256:" + hashlib.sha256(payload.encode()).hexdigest()


class EmbeddingResult(DTO):
    vectors: list[list[float]]
    signature: EmbeddingSignature
    usage: ModelUsage
    cache_hits: Count = 0
    provider_request_ids: list[Identifier] = Field(default_factory=list)
    position_index_fallback: bool = False

    @model_validator(mode="after")
    def valid_vectors(self) -> EmbeddingResult:
        import math

        if not self.vectors or any(
            len(row) != self.signature.dimension or not all(math.isfinite(v) for v in row)
            for row in self.vectors
        ):
            raise ValueError("Embedding vectors require finite values and the signed dimension")
        return self
