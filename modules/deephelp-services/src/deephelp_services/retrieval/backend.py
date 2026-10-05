"""Owned published-corpus retrieval through the official Streamable HTTP MCP SDK."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx

from deephelp_app.adapters.mcp.retrieval.retrieval_protocol import (
    NAMES,
    binding_from_files,
)
from deephelp_app.adapters.model_gateway.providers import ProviderConfig, create_gateway
from deephelp_app.adapters.observability.telemetry import observation
from deephelp_app.adapters.retrieval.milvus_dense import MilvusDenseStore, create_client
from deephelp_app.adapters.retrieval.milvus_hybrid import MilvusHybridStore
from deephelp_app.application.dense import DenseRetriever
from deephelp_app.application.hybrid import HybridRetriever
from deephelp_app.bootstrap.runtime.auth import LocalAuth
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import AsyncCalls, ExecutionBudget
from deephelp_app.domain.models.common import ErrorCode
from deephelp_app.domain.models.retrieval import DenseResult, HybridScope
from deephelp_app.domain.models.retrieval_protocol import (
    RetrievalAction,
    RetrievalQuery,
)


class ObservedEmbedding:
    def __init__(self, gateway: Any, actions: list[RetrievalAction]) -> None:
        self.gateway, self.actions = gateway, actions

    async def embed(self, texts: list[str], budget: ExecutionBudget) -> Any:
        return await self.invoke("embedding", lambda: self.gateway.embed(texts, budget), budget)

    async def rerank(self, request: Any, budget: ExecutionBudget) -> Any:
        return await self.invoke("rerank", lambda: self.gateway.rerank(request, budget), budget)

    async def invoke(
        self,
        action: Literal["embedding", "rerank"],
        operation: Callable[[], Awaitable[Any]],
        budget: ExecutionBudget,
    ) -> Any:
        before, started = budget.usage(), time.perf_counter()
        ids: tuple[str, ...] = ()
        try:
            result = await operation()
            ids = (
                tuple(result.provider_request_ids)
                if action == "embedding"
                else (result.provider_request_id,)
                if result.provider_request_id
                else ()
            )
            return result
        except AppError as exc:
            if exc.provider_request_id:
                ids = (exc.provider_request_id,)
            raise
        finally:
            after = budget.usage()
            self.actions.append(
                RetrievalAction(
                    action=action,
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                    attempts=after.attempts - before.attempts,
                    model_calls=after.attempts - before.attempts,
                    tokens=(after.tokens or 0) - (before.tokens or 0),
                    charged_tokens=(after.token_upper_bound or 0) - (before.token_upper_bound or 0),
                    uncertain_attempts=after.uncertain_attempts - before.uncertain_attempts,
                    provider_request_ids=ids,
                )
            )


class ObservedMilvus:
    def __init__(self, client: Any, actions: list[RetrievalAction]) -> None:
        self.client, self.actions = client, actions

    async def invoke(self, action: Any, operation: Callable[[], Awaitable[Any]]) -> Any:
        with observation("retrieval." + action):
            started = time.perf_counter()
            try:
                return await operation()
            finally:
                self.actions.append(
                    RetrievalAction(
                        action=action,
                        elapsed_ms=(time.perf_counter() - started) * 1000,
                        attempts=1,
                    )
                )

    async def describe_collection(self, *args: Any, **kwargs: Any) -> Any:
        return await self.invoke(
            "milvus_validate", lambda: self.client.describe_collection(*args, **kwargs)
        )

    async def search(self, *args: Any, **kwargs: Any) -> Any:
        action = "bm25" if kwargs.get("anns_field") == "sparse" else "dense"
        return await self.invoke(action, lambda: self.client.search(*args, **kwargs))

    async def hybrid_search(self, *args: Any, **kwargs: Any) -> Any:
        return await self.invoke("fusion", lambda: self.client.hybrid_search(*args, **kwargs))


class RetrievalBackend:
    def __init__(
        self, binding: Any, subjects: set[tuple[str, str]], gateway: Any, milvus: Any
    ) -> None:
        self.binding, self.subjects, self.gateway, self.milvus = binding, subjects, gateway, milvus
        self.slots = asyncio.Semaphore(2)

    @classmethod
    @asynccontextmanager
    async def open(
        cls, root: Path, providers: Path, pointer: Path, corpus: Path, auth: Path
    ) -> AsyncIterator[RetrievalBackend]:
        config = ProviderConfig.load(providers)
        binding = binding_from_files(pointer, config, corpus)
        identities = LocalAuth(auth)
        subjects = {(i.tenant_id, i.user_id) for _, i in identities.rows}
        async with AsyncExitStack() as stack:
            http = await stack.enter_async_context(httpx.AsyncClient(trust_env=False, timeout=35))
            gateway = create_gateway(http, config, AsyncCalls(2, 30))
            milvus = create_client(root)
            stack.push_async_callback(milvus.close)
            store_type = (
                MilvusHybridStore if isinstance(binding.scope, HybridScope) else MilvusDenseStore
            )
            await store_type(milvus, ExecutionBudget.start(30, 10, 0)).validate(
                binding.scope, binding.corpus_digest
            )
            yield cls(binding, subjects, gateway, milvus)

    async def execute(
        self,
        name: str,
        query: RetrievalQuery,
        budget: ExecutionBudget,
        actions: list[RetrievalAction],
    ) -> dict[str, DenseResult]:
        async with self.slots:
            embedding = ObservedEmbedding(self.gateway, actions)
            milvus: Any = ObservedMilvus(self.milvus, actions)
            scope = self.binding.scope
            if isinstance(scope, HybridScope):
                retriever = HybridRetriever(
                    embedding,
                    MilvusHybridStore(milvus, budget),
                    dense_weight=self.binding.weight,
                    reranker=embedding if self.binding.rerank_policy else None,
                    rerank_policy=self.binding.rerank_policy,
                    records=self.binding.records,
                    corpus_digest=self.binding.corpus_digest,
                )
                if name == NAMES[1]:
                    return await retriever.compare(query.query, scope, budget, top_k=query.top_k)
                return {
                    self.binding.mode: await retriever.retrieve(
                        query.query, scope, budget, top_k=query.top_k
                    )
                }
            if name == NAMES[1]:
                raise AppError(ErrorCode.VERSION_CONFLICT, "Published scope has no BM25 route")
            return {
                "dense": await DenseRetriever(embedding, MilvusDenseStore(milvus, budget)).retrieve(
                    query.query, scope, budget, top_k=query.top_k
                )
            }
