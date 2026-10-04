"""Actual loopback MCP SDK; synthetic backend content, no cloud or model calls."""

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from mcp.types import CallToolResult, TextContent

from deephelp_app.adapters.mcp_protocol import canonical
from deephelp_app.adapters.network_mcp import HTTPMCPConfig, ServiceCredentials
from deephelp_app.adapters.providers import ProviderConfig
from deephelp_app.adapters.retrieval_mcp import RemoteRetriever
from deephelp_app.adapters.retrieval_protocol import RetrievalBinding
from deephelp_app.application.corpus import read_corpus
from deephelp_app.application.hybrid import normalized_fusion, result_for
from deephelp_app.domain.errors import AppError
from deephelp_app.domain.execution import ExecutionBudget, request_execution
from deephelp_app.domain.models import (
    DenseCandidate,
    DenseHit,
    DenseResult,
    DenseScope,
    ErrorCode,
    HybridScope,
    RetrievalAction,
    RetrievalContext,
    RetrievalDelegation,
    RetrievalQuery,
    RetrievalWire,
    VerifiedIdentity,
)
from deephelp_services.retrieval import create_retrieval_app
from deephelp_tools.assets import asset_path
from deephelp_tools.evaluation.mvp_acceptance import api_client
from deephelp_tools.probes.mcp_smoke import synthetic_request

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def binding_assets():
    config = ProviderConfig.load(Path("modules/deephelp-app/providers.example.json"))
    corpus = read_corpus(asset_path("m09_corpus.jsonl"))
    scope = HybridScope(
        namespace="owned_reference",
        dataset_version="m09-corpus-v1",
        signature=config.signature(),
        index_kind="intent_hybrid",
    )
    return RetrievalBinding(scope, 3, 0.5, corpus.index_digest, corpus.index_records, config)


@pytest.fixture
def binding(binding_assets):
    # Records include mutable metadata; share loading, but isolate every consumer.
    return deepcopy(binding_assets)


class FakeBackend:
    def __init__(self, binding):
        self.binding = binding
        _, _, self.context = synthetic_request(ExecutionBudget.start(10, 1, 0))
        owner = self.context.identity
        self.subjects = {(owner.tenant_id, owner.user_id)}
        self.calls = 0
        self.wait = False
        self.unknown = False
        self.started, self.cancelled, self.release = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )

    async def execute(self, name, query, budget, actions):
        self.calls += 1
        self.started.set()
        if self.wait:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        budget.claim_attempt(retry=False, tokens=80, cost=Decimal("0.000640"))
        if self.unknown:
            actions.append(
                RetrievalAction(
                    action="embedding",
                    elapsed_ms=0,
                    attempts=1,
                    model_calls=1,
                    charged_tokens=80,
                    uncertain_attempts=1,
                )
            )
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "Synthetic provider unavailable")
        budget.settle_model(80, Decimal("0.000640"), 6, Decimal("0.000048"))
        actions.append(
            RetrievalAction(
                action="embedding",
                elapsed_ms=1,
                attempts=1,
                model_calls=1,
                tokens=6,
                charged_tokens=6,
            )
        )
        for action in ("milvus_validate", "dense", "bm25", "fusion"):
            budget.claim_attempt(retry=False)
            actions.append(RetrievalAction(action=action, elapsed_ms=1, attempts=1))
        record = self.binding.records[0]
        base = dict(
            doc_id=record.doc_id,
            intent_code=record.intent_code,
            content=record.content,
            metadata={},
            scope=self.binding.scope,
            rank=1,
        )
        dense = DenseHit(**base, raw_score=0.6, matched_sources=("dense",))
        sparse = DenseHit(**base, raw_score=2, score_kind="bm25", matched_sources=("bm25",))
        score = normalized_fusion(0.6, 2, self.binding.weight)
        fusion = DenseHit(
            **base,
            raw_score=score,
            score_kind="fusion",
            raw_dense=0.6,
            raw_sparse=2,
            fusion_score=score,
            matched_sources=("dense", "bm25"),
        )
        rows = {
            mode: result_for(self.binding.scope, mode, [hit], query.top_k, self.binding.weight, 2)
            for mode, hit in (("dense", dense), ("bm25", sparse), ("hybrid", fusion))
        }
        return rows if name == "compare_intents" else {"hybrid": rows["hybrid"]}


