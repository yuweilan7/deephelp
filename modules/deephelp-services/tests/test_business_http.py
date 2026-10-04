"""Real loopback SDK HTTP protocol with synthetic content; no remote DB or model calls."""

import asyncio
import hashlib
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from deephelp_app.adapters.business_client import BusinessClient
from deephelp_app.adapters.mcp_protocol import canonical, sign_metadata
from deephelp_app.adapters.network_mcp import (
    DelegationVerifier,
    HTTPMCPConfig,
    ServiceCredentials,
    encode_delegation,
)
from deephelp_app.adapters.tool_gateway import ToolGateway
from deephelp_app.api.business import business_router
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import (
    BusinessActivityInput,
    BusinessCouponInput,
    BusinessOrderInput,
    BusinessRecord,
    ErrorCode,
    ToolInvocationEnvelope,
    ToolStatus,
    VerifiedIdentity,
)
from deephelp_services.business import create_business_app
from deephelp_services.business_store import BusinessRepository
from deephelp_tools.demo.mcp_server import MockBackend
from deephelp_tools.demo.tool_config import MockConfig
from deephelp_tools.evaluation.mvp_acceptance import api_client
from deephelp_tools.probes.mcp_smoke import synthetic_request

pytestmark = pytest.mark.integration


class ProtocolRepository:
    def __init__(self):
        self.backend = MockBackend("unused", MockConfig())
        self.calls = 0
        self.delay = 0
        self.cancelled = asyncio.Event()
        self.mutate = None
        self.put = AsyncMock(side_effect=self.record)

    @staticmethod
    async def record(kind, identity, data, expected_version=None):
        return BusinessRecord(identity=identity, version=1, data=data)

    async def query(self, envelope):
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
            result = await self.backend.query(envelope)
            result = result.model_copy(
                update={
                    "evidence_refs": tuple(
                        ref.model_copy(update={"version": "business-mysql-v1"})
                        for ref in result.evidence_refs
                    )
                }
            )
            return self.mutate(result) if self.mutate else result
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


async def test_real_http_initialize_discovery_call_both_validators_and_one_budget(credentials_path):
    credentials = ServiceCredentials.load(credentials_path)
    repo = ProtocolRepository()
    app = create_business_app(Path.cwd(), credentials, repository=repo)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        config = HTTPMCPConfig(
            url=str(http.base_url).rstrip("/") + "/mcp", credentials_path=credentials_path
        )
        async with ToolGateway(http=config).open() as gateway:
            assert len(await gateway.list_tools()) == 2
            assert all(
                row == {"initialized": True, "protocol_version": "2025-11-25"}
                for row in gateway._connected().handshakes
            )
            budget = ExecutionBudget.start(20, 1, 0)
            req, question, context = synthetic_request(budget)
            result = await gateway.execute(req, question, budget, context=context)
            assert result.status == ToolStatus.SUCCEEDED
            assert result.evidence_refs[0].version == "business-mysql-v1"
            assert next(f.value.amount for f in result.facts if f.name == "paid") == Decimal(
                "99.90"
            )
            assert repo.calls == budget.tool_steps_used == budget.attempts_used == 1
            denied = await gateway.execute(req, question, budget, context=context)
            assert denied.error.code == ErrorCode.BUDGET_EXHAUSTED
            assert repo.calls == 1


