"""显式打开原业务依赖并组成同一Conversation，引擎不导入学习/探针实现。

构造阶段只选择配置/资产，open才取得PG、HTTP、Milvus、Redis、MCP与审批资源。
AsyncExitStack拥有已成功打开的资源，后续失败/取消也逆序关闭；先开者最后关。
调用路径可选本地/远程检索，身份、版本与累计预算仍由原装配守卫。
"""

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

import httpx
from fastapi import FastAPI
from mcp import StdioServerParameters

from deephelp_app.adapters.approval.approval_store import ApprovalRepository
from deephelp_app.adapters.approval.synthetic_rights import RightsClient
from deephelp_app.adapters.mcp.business.tool_gateway import ToolGateway
from deephelp_app.adapters.mcp.retrieval.retrieval_mcp import RemoteRetriever
from deephelp_app.adapters.mcp.shared.network_mcp import HTTPMCPConfig
from deephelp_app.adapters.memory_projection.milvus_memory import MilvusEventIndex
from deephelp_app.adapters.model_gateway.providers import (
    EndpointConfig,
    ProviderConfig,
    create_gateway,
)
from deephelp_app.adapters.observability.trace import TraceSink
from deephelp_app.adapters.persistence.agent_tasks import PostgresAgentTasks
from deephelp_app.adapters.persistence.cases import PostgresCaseRepository
from deephelp_app.adapters.retrieval.milvus_dense import MilvusDenseStore, create_client
from deephelp_app.adapters.retrieval.milvus_hybrid import MilvusHybridStore
from deephelp_app.api.app import create_app
from deephelp_app.application.approval import ApprovalService
from deephelp_app.application.artifact_paths import resolve_artifact
from deephelp_app.application.cascade import CascadePolicy, StructuredFallback
from deephelp_app.application.collaboration import load_agent_policy
from deephelp_app.application.conversation.orchestrator import Conversation
from deephelp_app.application.conversation.tracking import (
    CountedModel,
    CountedRetriever,
    TrackedTools,
)
from deephelp_app.application.dense import DenseRetriever, load_json
from deephelp_app.application.event_cluster.aggregation import EventAggregationService
from deephelp_app.application.event_cluster.judge import StructuredClusterJudge
from deephelp_app.application.hybrid import HybridRetriever
from deephelp_app.application.intent import IntentService
from deephelp_app.application.memory import MemoryService, RedisMemory
from deephelp_app.application.reply import ReplyComposer
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import RegistryStore, bundled_registry
from deephelp_app.application.text_entity import TextEntityProcessor
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import ConfigurationError
from deephelp_app.domain.execution import AsyncCalls, ExecutionBudget
from deephelp_app.domain.models.common import VerifiedIdentity
from deephelp_app.domain.models.release import ReleaseManifest, VersionManifest
from deephelp_app.domain.models.retrieval import DenseResult, DenseScope, HybridScope
from deephelp_app.resources import asset_path

from .auth import LocalAuth
from .budget import BudgetSession