def application(backend, credentials_path):
    return create_retrieval_app(
        Path.cwd(),
        ServiceCredentials.load(credentials_path),
        providers=Path("unused"),
        pointer=Path("unused"),
        corpus=Path("unused"),
        auth=Path("unused"),
        backend=backend,
    )


def config(http, credentials_path):
    return HTTPMCPConfig(
        url=str(http.base_url).rstrip("/") + "/mcp",
        credentials_path=credentials_path,
        audience="retrieval",
    )


def budget(timeout=20, attempts=20):
    return ExecutionBudget.start(timeout, attempts, 0, token_limit=20000, cost_limit=Decimal("2"))


async def test_initialize_compare_schema_candidates_actions_and_shared_budget(
    binding, credentials_path
):
    backend = FakeBackend(binding)
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        remote = RemoteRetriever(config(http, credentials_path), binding)
        async with remote.open():
            token = request_execution.set(backend.context)
            running = budget()
            reservations = []
            running.on_change = lambda: reservations.append(running.usage())
            try:
                results = await remote.compare("优惠未到账", binding.scope, running)
            finally:
                request_execution.reset(token)
            assert set(results) == {"dense", "bm25", "hybrid"}
            assert results["hybrid"].candidates[0].score_kind == "fusion"
            assert running.attempts_used == 6 and running.tokens_used == 6
            assert running.token_upper_bound == 6 and running.uncertain_attempts == 0
            assert reservations[0].attempts == 6 and reservations[0].uncertain_attempts == 1
            receipt = remote.diagnostics[-1]["receipt"]
            assert [a["action"] for a in receipt["actions"]] == [
                "mcp",
                "embedding",
                "milvus_validate",
                "dense",
                "bm25",
                "fusion",
            ]
            assert remote.diagnostics[-1]["client_validation"] == "PASS"
            assert all(
                h == {"initialized": True, "protocol_version": "2025-11-25"}
                for h in remote.network.handshakes
            )
            assert backend.calls == 1


@pytest.mark.parametrize(
    "change", ["identity", "scope", "extra", "profile", "audience", "query", "allowance"]
)
async def test_service_refuses_untrusted_scope_subject_arguments_and_budget(
    binding, credentials_path, change
):
    backend = FakeBackend(binding)
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        remote = RemoteRetriever(config(http, credentials_path), binding)
        async with remote.open():
            query = RetrievalQuery(query="优惠未到账", scope_id=binding.scope.fingerprint)
            ctx = RetrievalContext.from_request(backend.context)
            cap = binding.allowance(query.query, 20)
            if change == "identity":
                ctx = ctx.model_copy(
                    update={"identity": VerifiedIdentity(tenant_id="other", user_id="b")}
                )
            if change == "allowance":
                cap = cap.model_copy(update={"attempts": 16})
            delegation = RetrievalDelegation(
                context=ctx,
                name="retrieve_intents",
                query_hash=hashlib.sha256(canonical(query.model_dump(mode="json"))).hexdigest(),
                profile_id="0" * 64 if change == "profile" else binding.profile_id,
                allowance=cap,
            )
            args = query.model_dump(mode="json")
            if change == "scope":
                args["scope_id"] = "0" * 64
            if change == "extra":
                args["collection"] = "private_memory"
            if change == "query":
                args["query"] = "篡改查询"
            meta = remote.credentials.protect(
                "business" if change == "audience" else "retrieval",
                {
                    "action": "retrieve",
                    "nonce": uuid4().hex,
                    "delegation": delegation.model_dump(mode="json"),
                },
            )
            result = await remote.network.call_tool("retrieve_intents", args, meta=meta)
            assert result.is_error and backend.calls == 0


