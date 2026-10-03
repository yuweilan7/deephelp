"""Independent M11 runtime; its resources do not include main classification or tools."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx

from deephelp_app.cases import MySQLCaseRepository
from deephelp_app.event_cluster import (
    EventAggregationService,
    PersistentEventAggregation,
    StructuredClusterJudge,
)
from deephelp_app.execution import AsyncCalls
from deephelp_app.memory import MemoryService, ProjectionWorker, RedisMemory
from deephelp_app.milvus_dense import create_client
from deephelp_app.milvus_memory import MilvusEventIndex
from deephelp_app.ports import ChatPort
from deephelp_app.providers import EndpointConfig, ProviderConfig, create_gateway
from deephelp_app.text_entity import TextEntityProcessor


@asynccontextmanager
async def live_events(
    root: Path,
    config: ProviderConfig,
    *,
    collection: str | None = None,
    namespace: str = "deephelp:m10",
    judge_endpoint: EndpointConfig | None = None,
) -> AsyncIterator[tuple[PersistentEventAggregation, ProjectionWorker, MilvusEventIndex, ChatPort]]:
    async with AsyncExitStack() as stack:
        repository = await MySQLCaseRepository.open(root)
        stack.push_async_callback(repository.aclose)
        cache = RedisMemory.open(root, namespace=namespace)
        stack.push_async_callback(cache.aclose)
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=httpx.AsyncHTTPTransport(retries=0),
                timeout=60,
                trust_env=False,
            )
        )
        model = create_gateway(client, config, AsyncCalls(2, 45))
        judge_model = create_gateway(
            client,
            config.model_copy(update={"chat": judge_endpoint or config.chat}),
            AsyncCalls(2, 45),
        )
        milvus = create_client(root)
        stack.push_async_callback(milvus.close)
        index = MilvusEventIndex(milvus, model, config.signature(), collection)
        await index.initialize()
        service = EventAggregationService(
            repository,
            StructuredClusterJudge(judge_model),
            similarity=index,
            text=TextEntityProcessor(model),
        )
        memory = MemoryService(repository, cache, index)
        yield (
            PersistentEventAggregation(repository, service),
            ProjectionWorker(repository, memory, index),
            index,
            judge_model,
        )