async def test_network_auth_origin_delegate_arguments_replay_and_unknown_tools(credentials_path):
    credentials = ServiceCredentials.load(credentials_path)
    repo = ProtocolRepository()
    app = create_business_app(Path.cwd(), credentials, repository=repo)
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        assert (await http.post("/mcp", json={})).status_code == 401
        assert (
            await http.post(
                "/mcp",
                json={},
                headers={"Authorization": "Bearer " + "t" * 64, "Origin": "https://evil.invalid"},
            )
        ).status_code == 403
        gateway = ToolGateway(
            http=HTTPMCPConfig(
                url=str(http.base_url).rstrip("/") + "/mcp", credentials_path=credentials_path
            )
        )
        async with gateway.open():
            client = gateway._connected()
            req, _, ctx = synthetic_request(ExecutionBudget.start(20, 8, 0))
            call = "call-" + uuid4().hex
            envelope = ToolInvocationEnvelope(
                request=req, request_id=ctx.request_id, trace_id=ctx.trace_id, call_id=call
            )
            meta = credentials.protect(
                "business",
                {
                    "action": "call_tool",
                    "nonce": call,
                    "envelope": envelope.model_dump(mode="json"),
                },
            )
            unsigned = await client.call_tool("get_order_benefits", {"order_id": "000031"}, meta={})
            assert unsigned.is_error
            wrong = await client.call_tool("get_order_benefits", {"order_id": "000032"}, meta=meta)
            assert wrong.is_error and repo.calls == 0
            call2 = "call-" + uuid4().hex
            envelope = envelope.model_copy(update={"call_id": call2})
            meta2 = credentials.protect(
                "business",
                {
                    "action": "call_tool",
                    "nonce": call2,
                    "envelope": envelope.model_dump(mode="json"),
                },
            )
            result = await client.call_tool(
                "get_order_benefits", {"order_id": "000031"}, meta=meta2
            )
            assert not result.is_error
            replay = await client.call_tool(
                "get_order_benefits", {"order_id": "000031"}, meta=meta2
            )
            assert replay.is_error and repo.calls == 1
            bad = await client.call_tool(
                "get_order_benefits", {"order_id": "000031", "identity": {}}, meta=meta2
            )
            assert bad.is_error and repo.calls == 1
            bad = await client.call_tool("execute_sql", {"sql": "select 1"}, meta=meta2)
            assert bad.is_error and repo.calls == 1


async def test_http_timeout_sends_cancellation_and_later_new_call_recovers(credentials_path):
    repo = ProtocolRepository()
    app = create_business_app(
        Path.cwd(), ServiceCredentials.load(credentials_path), repository=repo
    )
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        async with ToolGateway(
            http=HTTPMCPConfig(
                url=str(http.base_url).rstrip("/") + "/mcp", credentials_path=credentials_path
            ),
            child_timeout=0.3,
        ).open() as gateway:
            repo.delay = 5
            budget = ExecutionBudget.start(10, 3, 0)
            req, question, ctx = synthetic_request(budget)
            result = await gateway.execute(req, question, budget, context=ctx)
            assert result.error.code == ErrorCode.TIMEOUT
            await asyncio.wait_for(repo.cancelled.wait(), 2)
            repo.delay = 0
            result = await gateway.execute(req, question, budget, context=ctx)
            assert result.status == ToolStatus.SUCCEEDED
            assert budget.attempts_used == 2


async def test_maintenance_proxy_injects_identity_and_body_forgery_never_reaches_store(
    credentials_path,
):
    repo = ProtocolRepository()
    credentials = ServiceCredentials.load(credentials_path)
    app = create_business_app(Path.cwd(), credentials, repository=repo)
    owner = VerifiedIdentity(tenant_id="personal", user_id="a")
    data = {
        "order_id": "NEW",
        "paid": {"amount": "123.45"},
        "discount": {"amount": "0"},
        "discount_status": "missing",
        "sku_ids": ["SKU"],
    }
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        proxy = FastAPI()
        proxy.include_router(
            business_router(
                BusinessClient(
                    HTTPMCPConfig(
                        url=str(http.base_url).rstrip("/") + "/mcp",
                        credentials_path=credentials_path,
                    )
                ),
                lambda request: owner,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(proxy), base_url="http://local"
        ) as client:
            result = await client.post("/business/orders", json=data)
            assert result.status_code == 200 and result.json()["identity"] == owner.model_dump()
            forged = await client.post(
                "/business/orders", json={**data, "identity": {"user_id": "b"}}
            )
            assert forged.status_code == 422 and repo.put.call_count == 1
        payload = {
            "action": "maintain",
            "nonce": uuid4().hex,
            "identity": owner.model_dump(),
            "method": "POST",
            "target": "/records/orders",
            "body_hash": hashlib.sha256(canonical(data)).hexdigest(),
        }
        headers = {
            "Authorization": "Bearer " + "t" * 64,
            "X-Deephelp-Delegation": encode_delegation(credentials.protect("business", payload)),
        }
        tampered = await http.post(
            "/records/orders", headers=headers, json={**data, "order_id": "CHANGED"}
        )
        assert tampered.status_code == 403 and repo.put.call_count == 1


@pytest.mark.parametrize("amount", [1.5, "1.001", "-1", "NaN", "1000000000000000000"])
def test_business_money_is_decimal_precise_and_sql_bounded(amount):
    with pytest.raises(ValidationError):
        BusinessOrderInput(
            order_id="NEW",
            paid={"amount": amount},
            discount={"amount": "0"},
            discount_status="missing",
            sku_ids=("SKU",),
        )


@pytest.mark.parametrize(
    "model,id_field", [(BusinessActivityInput, "activity_id"), (BusinessCouponInput, "coupon_id")]
)
def test_invalid_period_rejected(model, id_field):
    body = {
        id_field: "X",
        "valid_from": "2026-10-04T00:00:00Z",
        "valid_until": "2026-10-03T00:00:00Z",
    }
    body.update(
        {"label": "活动", "rules": "合成规则"}
        if id_field == "activity_id"
        else {"order_id": "X", "status": "usable", "minimum_spend": {"amount": "10"}}
    )
    with pytest.raises(ValidationError):
        model.model_validate(body)


@pytest.mark.parametrize("change", ["signature", "audience", "expiry", "nonce"])
def test_delegation_is_audience_expiry_signature_and_replay_bound(credentials_path, change):
    credentials = ServiceCredentials.load(credentials_path)
    verifier = DelegationVerifier(credentials, "business")
    payload = {
        "action": "test",
        "nonce": "N",
        "audience": "business",
        "expires_at": time.time() + 30,
    }
    if change == "audience":
        payload["audience"] = "retrieval"
    if change == "expiry":
        payload["expires_at"] = time.time() - 1
    if change == "nonce":
        payload["nonce"] = ""
    meta = sign_metadata("wrong" if change == "signature" else "k" * 64, payload)
    with pytest.raises(AppError):
        verifier.verify(meta)


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.invalid/mcp",
        "file:///mcp",
        "http://u:p@localhost/mcp",
        "http://localhost/demo",
        "http://localhost/mcp?token=x",
    ],
)
def test_unknown_or_unprotected_network_mode_fails_before_connect(credentials_path, url):
    with pytest.raises(ConfigurationError):
        ToolGateway(http=HTTPMCPConfig(url=url, credentials_path=credentials_path))


