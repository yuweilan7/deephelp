"""M02 recorded labels/slots and deterministic action replay; not model-quality evidence."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from deephelp_app.cascade import CascadePolicy
from deephelp_app.cases_fake import MemoryCaseRepository
from deephelp_app.conversation import Conversation, CountedModel, CountedRetriever, TrackedTools
from deephelp_app.corpus import frozen_preview
from deephelp_app.domain.models import (
    ChatRequest,
    ChatResult,
    DenseHit,
    DenseResult,
    DenseScope,
    EmbeddingResult,
    EmbeddingSignature,
    ModelToolCall,
    ModelUsage,
    VersionManifest,
)
from deephelp_app.event_cluster import EventAggregationService
from deephelp_app.execution import ExecutionBudget
from deephelp_app.intent import IntentService
from deephelp_app.ledger import MemoryLedger
from deephelp_app.mcp_mock import MockConfig
from deephelp_app.memory import MemoryService
from deephelp_app.ports import ClusterJudgePort
from deephelp_app.samples import load_corpus
from deephelp_app.sop import SOPExecutor
from deephelp_app.text_entity import TextEntityProcessor, TextPolicy, clean_text
from deephelp_app.tool_gateway import ToolGateway
from deephelp_app.trace import TraceSink

REPLAY_SCOPE = DenseScope(
    namespace="m08_replay",
    dataset_version="m02-replay-v1",
    signature=EmbeddingSignature(provider="synthetic", model="synthetic", dimension=2),
)


class RecordedModel:
    def __init__(self) -> None:
        self.rows = {c.message.raw_text: c.expected for c in load_corpus().cases}
        self.rows.update(
            {
                clean_text(c.message.raw_text, TextPolicy()).cleaned_text: c.expected
                for c in load_corpus().cases
            }
        )

    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult:
        budget.claim_attempt(retry=False)
        structured: dict[str, object] | None = None
        actions: list[ModelToolCall] = []
        if request.output_schema:
            data = json.loads(request.messages[-1].content or "{}")
            text = data["text"]
            expected = self.rows.get(text)
            properties = request.output_schema["properties"]
            assert isinstance(properties, dict)
            if "entities" in properties:
                structured = (
                    {
                        "entities": [
                            {"name": e.name.value, "value": e.value, "evidence": text}
                            for e in expected.entities
                            if e.name.value in data["fields"]
                        ]
                    }
                    if expected
                    else {"entities": []}
                )
            else:
                structured = {
                    "code": expected.intent.value if expected and expected.intent else None,
                    "reason": "supported" if expected and expected.intent else "unknown",
                }
        else:
            if request.tool_choice == "finish_sop":
                name, args = "finish_sop", {}
            else:
                slots = json.loads(
                    (request.messages[0].content or "").split("VERIFIED SLOTS:\n")[1]
                )
                name, args = request.tools[0].name, slots
            actions = [ModelToolCall(call_id="synthetic-action", name=name, arguments=args)]
        return ChatResult(
            model="synthetic-replay",
            structured=structured,
            tool_calls=actions,
            usage=ModelUsage(),
            finish_reason="tool_calls" if actions else "stop",
        )

    async def embed(self, texts: list[str], budget: ExecutionBudget) -> EmbeddingResult:
        raise AssertionError("Replay Dense does not perform embedding")


class RecordedDense:
    async def retrieve(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> DenseResult:
        records = frozen_preview().index_records[:top_k]
        return DenseResult(
            scope=scope,
            candidates=(),
            hits=tuple(
                DenseHit(
                    doc_id=r.doc_id,
                    content=r.content,
                    intent_code=r.intent_code,
                    raw_score=0.5,
                    metadata={},
                    scope=scope,
                )
                for r in records
            ),
        )


class ReplayAssembly:
    def __init__(
        self,
        *,
        mock: MockConfig | None = None,
        ledger: MemoryLedger | None = None,
        event_judge: ClusterJudgePort | None = None,
        policy: CascadePolicy | None = None,
    ) -> None:
        self.ledger = ledger or MemoryLedger()
        self.tools = ToolGateway(config=mock)
        self.event_judge, self.policy = event_judge, policy
        if event_judge and not isinstance(self.ledger, MemoryCaseRepository):
            raise ValueError("Replay events need MemoryCaseRepository")

    @asynccontextmanager
    async def open(
        self, client: httpx.AsyncClient, trace: TraceSink
    ) -> AsyncIterator[Conversation]:
        model = RecordedModel()
        counted = CountedModel(model, model)
        async with self.tools.open():
            yield Conversation(
                self.ledger,
                TextEntityProcessor(counted, trace=trace),
                IntentService(
                    counted, CountedRetriever(RecordedDense()), REPLAY_SCOPE, policy=self.policy
                ),
                SOPExecutor(counted, TrackedTools(self.tools)),
                trace,
                VersionManifest(
                    registry="complaints-v1",
                    model="synthetic-replay",
                    prompt="mvp-intent-v1",
                    policy="slot-policy-v1",
                ),
                memory=MemoryService(self.ledger)
                if isinstance(self.ledger, MemoryCaseRepository)
                else None,
                cases=self.ledger if isinstance(self.ledger, MemoryCaseRepository) else None,
                events=EventAggregationService(self.ledger, self.event_judge)
                if isinstance(self.ledger, MemoryCaseRepository) and self.event_judge
                else None,
            )
