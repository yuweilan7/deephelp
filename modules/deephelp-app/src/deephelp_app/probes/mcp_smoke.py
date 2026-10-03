"""Standalone real stdio acceptance. Uses only M02 synthetic data, no model/cloud calls."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.demo.tool_config import MockConfig
from deephelp_app.domain.models import (
    Entity,
    EntityName,
    EntitySource,
    IntentCode,
    Money,
    Question,
    QuestionStatus,
    RequestEnvelope,
    ToolName,
    ToolParameters,
    ToolRequest,
    ToolResult,
    ToolStatus,
    VerifiedIdentity,
    VersionManifest,
    tool_parameters_hash,
)
from deephelp_app.execution import ExecutionBudget
from deephelp_app.mcp_protocol import TOOL_VERSION, registered_tools
from deephelp_app.tool_gateway import ToolGateway, process_alive


def synthetic_request(
    budget: ExecutionBudget,
    *,
    order_id: str = "000031",
    coupon_id: str | None = None,
    user: str = "synthetic-user-a",
    tenant: str = "synthetic-tenant",
) -> tuple[ToolRequest, Question, RequestEnvelope]:
    """A trusted synthetic caller for demonstrations and tests, not an authentication service."""
    uid = uuid4().hex
    now = datetime.now(UTC)
    identity = VerifiedIdentity(tenant_id=tenant, user_id=user)
    context = RequestEnvelope(
        channel="m06-smoke",
        session_id="session-" + uid,
        message_id="message-" + uid,
        raw_text=f"合成查询 {order_id} {coupon_id or ''}",
        occurred_at=now,
        received_at=now,
        identity=identity,
        request_id="request-" + uid,
        trace_id="trace-" + uid,
    )
    entities = [
        Entity(
            name=EntityName.ORDER_ID,
            value=order_id,
            source=EntitySource(message_id=context.message_id, excerpt=order_id),
        )
    ]
    if coupon_id is not None:
        entities.append(
            Entity(
                name=EntityName.COUPON_ID,
                value=coupon_id,
                source=EntitySource(message_id=context.message_id, excerpt=coupon_id),
            )
        )
    question = Question(
        question_id="question-" + uid,
        identity=identity,
        session_id=context.session_id,
        status=QuestionStatus.ACTIVE,
        version=1,
        entities=tuple(entities),
        member_message_ids=(context.message_id,),
        active_intent=IntentCode.COUPON_UNUSABLE if coupon_id else IntentCode.DISCOUNT_MISSING,
        versions=VersionManifest(
            registry="complaints-v1",
            sop="coupon-unusable-v1" if coupon_id else "discount-missing-v1",
        ),
        created_at=now,
        updated_at=now,
    )
    parameters = ToolParameters(order_id=order_id, coupon_id=coupon_id)
    request = ToolRequest(
        operation_id="operation-" + uid,
        run_id="run-" + uid,
        question_id=question.question_id,
        identity=identity,
        tool_name=ToolName.CHECK_COUPON if coupon_id else ToolName.GET_ORDER_BENEFITS,
        tool_version=TOOL_VERSION,
        parameters=parameters,
        parameters_hash=tool_parameters_hash(parameters),
        budget=budget.snapshot(),
    )
    return request, question, context


def load_fault_config() -> MockConfig:
    data = json.loads(
        files("deephelp_app")
        .joinpath("assets/learning/mcp-faults.json")
        .read_text(encoding="utf-8")
    )
    if data["synthetic"] is not True or data["version"] != "mcp-faults-v1":
        raise ValueError("Unknown fault fixture")
    return MockConfig(faults=data["faults"])


async def invoke(gateway: ToolGateway, budget: ExecutionBudget, **kwargs: Any) -> ToolResult:
    request, question, context = synthetic_request(budget, **kwargs)
    return await gateway.execute(request, question, budget, context=context)


async def wait_record(
    gateway: ToolGateway, operation_id: str, status: str, *, wait_seconds: float = 3
) -> None:
    async with asyncio.timeout(wait_seconds):
        while True:
            if any(
                r.operation_id == operation_id and r.status == status
                for r in await gateway.ledger()
            ):
                return
            await asyncio.sleep(0.025)


async def accept(stage: str, *, run_seconds: float = 120, max_calls: int = 48) -> dict[str, Any]:
    checks: list[str] = []
    budget = ExecutionBudget.start(run_seconds, max_calls, 0)
    gateway = ToolGateway(
        config=load_fault_config() if stage == "feature" else MockConfig(),
        child_timeout=0.4 if stage == "feature" else 5,
    )
    async with asyncio.timeout(run_seconds):
        async with gateway.open():
            tools = await gateway.list_tools()
            assert {t.name for t in tools} == {t.name for t in registered_tools()}
            checks.append("real_list_tools_schemas")
            order = await invoke(gateway, budget)
            assert order.status == ToolStatus.SUCCEEDED
            values = {f.name: f.value for f in order.facts}
            assert isinstance(values["paid"], Money) and isinstance(values["discount"], Money)
            assert (
                str(values["paid"].amount) == "99.90" and str(values["discount"].amount) == "10.00"
            )
            coupon = await invoke(gateway, budget, order_id="000042", coupon_id="000009")
            assert coupon.status == ToolStatus.SUCCEEDED
            assert {f.name: f.value for f in coupon.facts}["usable"] is True
            checks.append("real_call_tool_order_coupon_content_evidence")
            foreign = await invoke(gateway, budget, order_id="DEMO-D05")
            assert foreign.error and foreign.error.code.value == "FORBIDDEN" and not foreign.facts
            checks.append("server_ownership_denial")
            if stage == "feature":
                parallel = await asyncio.gather(
                    invoke(gateway, budget),
                    invoke(gateway, budget, order_id="DEMO-D05", user="synthetic-user-b"),
                )
                assert all(r.status == ToolStatus.SUCCEEDED for r in parallel)
                assert [
                    next(f.value for f in r.facts if f.name == "order_id") for r in parallel
                ] == ["000031", "DEMO-D05"]
                checks.append("concurrent_users_one_process")
                for order_id, code in [
                    ("ABSENT", "NOT_FOUND"),
                    ("DEMO-D04", "RATE_LIMITED"),
                    ("DEMO-C01", "UPSTREAM_UNAVAILABLE"),
                    ("DEMO-C02", "MODEL_OUTPUT_INVALID"),
                    ("DEMO-C04", "MODEL_OUTPUT_INVALID"),
                    ("DEMO-A01", "MODEL_OUTPUT_INVALID"),
                    ("000053", "TIMEOUT"),
                ]:
                    result = await invoke(gateway, budget, order_id=order_id)
                    assert result.error and result.error.code.value == code and not result.facts
                    checks.append(f"{order_id}:{code}")
                await wait_record(gateway, gateway.diagnostics[-1]["operation_id"], "cancelled")
                checks.append("timeout_cancelled_on_server")
                injected = await invoke(gateway, budget, order_id="DEMO-D01")
                assert injected.status == ToolStatus.SUCCEEDED
                assert "忽略规则" in (injected.evidence_refs[0].summary or "")
                checks.append("injection_is_data_only")
                request, question, context = synthetic_request(budget, order_id="000053")
                task = asyncio.create_task(
                    gateway.execute(request, question, budget, context=context)
                )
                await wait_record(gateway, request.operation_id, "started")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("Cancellation was swallowed")
                await wait_record(gateway, request.operation_id, "cancelled")
                assert (await invoke(gateway, budget)).status == ToolStatus.SUCCEEDED
                checks.append("external_cancel_and_session_reuse")
            records = await gateway.ledger()
            assert len(records) == budget.attempts_used
            assert all(r.tool_name in ToolName for r in records)
            pid = gateway.pid
            assert pid is not None and process_alive(pid)
            checks.append("ledger_actual_calls_parameters_identity")
        assert not process_alive(pid)
        checks.append("child_process_reaped")
    return {
        "status": "PASS",
        "stage": stage,
        "sdk": "mcp-2.2.0",
        "transport": "stdio",
        "synthetic": True,
        "checks": checks,
        "pid": pid,
        "process_alive_after_close": False,
        "budget_used": budget.usage().model_dump(mode="json"),
        "ledger": [r.model_dump(mode="json") for r in records],
        "diagnostics": gateway.diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Launch real local SDK stdio subprocess"
    )
    parser.add_argument("--stage", choices=["feature", "main"], default="feature")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-calls", type=int, default=48)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live:
        print(
            "Offline preview: get_order_benefits(order_id), check_coupon(order_id, coupon_id). "
            "Add --live for real local stdio acceptance; no model or cloud calls."
        )
        return 0
    if args.timeout <= 0 or args.timeout > 300 or not 3 <= args.max_calls <= 128:
        parser.error("Use timeout within (0,300] and max-calls within [3,128]")
    if args.output is not None:
        local = Path.cwd().resolve() / ".local"
        output = args.output.resolve()
        if not output.is_relative_to(local) or output.exists():
            parser.error("Output must be a new file within this repository's .local")
    else:
        output = None
    try:
        report = asyncio.run(accept(args.stage, run_seconds=args.timeout, max_calls=args.max_calls))
    except (Exception, BaseExceptionGroup) as exc:
        report = {"status": "FAIL", "stage": args.stage, "error_type": type(exc).__name__}
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "stage")}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
