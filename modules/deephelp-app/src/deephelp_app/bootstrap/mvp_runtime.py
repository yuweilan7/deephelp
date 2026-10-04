"""Explicit live assembly and cumulative budget gate. Importing this module does no I/O."""

import hmac
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import httpx
from fastapi import FastAPI, Request
from mcp import StdioServerParameters

from deephelp_app.adapters.approval_store import ApprovalRepository
from deephelp_app.adapters.cases import PostgresCaseRepository
from deephelp_app.adapters.milvus_dense import MilvusDenseStore, create_client
from deephelp_app.adapters.milvus_hybrid import MilvusHybridStore
from deephelp_app.adapters.milvus_memory import MilvusEventIndex
from deephelp_app.adapters.network_mcp import HTTPMCPConfig
from deephelp_app.adapters.providers import EndpointConfig, ProviderConfig, create_gateway
from deephelp_app.adapters.retrieval_mcp import RemoteRetriever
from deephelp_app.adapters.synthetic_rights import RightsClient
from deephelp_app.adapters.tool_gateway import ToolGateway
from deephelp_app.adapters.trace import TraceSink
from deephelp_app.api.app import create_app
from deephelp_app.application.approval import ApprovalService
from deephelp_app.application.artifact_paths import resolve_artifact
from deephelp_app.application.cascade import CascadePolicy, StructuredFallback
from deephelp_app.application.conversation import (
    Conversation,
    CountedModel,
    CountedRetriever,
    TrackedTools,
)
from deephelp_app.application.dense import DenseRetriever, atomic_json, load_json
from deephelp_app.application.event_cluster import EventAggregationService, StructuredClusterJudge
from deephelp_app.application.hybrid import HybridRetriever
from deephelp_app.application.intent import IntentService
from deephelp_app.application.memory import MemoryService, RedisMemory
from deephelp_app.application.reply import ReplyComposer
from deephelp_app.application.sop import SOPExecutor
from deephelp_app.application.sop_governance import RegistryStore, bundled_registry
from deephelp_app.application.text_entity import TextEntityProcessor
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import AsyncCalls, ExecutionBudget
from deephelp_app.domain.models import (
    DenseResult,
    DenseScope,
    ErrorCode,
    HybridScope,
    ReleaseManifest,
    VerifiedIdentity,
    VersionManifest,
)
from deephelp_app.resources import asset_path


class LocalAuth:
    def __init__(self, path: Path) -> None:
        rows = load_json(path).get("tokens")
        if not isinstance(rows, list) or not rows:
            raise ConfigurationError("Configure local bearer tokens mapped to synthetic identities")
        self.rows: list[tuple[str, VerifiedIdentity]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("token"), str):
                raise ConfigurationError("Invalid local authentication mapping")
            if len(row["token"]) < 32 or not row["token"].isascii():
                raise ConfigurationError("Use a random ASCII local token of at least 32 characters")
            self.rows.append((row["token"], VerifiedIdentity.model_validate(row.get("identity"))))
        if len({token for token, _ in self.rows}) != len(self.rows):
            raise ConfigurationError("Duplicate local bearer token")

    def __call__(self, request: Request) -> VerifiedIdentity:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token.isascii():
            token = ""
        for expected, identity in self.rows:
            if hmac.compare_digest(token, expected):
                return identity
        raise AppError(ErrorCode.UNAUTHENTICATED, "本机访问令牌无效。", 401)


