"""Evaluation-only assemblies. Offline semantic replay never represents model quality."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

from deephelp_app.cascade import CascadePolicy, StructuredFallback
from deephelp_app.cases_fake import MemoryCaseRepository
from deephelp_app.conversation import Conversation, CountedModel, CountedRetriever, TrackedTools
from deephelp_app.domain.models import (
    ChatRequest,
    ChatResult,
    DenseHit,
    DenseResult,
    DenseScope,
    EmbeddingResult,
    HybridScope,
    ModelToolCall,
    ModelUsage,
    VersionManifest,
)
from deephelp_app.evaluation import LABELS, semantic_label
from deephelp_app.event_cluster import EventAggregationService
from deephelp_app.event_replay import ReplayJudge
from deephelp_app.execution import ExecutionBudget
from deephelp_app.hybrid import result_for
from deephelp_app.intent import IntentService
from deephelp_app.memory import MemoryService
from deephelp_app.milvus_dense import create_client
from deephelp_app.milvus_hybrid import MilvusHybridStore
from deephelp_app.mvp_replay import REPLAY_SCOPE
from deephelp_app.mvp_runtime import LiveAssembly
from deephelp_app.ports import DenseRetrieverPort
from deephelp_app.sop import SOPExecutor
from deephelp_app.sop_governance import bundled_registry
from deephelp_app.text_entity import TextEntityProcessor
from deephelp_app.tool_gateway import ToolGateway
from deephelp_app.trace import TraceSink


class ObservedRetriever:
    def __init__(self, port: DenseRetrieverPort) -> None:
        self.port = port
        self.rows: list[dict[str, Any]] = []

    async def retrieve(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> DenseResult:
        result = await self.port.retrieve(text, scope, budget, top_k=top_k)
        self.rows.append(result.model_dump(mode="json"))
        return result


class OfflineModel:
    async def chat(self, request: ChatRequest, budget: ExecutionBudget) -> ChatResult:
        budget.claim_attempt(retry=False)
        if request.tools:
            data = json.loads(request.messages[-1].content or "{}")
            action = ModelToolCall(
                call_id="replay-action",
                name=data["required_action"],
                arguments=data["verified_parameters"],
            )
            return ChatResult(
                model="m17-synthetic-adapter",
                tool_calls=[action],
                usage=ModelUsage(),
                finish_reason="tool_calls",
            )
        data = json.loads(request.messages[-1].content or "{}")
        properties = (request.output_schema or {}).get("properties", {})
        if isinstance(properties, dict) and "entities" in properties:
            structured: dict[str, Any] = {"entities": []}
        else:
            label = semantic_label(data["text"])
            structured = {
                "code": label if label in LABELS[:3] else None,
                "reason": "supported" if label in LABELS[:3] else label,
            }
        return ChatResult(
            model="m17-synthetic-adapter",
            structured=structured,
            usage=ModelUsage(),
            finish_reason="stop",
        )

    async def embed(self, texts: list[str], budget: ExecutionBudget) -> EmbeddingResult:
        raise AssertionError("Offline evaluation never calls remote embedding")


class OfflineRetriever:
    async def retrieve(
        self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
    ) -> DenseResult:
        from deephelp_app.corpus import frozen_preview

        hits = [
            DenseHit(
                doc_id=r.doc_id,
                content=r.content,
                intent_code=r.intent_code,
                raw_score=0.1,
                scope=scope,
                metadata={},
            )
            for r in frozen_preview().index_records[:top_k]
        ]
        return DenseResult(scope=scope, hits=tuple(hits), candidates=())


class EvaluationAssembly:
    def __init__(self, mode: str, *, live: LiveAssembly | None = None) -> None:
        self.mode, self.live = mode, live
        self.retriever: ObservedRetriever | None = None
        self.tools: ToolGateway | None = None
        self.ledger: Any = None
        self.collection_deleted = False

    @asynccontextmanager
    async def open(
        self, client: httpx.AsyncClient, trace: TraceSink
    ) -> AsyncIterator[Conversation]:
        enabled = self.mode in {"memory_event", "fasttext"}
        if self.live:
            async with self.live.open(client, trace) as original:
                assert original.intent.cascade
                assert isinstance(original.intent.scope, HybridScope)
                scope = original.intent.scope
                milvus = create_client(self.live.root)
                model = CountedModel(self.live.gateway, self.live.gateway)
                assembly = self

                class DenseOnly:
                    async def retrieve(
                        self,
                        text: str,
                        scope: DenseScope,
                        budget: ExecutionBudget,
                        *,
                        top_k: int = 3,
                    ) -> DenseResult:
                        assert isinstance(scope, HybridScope)
                        # One bounded dense route on the exact same indexed records and signature.
                        from deephelp_app.dense import validate_vector

                        embedded = await model.embed([text], budget)
                        if embedded.signature != scope.signature or len(embedded.vectors) != 1:
                            raise ValueError("Evaluation embedding scope mismatch")
                        validate_vector(embedded.vectors[0], scope)
                        hits = await MilvusHybridStore(milvus, budget).search(
                            scope, embedded.vectors[0], top_k
                        )
                        return result_for(scope, "dense", hits, top_k, 1, 0)

                original_port = original.intent.dense
                if isinstance(original_port, CountedRetriever):
                    original_port = original_port.retrieval
                port = DenseOnly() if self.mode == "rule_dense" else original_port
                self.retriever = ObservedRetriever(port)
                policy = (
                    CascadePolicy() if self.mode == "rule_dense" else original.intent.cascade.policy
                )
                intent = IntentService(
                    model,
                    CountedRetriever(self.retriever),
                    scope,
                    top_k=original.intent.top_k,
                    policy=policy,
                    fallback=original.intent.cascade.fallback,
                )
                self.tools, self.ledger = self.live.tools, self.live.ledger
                try:
                    service = Conversation(
                        original.ledger,
                        original.text,
                        intent,
                        original.sop,
                        trace,
                        original.versions,
                        definitions=original.definitions,
                        memory=original.memory if enabled else None,
                        cases=original.cases if enabled else None,
                        events=original.events if enabled else None,
                    )
                    if assembly.mode == "rule_dense":
                        service.disabled += ("hybrid",)
                    yield service
                finally:
                    if self.live.event_index:
                        index = self.live.event_index
                        await index.rpc(
                            lambda: index.client.drop_collection(
                                index.collection, timeout=None, retry_times=0
                            )
                        )
                        self.collection_deleted = not await index.rpc(
                            lambda: index.client.has_collection(
                                index.collection, timeout=None, retry_times=0
                            )
                        )
                    await milvus.close()
        else:
            ledger, tools = MemoryCaseRepository(), ToolGateway()
            offline_model = OfflineModel()
            counted = CountedModel(offline_model, offline_model)
            self.retriever = ObservedRetriever(OfflineRetriever())
            self.tools, self.ledger = tools, ledger
            async with tools.open():
                yield Conversation(
                    ledger,
                    TextEntityProcessor(counted, trace=trace),
                    IntentService(
                        counted,
                        CountedRetriever(self.retriever),
                        REPLAY_SCOPE,
                        policy=CascadePolicy(),
                        fallback=StructuredFallback(counted),
                    ),
                    SOPExecutor(counted, TrackedTools(tools), registry=bundled_registry()),
                    trace,
                    VersionManifest(
                        registry="complaints-v1",
                        model="m17-synthetic-adapter",
                        prompt="m12-fallback-v2",
                        policy="cascade-policy-v1",
                    ),
                    memory=MemoryService(ledger) if enabled else None,
                    cases=ledger if enabled else None,
                    events=EventAggregationService(ledger, ReplayJudge()) if enabled else None,
                )
