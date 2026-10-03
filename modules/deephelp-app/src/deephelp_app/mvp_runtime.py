"""Explicit live assembly and cumulative budget gate. Importing this module does no I/O."""

import hmac
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request

from deephelp_app.app import create_app
from deephelp_app.cascade import CascadePolicy, StructuredFallback
from deephelp_app.cases import MySQLCaseRepository
from deephelp_app.conversation import Conversation, CountedModel, CountedRetriever, TrackedTools
from deephelp_app.dense import DenseRetriever, atomic_json, load_json
from deephelp_app.domain.models import (
    DenseResult,
    DenseScope,
    ErrorCode,
    HybridScope,
    VerifiedIdentity,
    VersionManifest,
)
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.event_cluster import EventAggregationService, StructuredClusterJudge
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.hybrid import HybridRetriever
from deephelp_app.intent import IntentService
from deephelp_app.mcp_mock import MockConfig
from deephelp_app.memory import MemoryService, RedisMemory
from deephelp_app.milvus_dense import MilvusDenseStore, create_client
from deephelp_app.milvus_hybrid import MilvusHybridStore
from deephelp_app.milvus_memory import MilvusEventIndex
from deephelp_app.providers import EndpointConfig, ProviderConfig, create_gateway
from deephelp_app.settings import Settings
from deephelp_app.sop import SOPExecutor
from deephelp_app.text_entity import TextEntityProcessor
from deephelp_app.tool_gateway import ToolGateway
from deephelp_app.trace import TraceSink


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


class LiveAssembly:
    def __init__(
        self,
        root: Path,
        providers: Path,
        pointer: Path,
        *,
        mock: MockConfig | None = None,
        cascade_policy: Path | None = None,
        event_collection: str | None = None,
        fasttext_pointer: Path | None = None,
    ) -> None:
        self.root, self.config, self.pointer = (
            root,
            ProviderConfig.load(providers),
            load_json(pointer),
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
        if isinstance(self.scope, HybridScope):
            from deephelp_app.hybrid_eval import dataset, validate_selection

            corpus, queries = dataset()
            selection = self.pointer.get("selection")
            if not isinstance(selection, dict) or self.pointer.get("format") != "m09-pointer-v1":
                raise ConfigurationError("Hybrid pointer requires a frozen retrieval policy")
            top_k = selection.get("candidate_budget")
            if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 20:
                raise ConfigurationError("Hybrid candidate budget must be an integer 1..20")
            validate_selection(selection, self.scope, corpus.index_digest, queries, top_k)
            self.top_k = top_k
            if (
                self.pointer.get("corpus_digest") != corpus.index_digest
                or self.pointer.get("dense_weight") != selection["dense_weight"]
            ):
                raise ConfigurationError("Hybrid pointer corpus/weight mismatch")
        if self.scope.signature != self.config.signature():
            raise ConfigurationError("Active collection embedding signature differs")
        self.mock = mock
        self.gateway: Any = None
        self.judge_gateway: Any = None
        self.event_index: MilvusEventIndex | None = None
        self.tools: ToolGateway | None = None
        self.ledger: MySQLCaseRepository | None = None

    @asynccontextmanager
    async def open(
        self, client: httpx.AsyncClient, trace: TraceSink
    ) -> AsyncIterator[Conversation]:
        async with AsyncExitStack() as stack:
            ledger = await MySQLCaseRepository.open(self.root)
            stack.push_async_callback(ledger.aclose)
            self.ledger = ledger
            cache = RedisMemory.open(self.root)
            stack.push_async_callback(cache.aclose)
            memory = MemoryService(ledger, cache)
            gateway = create_gateway(client, self.config, AsyncCalls(2, 30))
            self.gateway = gateway
            model = CountedModel(gateway, gateway)
            endpoint = EndpointConfig.model_validate_json(
                (self.root / "modules/deephelp-app/event-judge.example.json").read_text(
                    encoding="utf-8"
                )
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
            policy_path = (
                self.cascade_policy_path or Path(__file__).parent / "sample_data/m12_policy.json"
            )
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
            tools = ToolGateway(config=self.mock)
            self.tools = tools
            await stack.enter_async_context(tools.open())
            assembly = self

            class RequestDense:
                async def retrieve(
                    self, text: str, scope: DenseScope, budget: ExecutionBudget, *, top_k: int = 3
                ) -> DenseResult:
                    # The store receives the caller's budget, never the startup budget.
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
                from deephelp_app.fasttext_runtime import (
                    FastTextClassifier,
                    FastTextFallback,
                    pointer_manifest,
                )

                classifier = FastTextClassifier(pointer_manifest(self.fasttext_pointer))
                fallback = FastTextFallback(classifier, fallback)
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
            )
            yield Conversation(
                ledger,
                TextEntityProcessor(model, trace=trace),
                IntentService(
                    model,
                    CountedRetriever(RequestDense()),
                    assembly.scope,
                    top_k=assembly.top_k,
                    policy=policy,
                    fallback=fallback,
                ),
                SOPExecutor(model, TrackedTools(tools)),
                trace,
                versions,
                memory=memory,
                cases=ledger,
                events=events,
            )


def live_app(
    assembly: LiveAssembly,
    auth: LocalAuth,
    budget: BudgetSession,
    trace_path: str = ".local/m08/trace.jsonl",
) -> FastAPI:
    return create_app(
        Settings(
            mode="mvp",
            request_timeout=90,
            child_timeout=30,
            max_attempts=40,
            retry_limit=2,
            trace_path=trace_path,
        ),
        identity_provider=auth,
        conversation_factory=assembly.open,
        budget_factory=budget.request,
    )