async def test_failed_database_transaction_rolls_back_and_does_not_publish():
    class Resource:
        async def __aenter__(self):
            return self.value

        async def __aexit__(self, *args):
            pass

    cursor = Resource()
    cursor.value = AsyncMock()
    conn = AsyncMock()
    conn.cursor = lambda: cursor
    acquired = Resource()
    acquired.value = conn
    connection = type(
        "Connection", (), {"pool": type("Pool", (), {"acquire": lambda self: acquired})()}
    )()
    store = BusinessRepository(connection)
    with pytest.raises(AppError):
        async with store.transaction():
            raise AppError(ErrorCode.VERSION_CONFLICT, "stale")
    assert conn.rollback.await_count == 2
    conn.commit.assert_not_awaited()


async def test_forged_fact_hash_rejected_on_service_side(credentials_path):
    repo = ProtocolRepository()
    repo.mutate = lambda result: result.model_copy(
        update={
            "evidence_refs": tuple(
                ref.model_copy(update={"content_hash": "0" * 64}) for ref in result.evidence_refs
            )
        }
    )
    app = create_business_app(
        Path.cwd(), ServiceCredentials.load(credentials_path), repository=repo
    )
    async with app.router.lifespan_context(app), api_client(app, http=True) as http:
        async with ToolGateway(
            http=HTTPMCPConfig(
                url=str(http.base_url).rstrip("/") + "/mcp", credentials_path=credentials_path
            )
        ).open() as gateway:
            budget = ExecutionBudget.start(10, 2, 0)
            req, question, ctx = synthetic_request(budget)
            result = await gateway.execute(req, question, budget, context=ctx)
            assert result.status == ToolStatus.FAILED and not result.facts


async def test_disconnected_http_tool_is_classified_without_facts_or_demo_fallback(
    credentials_path,
):
    repo = ProtocolRepository()
    app = create_business_app(
        Path.cwd(), ServiceCredentials.load(credentials_path), repository=repo
    )
    async with app.router.lifespan_context(app):
        serving = api_client(app, http=True)
        http = await serving.__aenter__()
        async with ToolGateway(
            http=HTTPMCPConfig(
                url=str(http.base_url).rstrip("/") + "/mcp",
                credentials_path=credentials_path,
            ),
            child_timeout=2,
        ).open() as gateway:
            await serving.__aexit__(None, None, None)
            budget = ExecutionBudget.start(5, 2, 0)
            req, question, ctx = synthetic_request(budget)
            result = await gateway.execute(req, question, budget, context=ctx)
            assert result.status == ToolStatus.FAILED
            assert result.error.code in {ErrorCode.UPSTREAM_UNAVAILABLE, ErrorCode.TIMEOUT}
            assert result.facts == () and repo.calls == 0
