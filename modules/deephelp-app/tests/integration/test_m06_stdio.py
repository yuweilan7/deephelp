import asyncio
import copy
import json
from uuid import uuid4

import pytest
from mcp.shared.exceptions import MCPError

from deephelp_app.adapters.mcp_protocol import LEDGER_URI, sign_metadata
from deephelp_app.adapters.tool_gateway import process_alive
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import ErrorCode, ToolInvocationEnvelope, ToolStatus
from deephelp_tools.demo.assembly import DemoToolGateway as ToolGateway
from deephelp_tools.demo.tool_config import FaultSpec, MockConfig
from deephelp_tools.probes.mcp_smoke import accept, invoke, synthetic_request, wait_record

pytestmark = pytest.mark.integration


async def test_full_real_stdio_discovery_content_faults_cancellation_and_cleanup():
    report = await accept("feature")
    assert report["status"] == "PASS"
    assert report["process_alive_after_close"] is False
    assert len(report["ledger"]) == 15
    assert report["budget_used"]["attempts"] == 15
    assert report["budget_used"]["tool_steps"] == 15


async def test_unsigned_tampered_replayed_wrong_arguments_and_write_tool_rejected():
    budget = ExecutionBudget.start(30, 8, 0)
    async with ToolGateway().open() as gateway:
        client = gateway._connected()
        no_auth = await client.call_tool("get_order_benefits", {"order_id": "000031"})
        assert no_auth.is_error and no_auth.structured_content["error"]["code"] == "FORBIDDEN"
        req, _, ctx = synthetic_request(budget)

        def metadata():
            call_id = "call-" + uuid4().hex
            envelope = ToolInvocationEnvelope(
                request=req, request_id=ctx.request_id, trace_id=ctx.trace_id, call_id=call_id
            )
            return sign_metadata(
                gateway._secret,
                {
                    "action": "call_tool",
                    "nonce": call_id,
                    "envelope": envelope.model_dump(mode="json"),
                },
            )

        valid = metadata()
        good = await client.call_tool("get_order_benefits", {"order_id": "000031"}, meta=valid)
        assert not good.is_error
        repeated = await client.call_tool("get_order_benefits", {"order_id": "000031"}, meta=valid)
        assert repeated.is_error and repeated.structured_content["error"]["code"] == "FORBIDDEN"
        reused_call = copy.deepcopy(valid["deephelp/internal"]["payload"])
        reused_call["nonce"] = uuid4().hex
        collision = await client.call_tool(
            "get_order_benefits",
            {"order_id": "000031"},
            meta=sign_metadata(gateway._secret, reused_call),
        )
        assert collision.is_error and collision.structured_content["error"]["code"] == "FORBIDDEN"
        tampered = copy.deepcopy(metadata())
        tampered["deephelp/internal"]["payload"]["envelope"]["request"]["identity"]["user_id"] = "b"
        for name, args, meta, code in [
            ("get_order_benefits", {"order_id": "000031"}, tampered, "FORBIDDEN"),
            ("get_order_benefits", {"order_id": "DEMO-D05"}, metadata(), "FORBIDDEN"),
            (
                "get_order_benefits",
                {"order_id": "000031", "user_id": "b"},
                metadata(),
                "INVALID_ARGUMENT",
            ),
            ("refund_order", {"order_id": "000031"}, metadata(), "INVALID_ARGUMENT"),
        ]:
            wire = await client.call_tool(name, args, meta=meta)
            assert wire.is_error and wire.structured_content["error"]["code"] == code
        with pytest.raises(MCPError):
            await client.read_resource(LEDGER_URI)
        records = await gateway.ledger()
        assert len(records) == 1 and records[0].status == "succeeded"
        assert not any("signature" in str(r.model_dump()) for r in records)


async def test_shared_budget_last_reservation_and_ledger_capacity():
    async with ToolGateway(config=MockConfig(max_invocations=2)).open() as gateway:
        budget = ExecutionBudget.start(20, 1, 0)
        results = await asyncio.gather(invoke(gateway, budget), invoke(gateway, budget))
        assert sum(r.status == ToolStatus.SUCCEEDED for r in results) == 1
        assert (
            sum(r.error is not None and r.error.code == ErrorCode.BUDGET_EXHAUSTED for r in results)
            == 1
        )
        assert budget.attempts_used == 1 and len(await gateway.ledger()) == 1
        assert budget.tool_steps_used == 1
        remaining = ExecutionBudget.start(20, 2, 0)
        assert (await invoke(gateway, remaining)).status == ToolStatus.SUCCEEDED
        exhausted = await invoke(gateway, remaining)
        assert exhausted.error.code == ErrorCode.BUDGET_EXHAUSTED
        assert len(await gateway.ledger()) == 2