class BudgetSession:
    """One process writer; synchronous reservations persist before external dispatch."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state: dict[str, Any] = load_json(path)
        self.lock_path = path.with_suffix(".lock")
        self.lock: Any = None
        for field in ("max_calls", "max_tokens", "max_cost_cny"):
            value = Decimal(str(self.state.get(field, 0)))
            if not value.is_finite() or value <= 0:
                raise ConfigurationError("Positive cumulative call/token/cost limits required")

    def open(self) -> None:
        try:
            self.lock = self.lock_path.open("x", encoding="utf-8")
        except FileExistsError:
            raise ConfigurationError("MVP budget is locked; inspect previous process") from None
        # Re-read after taking the writer lock; a constructor snapshot cannot authorize spend.
        self.state = load_json(self.path)

    def close(self) -> None:
        if self.lock:
            self.lock.close()
            self.lock_path.unlink()
            self.lock = None

    def request(self) -> ExecutionBudget:
        if self.lock is None:
            raise ConfigurationError("Budget writer is not open")
        calls = int(self.state["max_calls"]) - int(self.state["attempts"])
        tokens = int(self.state["max_tokens"]) - int(self.state["charged_tokens"])
        cost = Decimal(str(self.state["max_cost_cny"])) - Decimal(str(self.state["cost_upper_cny"]))
        if min(calls, tokens) <= 0 or cost <= 0:
            raise AppError(ErrorCode.BUDGET_EXHAUSTED, "MVP cumulative budget exhausted")
        budget = ExecutionBudget.start(
            90,
            min(40, calls),
            2,
            token_limit=min(150000, tokens),
            cost_limit=min(Decimal("2"), cost),
        )
        previous = {
            "attempts": 0,
            "tokens": 0,
            "charged_tokens": 0,
            "cost_upper_cny": Decimal("0"),
            "uncertain_attempts": 0,
        }

        def persist() -> None:
            current = {
                "attempts": budget.attempts_used,
                "tokens": budget.tokens_used,
                "charged_tokens": budget.token_upper_bound,
                "cost_upper_cny": budget.cost_upper_bound,
                "uncertain_attempts": budget.uncertain_attempts,
            }
            for key, value in current.items():
                updated = (
                    Decimal(str(self.state[key]))
                    + Decimal(str(value))
                    - Decimal(str(previous[key]))
                )
                self.state[key] = str(updated) if key == "cost_upper_cny" else int(updated)
            previous.update(current)
            atomic_json(self.path, self.state)
            if (
                self.state["attempts"] > self.state["max_calls"]
                or self.state["charged_tokens"] > self.state["max_tokens"]
                or Decimal(self.state["cost_upper_cny"]) > Decimal(str(self.state["max_cost_cny"]))
            ):
                raise AppError(ErrorCode.BUDGET_EXHAUSTED, "Cumulative reservation exhausted")

        budget.on_change = persist
        return budget


def validate_control_paths(paths: list[Path], *, output: Path | None = None) -> None:
    reserved: set[Path] = set()
    for path in paths:
        owned = {
            path.resolve(),
            path.with_suffix(".lock").resolve(),
            path.with_suffix(".tmp").resolve(),
            path.with_suffix(path.suffix + ".tmp").resolve(),
        }
        if reserved & owned:
            raise ConfigurationError("Auth/budget/pointer/provider control paths must differ")
        reserved.update(owned)
    if output:
        generated = {
            output.resolve(),
            output.with_suffix(".trace.jsonl").resolve(),
            output.with_suffix(".restart.jsonl").resolve(),
        }
        if reserved & generated or len(generated) != 3:
            raise ConfigurationError("Report and derived trace paths overlap control files")


def validate_trace_path(path: Path, controls: list[Path]) -> None:
    validate_control_paths([*controls, path])
    key = path.with_suffix(path.suffix + ".key").resolve()
    if key in {control.resolve() for control in controls}:
        raise ConfigurationError("Trace redaction key overlaps a control file")


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
    ) -> None:
        self.root, self.config, self.pointer = (
            root,
            ProviderConfig.load(providers),
            load_json(pointer),
        )
        self.release_manifest, self.release_channel = release_manifest, release_channel
        self.judge_providers = (
            resolve_artifact(release_manifest.judge_providers_path)
            if release_manifest
            else root / "modules/deephelp-app/event-judge.example.json"
        )
        active = self.pointer.get("active")
        self.scope = (
            HybridScope.model_validate(active)
            if isinstance(active, dict) and active.get("index_kind") == "intent_hybrid"
            else DenseScope.model_validate(active)
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
        if release_manifest:
            from deephelp_app.adapters.release_assets import verify_release
            from deephelp_app.application.sop_governance import SOPRegistry

            verify_release(release_manifest)
            self.sop_registry = SOPRegistry.model_validate(release_manifest.sop_registry)
            self.sop_snapshots = (self.sop_registry,)
        from deephelp_app.adapters.retrieval_protocol import load_binding

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
        if self.tool_server is None and getattr(self, "business_mcp", None) is None:
            raise ConfigurationError(
                "Select an explicit MCP server before opening runtime dependencies"
            )
        async with AsyncExitStack() as stack:
            repository = ApprovalRepository if self.rights_port else PostgresCaseRepository
            if self.release_manifest:
                from deephelp_app.adapters.release_store import ReleaseRepository

                repository = ReleaseRepository
            if self.rights_port or self.rights_key:
                raise ConfigurationError("PostgreSQL approval execution requires M26")
            ledger = await repository.open(self.root)
            if self.release_manifest:
                assert isinstance(ledger, ReleaseRepository)
                ledger.manifest, ledger.channel = self.release_manifest, self.release_channel
            stack.push_async_callback(ledger.aclose)
            self.ledger = ledger
            cache = RedisMemory.open(self.root)
            stack.push_async_callback(cache.aclose)
            memory = MemoryService(ledger, cache)
            gateway = create_gateway(client, self.config, AsyncCalls(2, 30))
            self.gateway = gateway
            model = CountedModel(gateway, gateway)
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
            tools = ToolGateway(server=self.tool_server, http=self.business_mcp)
            self.tools = tools
            await stack.enter_async_context(tools.open())
            assembly = self

            if self.retrieval_mcp:
                from deephelp_app.application.conversation import current_run

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
                            model, MilvusHybridStore(milvus, budget), dense_weight=weight
                        ).retrieve(text, scope, budget, top_k=top_k)
                    return await DenseRetriever(model, MilvusDenseStore(milvus, budget)).retrieve(
                        text, scope, budget, top_k=top_k
                    )

            fallback: Any = StructuredFallback(strong_model)
            classifier = None
            if self.fasttext_pointer is not None:
                from deephelp_app.adapters.fasttext_runtime import (
                    FastTextClassifier,
                    FastTextFallback,
                    pointer_manifest,
                )

                classifier = FastTextClassifier(pointer_manifest(self.fasttext_pointer))
                fallback = FastTextFallback(classifier, fallback)
            release_identity = None
            if self.release_manifest:
                from deephelp_app.adapters.release_assets import release_hash

                release_identity = release_hash(self.release_manifest)
            versions = VersionManifest(
                registry="complaints-v1",
                dataset=self.scope.dataset_version,
                split="reference",
                embedding_signature=self.scope.signature.fingerprint,
                model=endpoint.model,
                provider=self.config.provider,
                prompt="m12-fallback-v2",
                policy="cascade-policy-v1",
                fasttext_model=classifier.manifest.version if classifier else None,
                fasttext_preprocessing=classifier.preprocessor.signature if classifier else None,
                fasttext_policy=classifier.manifest.policy_version if classifier else None,
                release_manifest=release_identity,
                code_commit=self.release_manifest.code_commit if self.release_manifest else None,
            )
            yield Conversation(
                ledger,
                TextEntityProcessor(model, trace=trace, reviewed_rules=self.reviewed_rules),
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
                ),
                trace,
                versions,
                memory=memory,
                cases=ledger,
                events=events,
                reply_composer=ReplyComposer(model if self.reply_polish else None),
                approvals=ApprovalService(
                    ledger,
                    RightsClient(client, self.rights_port, self.rights_key.read_bytes()),
                    self.sop_registry.snapshot_hash,
                )
                if isinstance(ledger, ApprovalRepository) and self.rights_port and self.rights_key
                else None,
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
) -> FastAPI:
    from deephelp_app.api.workbench import BrowserAuth, workbench_router

    browser_auth = BrowserAuth(auth)
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
            },
        )
    )
    app.state.browser_auth = browser_auth
    if config := getattr(assembly, "business_mcp", None):
        from deephelp_app.adapters.business_client import BusinessClient
        from deephelp_app.api.business import business_router

        app.include_router(business_router(BusinessClient(config), browser_auth))
    return app