class LiveAssembly:
    def __init__(
        self,
        root: Path,
        providers: Path,
        pointer: Path,
        *,
        tool_server: StdioServerParameters | None = None,
        business_mcp: HTTPMCPConfig | None = None,
        retrieval_mcp: HTTPMCPConfig | None = None,
        corpus_path: Path | None = None,
        cascade_policy: Path | None = None,
        event_collection: str | None = None,
        fasttext_pointer: Path | None = None,
        sop_directory: Path | None = None,
        reply_polish: bool = False,
        reviewed_rules: Path | None = None,
        rights_port: int | None = None,
        rights_key: Path | None = None,
        release_manifest: ReleaseManifest | None = None,
        release_channel: str | None = None,
        prompt_artifact: Path | None = None,
        skill_review: Path | None = None,
    ) -> None:
        self.root, self.config, self.pointer = (
            root,
            ProviderConfig.load(providers),
            load_json(pointer),
        )
        self.release_manifest, self.release_channel = release_manifest, release_channel
        from deephelp_app.application.intent_prompt import published_prompt, read_prompt

        active = self.pointer.get("active")
        self.scope = (
            HybridScope.model_validate(active)
            if isinstance(active, dict) and active.get("index_kind") == "intent_hybrid"
            else DenseScope.model_validate(active)
        )
        if release_manifest and prompt_artifact:
            raise ConfigurationError("Published runtime Prompt must come from its ReleaseManifest")
        binding = published_prompt(release_manifest) if release_manifest else None
        self.intent_prompt = (
            binding[1]
            if binding
            else (
                read_prompt(prompt_artifact, self.scope.registry_version)
                if prompt_artifact
                else None
            )
        )
        self.judge_providers = (
            resolve_artifact(release_manifest.judge_providers_path)
            if release_manifest
            else root / "modules/deephelp-app/event-judge.example.json"
        )
        self.top_k = 3
        self.cascade_policy_path = cascade_policy
        self.event_collection = event_collection
        self.fasttext_pointer = fasttext_pointer
        self.reply_polish = reply_polish
        self.reviewed_rules = reviewed_rules
        if (rights_port is None) != (rights_key is None):
            raise ConfigurationError("M15 needs both a synthetic rights port and key")
        self.rights_port, self.rights_key = rights_port, rights_key
        self.sop_snapshots = RegistryStore(sop_directory).history() if sop_directory else ()
        if sop_directory and not self.sop_snapshots:
            raise ConfigurationError(
                "Publish a SOP registry before explicitly enabling its directory"
            )
        self.sop_registry = self.sop_snapshots[0] if self.sop_snapshots else bundled_registry()
        self.agent_policy = load_agent_policy()
        from deephelp_app.application.skills import SkillRegistry, load_skills

        if release_manifest and skill_review:
            raise ConfigurationError("Released Skill must come from its immutable manifest")
        # 候选读取原审核快照，不让包内默认Skill替代云端现行版本。
        # 只有文件验证，不提交审核或扩大工具许可。
        reviewed_skills = None
        if skill_review:
            from deephelp_app.adapters.releases.skill_store import read_skill_review

            reviewed_skills, _ = read_skill_review(skill_review)
        self.skills_registry = (
            SkillRegistry.model_validate(release_manifest.skill_registry)
            if release_manifest and release_manifest.skill_registry
            else reviewed_skills or load_skills()
        )
        self.skills_enabled = (
            release_manifest is None or release_manifest.skill_registry is not None
        )
        if release_manifest:
            from deephelp_app.adapters.releases.release_assets import verify_release
            from deephelp_app.application.sop_governance import SOPRegistry

            verify_release(release_manifest)
            self.sop_registry = SOPRegistry.model_validate(release_manifest.sop_registry)
            self.sop_snapshots = (self.sop_registry,)
        from deephelp_app.adapters.mcp.retrieval.retrieval_protocol import load_binding

        self.retrieval_binding = load_binding(self.pointer, self.config, corpus_path)
        self.scope, self.top_k = self.retrieval_binding.scope, self.retrieval_binding.top_k
        self.retrieval_mcp = retrieval_mcp
        self.retrieval: RemoteRetriever | None = None
        if retrieval_mcp:
            if retrieval_mcp.audience != "retrieval" or corpus_path is None:
                raise ConfigurationError(
                    "Remote retrieval requires explicit corpus and credentials"
                )
            retrieval_mcp.credentials()
        if tool_server is not None and business_mcp is not None:
            raise ConfigurationError("Choose one explicit business MCP source")
        if business_mcp:
            business_mcp.credentials()
        self.tool_server, self.business_mcp = tool_server, business_mcp
        self.gateway: Any = None
        self.judge_gateway: Any = None
        self.event_index: MilvusEventIndex | None = None
        self.tools: ToolGateway | None = None
        self.ledger: PostgresCaseRepository | None = None

    @asynccontextmanager
    async def open(
        self, client: httpx.AsyncClient, trace: TraceSink
    ) -> AsyncIterator[Conversation]:
        """打开原引擎的一组依赖并yield Conversation，当前上下文拥有它们。

        同Task进入/退出MCP与AsyncExitStack，部分打开失败也逆序关闭已开资源。
        实例只读所选运行资产，不从learning/probes或dev/test构造生产结果。
        """
        if self.tool_server is None and getattr(self, "business_mcp", None) is None:
            raise ConfigurationError(
                "Select an explicit MCP server before opening runtime dependencies"
            )
        # assembly拥有成功打开的资源，退出按登记逆序关闭；client是外层借入资源。
        async with AsyncExitStack() as stack:
            repository = ApprovalRepository if self.rights_port else PostgresCaseRepository
            if self.release_manifest:
                from deephelp_app.adapters.releases.release_store import ReleaseRepository

                repository = ReleaseRepository
            if bool(self.rights_port) != bool(self.rights_key):
                raise ConfigurationError("Synthetic rights requires both port and signing key")
            ledger = await repository.open(self.root)
            if self.release_manifest:
                assert isinstance(ledger, ReleaseRepository)
                ledger.manifest, ledger.channel = self.release_manifest, self.release_channel
            stack.push_async_callback(ledger.aclose)
            self.ledger = ledger
            approvals = None
            if isinstance(ledger, ApprovalRepository) and self.rights_port and self.rights_key:
                approvals = await stack.enter_async_context(
                    ApprovalService.open(
                        ledger,
                        RightsClient(client, self.rights_port, self.rights_key.read_bytes()),
                        self.sop_registry.snapshot_hash,
                        root=self.root,
                    )
                )
            cache = RedisMemory.open(self.root)
            stack.push_async_callback(cache.aclose)
            from deephelp_app.adapters.persistence.long_memory import PostgresLongMemory
            from deephelp_app.application.long_memory import LongMemoryService

            memory = MemoryService(
                ledger,
                cache,
                long_term=LongMemoryService(PostgresLongMemory(ledger.pool))
                if self.release_manifest is None or self.release_manifest.memory_binding is not None
                else None,
            )
            gateway = create_gateway(client, self.config, AsyncCalls(2, 30))
            self.gateway = gateway
            model = CountedModel(gateway, gateway, gateway)
            endpoint = EndpointConfig.model_validate_json(
                self.judge_providers.read_text(encoding="utf-8")
            )
            judge_gateway = create_gateway(
                client, self.config.model_copy(update={"chat": endpoint}), AsyncCalls(2, 45)
            )
            self.judge_gateway = judge_gateway
            strong_model = CountedModel(judge_gateway, gateway)
            milvus = create_client(self.root)
            stack.push_async_callback(milvus.close)
            # Startup validates the already-published corpus, without repairing or importing it.
            startup = ExecutionBudget.start(30, 10, 0)
            store_type = (
                MilvusHybridStore if isinstance(self.scope, HybridScope) else MilvusDenseStore
            )
            if self.retrieval_mcp is None:
                await store_type(milvus, startup).validate(
                    self.scope, str(self.pointer["corpus_digest"])
                )
            event_index = MilvusEventIndex(
                milvus, model, self.config.signature(), self.event_collection
            )
            await event_index.initialize()
            self.event_index = event_index
            events = EventAggregationService(
                ledger, StructuredClusterJudge(strong_model), similarity=event_index
            )
            policy_path = self.cascade_policy_path or asset_path("m12_policy.json")
            policy = (
                CascadePolicy.load(policy_path)
                if isinstance(self.scope, HybridScope)
                else CascadePolicy()
            )
            if policy.dense_weight is not None and policy.dense_weight != self.pointer.get(
                "dense_weight"
            ):
                raise ConfigurationError("Cascade calibration retrieval weight differs")
            if policy.corpus_digest and policy.corpus_digest != self.pointer.get("corpus_digest"):
                raise ConfigurationError("Cascade calibration corpus differs")
            if self.scope.registry_version != self.sop_registry.catalog_version:
                raise ConfigurationError("Retrieval and SOP catalog versions differ")
            tools = ToolGateway(
                server=self.tool_server,
                http=self.business_mcp,
                catalog_version=self.sop_registry.catalog_version,
            )
            self.tools = tools
            await stack.enter_async_context(tools.open())
            assembly = self

            if self.retrieval_mcp:
                from deephelp_app.application.conversation.tracking import current_run

                def record_retrieval(record: dict[str, Any]) -> None:
                    if stats := current_run.get():
                        stats.model_calls += record.get("model_calls") or 0
                        stats.retrievals.append(record)

                self.retrieval = await stack.enter_async_context(
                    RemoteRetriever(
                        self.retrieval_mcp, self.retrieval_binding, on_record=record_retrieval
                    ).open()
                )

            class RequestDense:
                async def retrieve(
                    self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
                ) -> DenseResult:
                    # The store receives the caller's budget, never the startup budget.
                    if assembly.retrieval is not None:
                        return await assembly.retrieval.retrieve(text, scope, budget, top_k=top_k)
                    if isinstance(scope, HybridScope):
                        weight = float(str(assembly.pointer.get("dense_weight", 0.5)))
                        return await HybridRetriever(
                            model,
                            MilvusHybridStore(milvus, budget),
                            dense_weight=weight,
                            reranker=model if assembly.retrieval_binding.rerank_policy else None,
                            rerank_policy=assembly.retrieval_binding.rerank_policy,
                            records=assembly.retrieval_binding.records,
                            corpus_digest=assembly.retrieval_binding.corpus_digest,
                        ).retrieve(text, scope, budget, top_k=top_k)
                    return await DenseRetriever(model, MilvusDenseStore(milvus, budget)).retrieve(
                        text, scope, budget, top_k=top_k
                    )

            fallback: Any = StructuredFallback(
                strong_model,
                system_prompt=self.intent_prompt.text if self.intent_prompt else None,
                catalog_version=self.sop_registry.catalog_version,
            )
            classifier = None
            if self.fasttext_pointer is not None:
                from deephelp_app.adapters.model_gateway.fasttext_runtime import (
                    FastTextClassifier,
                    FastTextFallback,
                    pointer_manifest,
                )

                classifier = FastTextClassifier(pointer_manifest(self.fasttext_pointer))
                fallback = FastTextFallback(classifier, fallback)
            release_identity = None
            if self.release_manifest:
                from deephelp_app.adapters.releases.release_assets import release_hash

                release_identity = release_hash(self.release_manifest)
            versions = VersionManifest(
                registry=self.sop_registry.catalog_version,
                dataset=self.scope.dataset_version,
                split="reference",
                embedding_signature=self.scope.signature.fingerprint,
                model=endpoint.model,
                provider=self.config.provider,
                prompt=(
                    f"{self.intent_prompt.name}-v{self.intent_prompt.version}-{self.intent_prompt.request_digest[:12]}"
                    if self.intent_prompt
                    else "m12-fallback-v2"
                ),
                policy="cascade-policy-v1",
                fasttext_model=classifier.manifest.version if classifier else None,
                fasttext_preprocessing=classifier.preprocessor.signature if classifier else None,
                fasttext_policy=classifier.manifest.policy_version if classifier else None,
                release_manifest=release_identity,
                code_commit=self.release_manifest.code_commit if self.release_manifest else None,
                collaboration=self.agent_policy.digest,
                skills=self.skills_registry.digest if self.skills_enabled else None,
                memory="m46-explicit-v1" if memory.long_term else None,
            )
            yield Conversation(
                ledger,
                TextEntityProcessor(
                    model,
                    trace=trace,
                    reviewed_rules=self.reviewed_rules,
                    catalog_version=self.sop_registry.catalog_version,
                ),
                IntentService(
                    model,
                    CountedRetriever(RequestDense()),
                    assembly.scope,
                    top_k=assembly.top_k,
                    policy=policy,
                    fallback=fallback,
                ),
                SOPExecutor(
                    model,
                    TrackedTools(tools),
                    registry=self.sop_registry,
                    history=self.sop_snapshots,
                    evidence_version=tools.evidence_version,
                    agent_policy=self.agent_policy,
                    agent_journal=PostgresAgentTasks(ledger.pool),
                    skills=self.skills_registry,
                    skills_enabled=self.skills_enabled,
                ),
                trace,
                versions,
                memory=memory,
                cases=ledger,
                events=events,
                reply_composer=ReplyComposer(model if self.reply_polish else None),
                approvals=approvals,
            )