async def test_two_tenants_and_coupon_cross_user_are_checked_on_server():
    budget = ExecutionBudget.start(20, 8, 0)
    async with ToolGateway().open() as gateway:
        results = await asyncio.gather(
            invoke(gateway, budget, tenant="foreign-tenant"),
            invoke(gateway, budget, order_id="DEMO-C05", coupon_id="COUPON-C05"),
            invoke(gateway, budget, order_id="000042", coupon_id="COUPON-C05"),
            invoke(
                gateway,
                budget,
                order_id="DEMO-C05",
                coupon_id="COUPON-C05",
                user="synthetic-user-b",
            ),
        )
        assert all(r.error.code == ErrorCode.FORBIDDEN and not r.facts for r in results[:3])
        assert results[3].status == ToolStatus.SUCCEEDED
        assert {f.name: f.value for f in results[3].facts}["coupon_status"] == "expired"
        assert len(await gateway.ledger()) == 4


async def test_queued_request_deadline_cancels_without_dispatching_second_call():
    config = MockConfig(faults={"000031": FaultSpec(delay_seconds=1)})
    async with ToolGateway(config=config, concurrency=1, child_timeout=5).open() as gateway:
        first_budget = ExecutionBudget.start(10, 1, 0)
        req, question, ctx = synthetic_request(first_budget)
        task = asyncio.create_task(gateway.execute(req, question, first_budget, context=ctx))
        await wait_record(gateway, req.operation_id, "started")
        short_budget = ExecutionBudget.start(0.05, 1, 0)
        queued = await invoke(gateway, short_budget)
        assert queued.error.code == ErrorCode.BUDGET_EXHAUSTED
        assert short_budget.attempts_used == 0 and queued.call_id is None
        assert (await task).status == ToolStatus.SUCCEEDED
        assert len(await gateway.ledger()) == 1


async def test_cancel_gateway_lifespan_reaps_child_and_records_inflight_cancel(tmp_path):
    entered = asyncio.Event()
    ledger_path = tmp_path / "cancelled.json"
    gateway = ToolGateway(
        config=MockConfig(faults={"000031": FaultSpec(delay_seconds=10)}),
        child_timeout=15,
        ledger_path=ledger_path,
    )

    async def run():
        async with gateway.open():
            entered.set()
            budget = ExecutionBudget.start(30, 1, 0)
            await invoke(gateway, budget)

    task = asyncio.create_task(run())
    await entered.wait()
    # Use the server's persisted record as a synchronization point; no request after close.
    async with asyncio.timeout(5):
        while True:
            if json.loads(ledger_path.read_text())["records"]:
                break
            await asyncio.sleep(0.025)
    pid = gateway.pid
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert pid is not None and not process_alive(pid)
    assert json.loads(ledger_path.read_text())["records"][0]["status"] == "cancelled"


async def test_exception_in_caller_preserves_type_and_reaps_process():
    gateway = ToolGateway()
    with pytest.raises(ValueError, match="synthetic caller failure"):
        async with gateway.open():
            raise ValueError("synthetic caller failure")
    assert gateway.pid is not None and not process_alive(gateway.pid)


async def test_gateway_rejects_second_lifespan_without_spawning():
    gateway = ToolGateway()
    async with gateway.open():
        assert gateway.pid is not None
    with pytest.raises(ValueError, match="single-use"):
        async with gateway.open():
            pass
    assert not process_alive(gateway.pid)


async def test_startup_handshake_timeout_reaps_unresponsive_child(tmp_path):
    import sys

    from mcp import StdioServerParameters

    import deephelp_app.adapters.tool_gateway as module

    pid_path = tmp_path / "stalled.pid"
    code = (
        "import os,time,pathlib; pathlib.Path("
        + repr(str(pid_path))
        + ").write_text(str(os.getpid())); time.sleep(30)"
    )
    gateway = module.ToolGateway(
        server=StdioServerParameters(command=sys.executable, args=["-c", code]),
        startup_timeout=0.5,
    )
    async with asyncio.timeout(10):
        with pytest.raises(TimeoutError):
            async with gateway.open():
                raise AssertionError("Unresponsive server became ready")
    pid = int(pid_path.read_text())
    assert not process_alive(pid)


async def test_startup_registry_failure_reaps_child(tmp_path, monkeypatch):
    from deephelp_app.domain.errors import AppError

    ledger_path = tmp_path / "startup.json"
    gateway = ToolGateway(ledger_path=ledger_path)

    async def reject():
        raise AppError(ErrorCode.MODEL_CAPABILITY_UNAVAILABLE, "Synthetic schema mismatch")

    monkeypatch.setattr(gateway, "list_tools", reject)
    with pytest.raises(AppError):
        async with gateway.open():
            raise AssertionError("Invalid schema became ready")
    assert not process_alive(json.loads(ledger_path.read_text())["pid"])