async def test_cancellation_reaches_backend_retains_reservation_and_new_call_recovers(
    binding, credentials_path
):
    backend = FakeBackend(binding)
    backend.wait = True
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        async with RemoteRetriever(config(http, credentials_path), binding).open() as remote:
            token = request_execution.set(backend.context)
            try:
                running = budget(timeout=0.35)
                with pytest.raises(AppError) as error:
                    await remote.retrieve("优惠未到账", binding.scope, running)
                assert error.value.code == ErrorCode.TIMEOUT
                await asyncio.wait_for(backend.cancelled.wait(), 2)
                assert running.uncertain_attempts == 1 and running.attempts_used == 6
                assert running.token_upper_bound > 6
                backend.wait = False
                result = await remote.retrieve("新调用", binding.scope, budget())
                assert result.candidates and backend.calls == 2
            finally:
                request_execution.reset(token)


async def test_atomic_shared_reservation_prevents_concurrent_overspend(binding, credentials_path):
    backend = FakeBackend(binding)
    backend.wait = True
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        async with RemoteRetriever(config(http, credentials_path), binding).open() as remote:
            running = budget(attempts=10)
            token = request_execution.set(backend.context)
            try:
                first = asyncio.create_task(remote.retrieve("首条", binding.scope, running))
                await asyncio.wait_for(backend.started.wait(), 2)
                with pytest.raises(AppError) as error:
                    await remote.retrieve("并发第二条", binding.scope, running)
                assert error.value.code == ErrorCode.BUDGET_EXHAUSTED
                backend.release.set()
                await first
                assert backend.calls == 1 and running.attempts_used == 6
            finally:
                backend.release.set()
                request_execution.reset(token)


async def test_valid_error_receipt_accounts_unknown_provider_usage(binding, credentials_path):
    backend = FakeBackend(binding)
    backend.unknown = True
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        async with RemoteRetriever(config(http, credentials_path), binding).open() as remote:
            running = budget()
            token = request_execution.set(backend.context)
            try:
                with pytest.raises(AppError) as error:
                    await remote.retrieve("未知用量", binding.scope, running)
                assert error.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
                assert running.attempts_used == 2 and running.uncertain_attempts == 1
                assert running.tokens_used == 0 and running.token_upper_bound == 80
                assert remote.diagnostics[-1]["model_calls"] == 1
            finally:
                request_execution.reset(token)


@pytest.mark.parametrize("change", ["owner", "score", "usage", "proof"])
async def test_caller_rejects_forged_or_semantically_invalid_receipts(
    binding, credentials_path, change
):
    backend = FakeBackend(binding)
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        async with RemoteRetriever(config(http, credentials_path), binding).open() as remote:
            original = remote.network.call_tool

            async def corrupted(*args, **kwargs):
                result = await original(*args, **kwargs)
                wire = RetrievalWire.model_validate(result.structured_content)
                data = wire.model_dump(mode="json")
                if change == "owner":
                    data["reply"]["delegation"]["context"]["identity"]["user_id"] = "b"
                if change == "score":
                    data["reply"]["results"]["hybrid"]["hits"][0]["raw_dense"] = -0.9
                if change == "usage":
                    data["reply"]["used"]["attempts"] = 100
                if change != "proof":
                    data["proof"] = dict(
                        remote.credentials.protect(
                            "retrieval-receipt",
                            {
                                "action": "receipt",
                                "nonce": data["reply"]["call_id"],
                                "reply_hash": hashlib.sha256(canonical(data["reply"])).hexdigest(),
                            },
                        )
                    )
                else:
                    data["proof"]["deephelp/internal"]["signature"] = "0" * 64
                return CallToolResult(
                    structured_content=data, content=[TextContent(text=canonical(data).decode())]
                )

            remote.network.call_tool = corrupted
            running = budget()
            token = request_execution.set(backend.context)
            try:
                with pytest.raises(AppError):
                    await remote.retrieve("优惠未到账", binding.scope, running)
                assert running.uncertain_attempts == 1 and running.token_upper_bound > 6
                assert remote.diagnostics[-1]["status"] == "unknown"
            finally:
                request_execution.reset(token)


async def test_schema_drift_and_profile_drift_fail_at_startup(binding, credentials_path):
    backend = FakeBackend(binding)
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        remote = RemoteRetriever(config(http, credentials_path), replace(binding, weight=0.2))
        with pytest.raises(AppError) as error:
            async with remote.open():
                pass
        assert error.value.code == ErrorCode.VERSION_CONFLICT
        remote = RemoteRetriever(config(http, credentials_path), binding)
        original = remote.network.list_tools

        async def drift():
            tools = await original()
            tools.tools[0].input_schema["properties"]["unsafe_sql"] = {"type": "string"}
            return tools

        remote.network.list_tools = drift
        with pytest.raises(AppError) as error:
            async with remote.open():
                pass
        assert error.value.code == ErrorCode.MODEL_CAPABILITY_UNAVAILABLE and backend.calls == 0