class ConversationAssembly(Protocol):
    def open(
        self, client: httpx.AsyncClient, trace: TraceSink
    ) -> AbstractAsyncContextManager[Conversation]: ...


def live_app(
    assembly: ConversationAssembly,
    auth: LocalAuth,
    budget: BudgetSession,
    trace_path: str = ".local/m08/trace.jsonl",
    *,
    personal_identity: VerifiedIdentity | None = None,
) -> FastAPI:
    from deephelp_app.api.workbench import BrowserAuth, workbench_router

    browser_auth = BrowserAuth(auth, identity=personal_identity)
    app = create_app(
        Settings(
            mode="mvp",
            request_timeout=90,
            child_timeout=30,
            max_attempts=40,
            retry_limit=2,
            trace_path=trace_path,
        ),
        identity_provider=browser_auth,
        conversation_factory=assembly.open,
        budget_factory=budget.request,
    )
    app.include_router(
        workbench_router(
            browser_auth,
            lambda: {
                "mode": "live",
                "business_source": "postgresql"
                if getattr(assembly, "business_mcp", None)
                else "demo",
                "remaining_calls": max(0, budget.state["max_calls"] - budget.state["attempts"]),
                "max_tokens": budget.state["max_tokens"],
                "charged_tokens": budget.state["charged_tokens"],
                "request_timeout_seconds": 90,
                "expected_calls_per_message": "0–8（含Embedding，复杂多轮依原预算）",
                "business_maintenance": bool(getattr(assembly, "business_mcp", None)),
                "collaboration_available": bool(getattr(assembly, "agent_policy", None)),
            },
        )
    )
    app.state.browser_auth = browser_auth
    if config := getattr(assembly, "business_mcp", None):
        from deephelp_app.adapters.mcp.business.business_client import BusinessClient
        from deephelp_app.api.business import business_router

        app.include_router(business_router(BusinessClient(config), browser_auth))
    return app
