from collections.abc import Callable
from typing import Protocol

from deephelp_app.domain.models import (
    ChatRequest,
    ChatResult,
    ClusterJudgement,
    DenseResult,
    DenseScope,
    EmbeddingResult,
    EventCandidate,
    EventMessage,
    LifecycleCommand,
    MemoryWindow,
    Question,
    RequestEnvelope,
    ResponseEnvelope,
    SOPResult,
    ToolRequest,
    ToolResult,
    VerifiedIdentity,
)
from deephelp_app.execution import ExecutionBudget


class ModelGateway(Protocol):
    async def complete(self, text: str, budget: ExecutionBudget) -> str: ...


class ChatPort(Protocol):
    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult: ...


class EmbeddingPort(Protocol):
    async def embed(self, texts: list[str], budget: ExecutionBudget) -> EmbeddingResult: ...


class ClusterJudgePort(Protocol):
    async def judge(
        self,
        current: EventMessage,
        candidates: tuple[EventCandidate, ...],
        messages: tuple[EventMessage, ...],
        budget: ExecutionBudget,
    ) -> ClusterJudgement: ...


class CaseRepository(Protocol):
    async def question(
        self, identity: VerifiedIdentity, session: str, question_id: str
    ) -> Question | None: ...
    async def snapshot(
        self,
        identity: VerifiedIdentity,
        session: str,
        *,
        count: int = 30,
        token_limit: int = 8192,
        age_seconds: int = 86400,
        case_limit: int = 32,
    ) -> MemoryWindow: ...
    async def transition(
        self, identity: VerifiedIdentity, qid: str, command: LifecycleCommand
    ) -> Question: ...


class MemoryPort(Protocol):
    async def load(
        self,
        identity: VerifiedIdentity,
        session: str,
        budget: ExecutionBudget,
        *,
        query: str | None = None,
    ) -> MemoryWindow: ...


class DenseRetrieverPort(Protocol):
    async def retrieve(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> DenseResult: ...


class HybridRetrieverPort(DenseRetrieverPort, Protocol):
    async def compare(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> dict[str, DenseResult]: ...


class ToolPort(Protocol):
    async def execute(
        self,
        request: ToolRequest,
        question: Question,
        budget: ExecutionBudget,
        *,
        context: RequestEnvelope,
        on_dispatch: Callable[[str], None] | None = None,
    ) -> ToolResult: ...


class SOPExecutorPort(Protocol):
    async def execute(
        self,
        question: Question,
        budget: ExecutionBudget,
        *,
        context: RequestEnvelope,
        run_id: str,
    ) -> SOPResult: ...


class Repository(Protocol):
    """M01 fake storage seam, not a persistent message/idempotency ledger."""

    async def get(
        self, identity: VerifiedIdentity, channel: str, message_id: str
    ) -> ResponseEnvelope | None: ...

    async def put(self, request: RequestEnvelope, response: ResponseEnvelope) -> None: ...