async def test_server_rejects_wrong_record_provenance(binding, credentials_path):
    backend = FakeBackend(binding)
    original = backend.execute

    async def wrong(*args):
        results = await original(*args)
        first = results["hybrid"].hits[0].model_copy(update={"doc_id": "not_published"})
        results["hybrid"] = results["hybrid"].model_copy(update={"hits": (first,)})
        return results

    backend.execute = wrong
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        async with RemoteRetriever(config(http, credentials_path), binding).open() as remote:
            running = budget()
            token = request_execution.set(backend.context)
            try:
                with pytest.raises(AppError) as error:
                    await remote.retrieve("优惠未到账", binding.scope, running)
                assert error.value.code == ErrorCode.MODEL_OUTPUT_INVALID
                assert running.uncertain_attempts == 0 and running.tokens_used == 6
            finally:
                request_execution.reset(token)


async def test_original_dense_port_preserves_per_intent_evidence_outside_top_hits(
    binding, credentials_path
):
    scope = DenseScope(
        namespace="owned_dense", dataset_version="dense-v1", signature=binding.scope.signature
    )
    binding = replace(binding, scope=scope)
    backend = FakeBackend(binding)
    unique = {r.intent_code: r for r in binding.records}
    records = sorted(unique.values(), key=lambda r: r.intent_code.value)

    async def dense(name, query, running, actions):
        if name == "compare_intents":
            raise AppError(ErrorCode.VERSION_CONFLICT, "No native BM25")
        running.claim_attempt(retry=False, tokens=80, cost=Decimal("0.000640"))
        running.settle_model(80, Decimal("0.000640"), 6, Decimal("0.000048"))
        actions.append(
            RetrievalAction(
                action="embedding",
                elapsed_ms=0,
                attempts=1,
                model_calls=1,
                tokens=6,
                charged_tokens=6,
            )
        )
        candidates = tuple(
            DenseCandidate(
                intent_code=r.intent_code,
                raw_score=0.9 - i * 0.1,
                rank=i + 1,
                evidence_doc_id=r.doc_id,
            )
            for i, r in enumerate(records)
        )
        first = records[0]
        hit = DenseHit(
            doc_id=first.doc_id,
            intent_code=first.intent_code,
            content=first.content,
            raw_score=0.9,
            metadata={},
            scope=scope,
        )
        return {"dense": DenseResult(scope=scope, hits=(hit,), candidates=candidates)}

    backend.execute = dense
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        async with RemoteRetriever(config(http, credentials_path), binding).open() as remote:
            token = request_execution.set(backend.context)
            try:
                running = budget()
                result = await remote.retrieve("订单优惠", scope, running, top_k=1)
                assert len(result.hits) == 1 and len(result.candidates) == 3
                assert result.policy_version == "dense-baseline-v1"
                assert all(c.score_kind == "cosine" for c in result.candidates)
                assert running.attempts_used == 2 and running.uncertain_attempts == 0
                with pytest.raises(AppError) as error:
                    await remote.compare("订单优惠", scope, budget())
                assert error.value.code == ErrorCode.VERSION_CONFLICT
            finally:
                request_execution.reset(token)


async def test_disconnect_keeps_reservation_and_never_falls_back_to_local(
    binding, credentials_path
):
    backend = FakeBackend(binding)
    app = application(backend, credentials_path)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        remote = RemoteRetriever(config(http, credentials_path), binding)
        lifespan = remote.open()
        await lifespan.__aenter__()
    try:
        token = request_execution.set(backend.context)
        running = budget()
        try:
            with pytest.raises(AppError) as error:
                await remote.retrieve("断连", binding.scope, running)
            assert error.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
            assert backend.calls == 0 and running.uncertain_attempts == 1
        finally:
            request_execution.reset(token)
    finally:
        await lifespan.__aexit__(None, None, None)
