"""用Protocol描述用例所需能力，具体连接与生命周期由bootstrap装配。

Protocol类似Java接口，但实现不必显式继承：类型检查按同名方法与签名判断。
await返回候选/观察值仍要经用例守卫，Port并不自带授权或事实真实性保证。
Memory/Case端口读取原问题，Repository仅是M01练习缝隙，不是持久幂等账本。
"""

from collections.abc import Callable
from typing import Protocol

from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models.collaboration import AgentTask, AgentTaskRecord
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_app.domain.models.conversation import LifecycleCommand, Question, RequestEnvelope
from deephelp_app.domain.models.events import ClusterJudgement, EventCandidate, EventMessage
from deephelp_app.domain.models.intent_decisions import FallbackResult
from deephelp_app.domain.models.memory import MemoryWindow
from deephelp_app.domain.models.model_io import (
    ChatRequest,
    ChatResult,
    EmbeddingResult,
    RerankRequest,
    RerankResult,
)
from deephelp_app.domain.models.responses import ResponseEnvelope
from deephelp_app.domain.models.retrieval import DenseResult, DenseScope
from deephelp_app.domain.models.sop import SOPResult
from deephelp_app.domain.models.text import TextEntityResult
from deephelp_app.domain.models.tools import ToolRequest, ToolResult


class ModelGateway(Protocol):
    async def complete(self, text: str, budget: ExecutionBudget) -> str: ...


class ChatPort(Protocol):
    # 结构类型：有匹配chat签名即可；这里不创建连接，也不提供默认模型。
    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult: ...


class EmbeddingPort(Protocol):
    async def embed(self, texts: list[str], budget: ExecutionBudget) -> EmbeddingResult: ...


class RerankPort(Protocol):
    async def rerank(self, request: RerankRequest, budget: ExecutionBudget) -> RerankResult: ...


class ClusterJudgePort(Protocol):
    async def judge(
        self,
        current: EventMessage,
        candidates: tuple[EventCandidate, ...],
        messages: tuple[EventMessage, ...],
        budget: ExecutionBudget,
    ) -> ClusterJudgement: ...


class FallbackPort(Protocol):
    async def decide(
        self,
        text: TextEntityResult,
        retrievals: tuple[DenseResult, ...],
        budget: ExecutionBudget,
        *,
        context: str | None = None,
    ) -> FallbackResult: ...


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


class AgentTaskJournalPort(Protocol):
    async def begin(
        self, task: AgentTask, context: RequestEnvelope, *, max_tasks: int
    ) -> AgentTaskRecord: ...

    async def write(self, record: AgentTaskRecord) -> None: ...

    async def records(
        self, identity: VerifiedIdentity, session: str, run_id: str
    ) -> tuple[AgentTaskRecord, ...]: ...


class Repository(Protocol):
    """M01 fake storage seam, not a persistent message/idempotency ledger."""

    async def get(
        self, identity: VerifiedIdentity, channel: str, message_id: str
    ) -> ResponseEnvelope | None: ...

    async def put(self, request: RequestEnvelope, response: ResponseEnvelope) -> None: ...
