"""显式归属学习/探针装配，资源不含主意图服务或SOP工具。

复用原PG窗口、文本提取与事件归属，AsyncExitStack负责依赖生命周期。
这条独立入口只证明归属机制，不冒称完整十三段业务闭环。
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import httpx

from deephelp_app.adapters.memory_projection.milvus_memory import MilvusEventIndex
from deephelp_app.adapters.model_gateway.providers import (
    EndpointConfig,
    ProviderConfig,
    create_gateway,
)
from deephelp_app.adapters.persistence.cases import PostgresCaseRepository
from deephelp_app.adapters.retrieval.milvus_dense import create_client
from deephelp_app.application.event_cluster.aggregation import EventAggregationService
from deephelp_app.application.event_cluster.judge import StructuredClusterJudge
from deephelp_app.application.event_cluster.persistence import PersistentEventAggregation
from deephelp_app.application.memory import MemoryService, ProjectionWorker, RedisMemory
from deephelp_app.application.text_entity import TextEntityProcessor
from deephelp_app.domain.execution import AsyncCalls
from deephelp_app.domain.ports import ChatPort


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
        repository = await PostgresCaseRepository.open(root)
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
