"""Whole API acceptance; offline model replay and live content evidence remain distinct."""

import argparse
import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from deephelp_app.adapters.tool_gateway import process_alive
from deephelp_app.adapters.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.application.conversation import STAGES
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.bootstrap.mvp_runtime import (
    BudgetSession,
    LocalAuth,
    live_app,
    validate_control_paths,
)
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models import (
    ConverseInput,
    RequestEnvelope,
    ResponseEnvelope,
    ToolInvocationRecord,
    VerifiedIdentity,
)
from deephelp_tools.demo.assembly import DemoAssembly as LiveAssembly
from deephelp_tools.demo.tool_config import FaultSpec, MockConfig
from deephelp_tools.evaluation.mvp_acceptance import api_client, mysql_claim_checks
from deephelp_tools.evaluation.samples import CaseExpectation, load_corpus
from deephelp_tools.learning.mvp_replay import ReplayAssembly


async def run(args: argparse.Namespace) -> int:
    output = local_path(args.output)
    validate_control_paths(
        [
            local_path(args.auth),
            local_path(args.budget_state),
            local_path(args.pointer),
            Path(args.providers),
        ],
        output=output,
    )
    if output.exists():
        raise ConfigurationError("Use a new acceptance report path")
    auth = LocalAuth(local_path(args.auth))
    nonce = "m08-" + uuid4().hex
    trace = MemoryTrace()
    report: dict[str, Any] = {
        "status": "PENDING",
        "mode": "live" if args.live else "synthetic-replay",
        "stage": args.stage,
        "results": [],
    }
    faults = {"DEMO-D04": FaultSpec(kind="upstream_500"), "DEMO-D01": FaultSpec(kind="injection")}
    assembly: LiveAssembly | ReplayAssembly
    gate: BudgetSession | None = None
    if args.live:
        if output in {local_path(args.budget_state), local_path(args.auth)}:
            raise ConfigurationError("Report/control paths must differ")
        gate = BudgetSession(local_path(args.budget_state))
        assembly = LiveAssembly(
            Path.cwd(),
            Path(args.providers),
            local_path(args.pointer),
            mock=MockConfig(faults=faults),
        )
        gate.open()
        app = live_app(assembly, auth, gate, trace_path=str(output.with_suffix(".trace.jsonl")))
    else:
        assembly = ReplayAssembly()
        app = create_app(
            Settings(
                mode="test", request_timeout=90, child_timeout=30, max_attempts=40, retry_limit=2
            ),
            trace=trace,
            identity_provider=auth,
            conversation_factory=assembly.open,
        )
    messages: list[tuple[ConverseInput, str, str]] = []
    cases: list[tuple[str, ConverseInput, VerifiedIdentity, CaseExpectation | None]]
    checks: list[bool] = []
    try:
        async with app.router.lifespan_context(app):
            async with api_client(app, http=args.http) as client:
                if not args.live:
                    cases = [
                        (c.case_id, c.message, c.identity, c.expected) for c in load_corpus().cases
                    ]
                else:
                    identity = VerifiedIdentity(
                        tenant_id="synthetic-tenant", user_id="synthetic-user-a"
                    )
                    texts = [
                        ("discount", "订单 000031 未享受优惠，请查一下"),
                        ("coupon", "订单 000042 的券 000009 不能用"),
                        ("activity", "查询订单 000053 参加的活动"),
                        ("dense", "请核查订单 000031，结算优惠没有到账"),
                        ("missing", "我的订单未享受优惠，请查一下"),
                        ("unknown", "今天天气怎么样"),
                        ("failure", "订单 DEMO-D04 未享受优惠"),
                        ("injection", "订单 DEMO-D01 未享受优惠"),
                        ("expired", "订单 DEMO-C04 的券 COUPON-C04 不能用"),
                        ("empty", "查询订单 DEMO-A04 参加的活动"),
                    ]
                    if args.stage == "main":
                        texts = texts[:6]
                    cases = [
                        (
                            name,
                            ConverseInput(
                                channel="m08-accept",
                                session_id=nonce,
                                message_id=nonce + "-" + name,
                                raw_text=text,
                                occurred_at=datetime.now(UTC),
                            ),
                            identity,
                            None,
                        )
                        for name, text in texts
                    ]
                    if args.samples:
                        selected = {
                            "M02-001",
                            "M02-003",
                            "M02-005",
                            "M02-012",
                            "M02-013",
                            "M02-017",
                            "M02-019",
                            "M02-025",
                            "M02-027",
                            "M02-029",
                            "M02-032",
                            "M02-035",
                        }
                        cases = [
                            (c.case_id, c.message, c.identity, c.expected)
                            for c in load_corpus().cases
                            if c.case_id in selected
                        ]
                for name, message, identity, expected in cases:
                    token = next(t for t, i in auth.rows if i == identity)
                    body = message.model_copy(
                        update={
                            "message_id": nonce + "-" + name,
                            "session_id": nonce + "-" + name if args.isolated_sessions else nonce,
                        }
                    )
                    start = perf_counter()
                    reply = await client.post(
                        "/converse",
                        headers={"Authorization": "Bearer " + token},
                        json=body.model_dump(mode="json"),
                    )
                    result = ResponseEnvelope.model_validate(reply.json())
                    assert assembly.tools is not None
                    ledger = [r for r in await assembly.tools.ledger() if r.run_id == result.run_id]
                    valid = (
                        reply.status_code == 200 and tuple(s.stage for s in result.stages) == STAGES
                    )
                    valid = valid and result.run_id is not None and result.question_id is not None
                    valid = valid and (
                        result.error is None or result.error.code != "INTERNAL_ERROR"
                    )
                    valid = valid and set(result.tool_call_ids) == {r.call_id for r in ledger}
                    if (
                        result.missing_slots
                        or result.intent_decision is None
                        or result.intent_decision.final_code is None
                    ):
                        valid = valid and not ledger and not result.facts
                    facts = {f.name: f.value for f in result.facts}
                    if args.live:
                        if expected:
                            target = {
                                "M02-001": "HANDOFF",
                                "M02-003": "CLARIFY",
                                "M02-012": "HANDOFF",
                                "M02-027": "CLARIFY",
                                "M02-035": "HANDOFF",
                            }.get(name, "ANSWERED")
                            valid = valid and result.outcome == target
                            valid = (
                                valid
                                and (
                                    result.intent_decision.final_code
                                    if result.intent_decision
                                    else None
                                )
                                == expected.intent
                            )
                            valid = valid and [r.parameters for r in ledger] == [
                                t.parameters for t in expected.tools
                            ]
                        else:
                            valid = valid and live_content(name, result, ledger, facts)
                    actual_tools = [
                        {
                            "tool_name": r.tool_name.value,
                            "parameters": r.parameters.model_dump(mode="json"),
                        }
                        for r in ledger
                    ]
                    expected_tools = (
                        [t.model_dump(mode="json") for t in expected.tools] if expected else None
                    )
                    observation = {
                        "case": name,
                        "elapsed_ms": (perf_counter() - start) * 1000,
                        "passed": valid,
                        "response": result.model_dump(mode="json"),
                        "ledger": [r.model_dump(mode="json") for r in ledger],
                        "expected": expected.model_dump(mode="json") if expected else None,
                        "intent_match": (
                            result.intent_decision.final_code if result.intent_decision else None
                        )
                        == expected.intent
                        if expected
                        else None,
                        "tools_match": actual_tools == expected_tools if expected else None,
                        "outcome_match": result.outcome == expected.outcome if expected else None,
                    }
                    report["results"].append(observation)
                    checks.append(valid)
                    messages.append((body, token, result.run_id or ""))
                    if args.live and not valid:
                        break
                # Replays retain the original run and do not execute external calls.
                body, token, run_id = messages[0]
                replay = await client.post(
                    "/converse",
                    headers={"Authorization": "Bearer " + token},
                    json=body.model_dump(mode="json"),
                )
                replay_body = replay.json()
                checks.append(
                    replay_body["run_id"] == run_id
                    and replay_body["replayed"]
                    and replay_body["budget_used"]["attempts"] == 0
                )
                report["same_process_replay"] = replay_body
                conflict = await client.post(
                    "/converse",
                    headers={"Authorization": "Bearer " + token},
                    json=body.model_copy(update={"raw_text": "changed payload"}).model_dump(
                        mode="json"
                    ),
                )
                checks.append(conflict.status_code == 409)
                unauthorized = await client.post("/converse", json=body.model_dump(mode="json"))
                checks.append(unauthorized.status_code == 401)
                report["conflict_http"], report["unauthenticated_http"] = (
                    conflict.status_code,
                    unauthorized.status_code,
                )
                if args.live:
                    assert isinstance(assembly, LiveAssembly) and assembly.ledger is not None
                    persisted = []
                    for body, token, run_id in messages:
                        identity = next(i for t, i in auth.rows if t == token)
                        req = RequestEnvelope(
                            **body.model_dump(),
                            identity=identity,
                            request_id=uuid4().hex,
                            trace_id=uuid4().hex,
                            received_at=datetime.now(UTC),
                        )
                        receipt = await assembly.ledger.accept(req)
                        persisted.append(
                            receipt.response is not None
                            and receipt.run_id == run_id
                            and receipt.question.status == receipt.response.question_status
                            and receipt.question.version == 2
                        )
                    report["mysql_terminal_rows"] = persisted
                    checks.extend(persisted)
                    report["mysql_claim_checks"] = await mysql_claim_checks(assembly.ledger)
                    checks.extend(report["mysql_claim_checks"].values())
        if args.live:
            # Fresh resources and a new MCP process prove durable replay across app restarts.
            assert isinstance(assembly, LiveAssembly) and gate is not None
            app2 = live_app(
                assembly, auth, gate, trace_path=str(output.with_suffix(".restart.jsonl"))
            )
            async with app2.router.lifespan_context(app2):
                async with api_client(app2, http=args.http) as client:
                    body, token, run_id = messages[0]
                    reply = await client.post(
                        "/converse",
                        headers={"Authorization": "Bearer " + token},
                        json=body.model_dump(mode="json"),
                    )
                    data = reply.json()
                    report["restart_replay"] = data
                    assert assembly.tools is not None
                    records = await assembly.tools.ledger()
                    checks.append(data["run_id"] == run_id and data["replayed"] and not records)
        assert assembly.tools is not None
        report["mcp_process_exited"] = assembly.tools.pid is not None and not process_alive(
            assembly.tools.pid
        )
        checks.append(report["mcp_process_exited"])
        report["status"] = "PASS" if all(checks) else "FAIL"
    except (AppError, ConfigurationError) as exc:
        report.update(
            status="FAIL",
            error={
                "message": str(exc),
                "provider_request_id": getattr(exc, "provider_request_id", None),
            },
        )
    except Exception as exc:
        report.update(status="FAIL", error={"type": type(exc).__name__})
    finally:
        if isinstance(assembly, LiveAssembly) and assembly.gateway:
            report["model_diagnostics"] = assembly.gateway.diagnostics
        if gate:
            report["cumulative_budget"] = gate.state
            gate.close()
        rows = report["results"]
        report["metrics"] = {
            "cases": len(rows),
            "intent_matches": sum(r["intent_match"] is True for r in rows),
            "tool_matches": sum(r["tools_match"] is True for r in rows),
            "outcome_matches": sum(r["outcome_match"] is True for r in rows),
            "answered": sum(r["response"]["outcome"] == "ANSWERED" for r in rows),
            "clarify": sum(r["response"]["outcome"] == "CLARIFY" for r in rows),
            "handoff": sum(r["response"]["outcome"] == "HANDOFF" for r in rows),
            "error": sum(r["response"]["outcome"] == "ERROR" for r in rows),
            "model_calls": sum(s["model_calls"] for r in rows for s in r["response"]["stages"]),
            "tool_calls": sum(len(r["ledger"]) for r in rows),
            "outcome_differences": [r["case"] for r in rows if r["outcome_match"] is False],
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "metrics": report["metrics"],
                "error": report.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" else 1


def live_content(
    name: str,
    result: ResponseEnvelope,
    ledger: list[ToolInvocationRecord],
    facts: Mapping[str, object],
) -> bool:
    from decimal import Decimal

    from deephelp_app.domain.models import Money

    if name in {"discount", "dense"}:
        paid, discount = facts.get("paid"), facts.get("discount")
        return (
            result.outcome == "ANSWERED"
            and isinstance(paid, Money)
            and paid.amount == Decimal("99.90")
            and isinstance(discount, Money)
            and discount.amount == Decimal("10.00")
            and facts.get("order_id") == "000031"
            and len(ledger) == 1
        )
    if name == "coupon":
        return (
            result.outcome == "ANSWERED"
            and facts.get("usable") is True
            and facts.get("coupon_id") == "000009"
        )
    if name == "activity":
        return result.outcome == "ANSWERED" and facts.get("activity_ids") == "ACTIVITY-DEMO-01"
    if name == "missing":
        return result.outcome == "CLARIFY" and "order_id" in result.missing_slots and not ledger
    if name == "unknown":
        return result.outcome == "HANDOFF" and not ledger
    if name == "failure":
        return result.outcome == "ERROR" and not result.facts and len(ledger) >= 1
    if name == "injection":
        return result.outcome == "HANDOFF" and len(ledger) == 1 and "退款成功" not in result.reply
    if name == "expired":
        return result.outcome == "ANSWERED" and facts.get("coupon_status") == "expired"
    if name == "empty":
        return result.outcome == "ANSWERED" and facts.get("activity_ids") == ""
    return False


def main() -> int:
    p = argparse.ArgumentParser(description="M08 full API regression / explicit live acceptance")
    p.add_argument("--live", action="store_true")
    p.add_argument("--http", action="store_true", help="Use an actual loopback HTTP server")
    p.add_argument(
        "--isolated-sessions",
        action="store_true",
        help="Give each single-message regression its own session",
    )
    p.add_argument(
        "--samples", action="store_true", help="Live subset of original frozen M02 samples"
    )
    p.add_argument("--stage", choices=["feature", "main"], default="feature")
    p.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    p.add_argument("--pointer", default=".local/m05/active.json")
    p.add_argument("--auth", default=".local/m08/auth.json")
    p.add_argument("--budget-state", default=".local/m08/session-budget.json")
    p.add_argument("--output", required=True)
    return asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
