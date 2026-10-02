from typing import Protocol

from deephelp_app.domain.models import (
    ChatRequest,
    ChatResult,
    DenseResult,
    DenseScope,
    EmbeddingResult,
    RequestEnvelope,
    ResponseEnvelope,
    VerifiedIdentity,
)
from deephelp_app.execution import ExecutionBudget


class ModelGateway(Protocol):
    async def complete(self, text: str, budget: ExecutionBudget) -> str: ...


class ChatPort(Protocol):
    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult: ...


class EmbeddingPort(Protocol):
    async def embed(self, texts: list[str], budget: ExecutionBudget) -> EmbeddingResult: ...


class DenseRetrieverPort(Protocol):
    async def retrieve(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> DenseResult: ...


class Repository(Protocol):
    """M01 fake storage seam, not a persistent message/idempotency ledger."""

    async def get(
        self, identity: VerifiedIdentity, channel: str, message_id: str
    ) -> ResponseEnvelope | None: ...

    async def put(self, request: RequestEnvelope, response: ResponseEnvelope) -> None: ...
