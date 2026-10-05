"""Retrieval MCP的查询、可信上下文、预算委托和回执。

公开查询与签名委托分开；身份/版本来自入口，远程usage必须在预留范围内才可结算。
普通schema只验证形状，客户端和服务端仍须校验签名、归属与原候选语义。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    Field,
)

from .common import (
    DTO,
    BudgetUsed,
    Count,
    DecimalValue,
    ErrorDetail,
    Identifier,
    PositiveCount,
    VerifiedIdentity,
)
from .conversation import RequestEnvelope
from .retrieval import DenseResult


class RetrievalQuery(DTO):
    query: Annotated[str, Field(strict=True, min_length=1, max_length=2000, pattern=r"\S")]
    top_k: Annotated[int, Field(strict=True, ge=1, le=20)] = 3
    scope_id: Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]


class RetrievalContext(DTO):
    identity: VerifiedIdentity
    session_id: Identifier
    message_id: Identifier
    request_id: Identifier
    trace_id: Identifier

    @classmethod
    def from_request(cls, request: RequestEnvelope) -> RetrievalContext:
        return cls.model_validate({key: getattr(request, key) for key in cls.model_fields})


class RetrievalAllowance(DTO):
    attempts: Annotated[int, Field(strict=True, ge=1, le=16)]
    tokens: PositiveCount
    cost: DecimalValue
    seconds: Annotated[float, Field(gt=0, le=60, allow_inf_nan=False)]


class RetrievalDelegation(DTO):
    version: Literal["m20-retrieval-v1", "m31-retrieval-v1"] = "m20-retrieval-v1"
    context: RetrievalContext
    name: Literal["retrieve_intents", "compare_intents"]
    query_hash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    profile_id: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    allowance: RetrievalAllowance


class RetrievalAction(DTO):
    action: Literal["mcp", "embedding", "rerank", "milvus_validate", "dense", "bm25", "fusion"]
    elapsed_ms: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    attempts: Count = 0
    model_calls: Count = 0
    tokens: Count = 0
    charged_tokens: Count = 0
    uncertain_attempts: Count = 0
    provider_request_ids: tuple[Identifier, ...] = ()


class RetrievalReply(DTO):
    version: Literal["m20-retrieval-v1", "m31-retrieval-v1"] = "m20-retrieval-v1"
    call_id: Identifier
    delegation: RetrievalDelegation
    query: RetrievalQuery
    service_pid: PositiveCount
    results: dict[Literal["dense", "bm25", "hybrid", "hybrid_rerank"], DenseResult]
    used: BudgetUsed
    actions: tuple[RetrievalAction, ...] = Field(max_length=16)
    error: ErrorDetail | None = None


class RetrievalWire(DTO):
    reply: RetrievalReply
    proof: dict[str, Any]
