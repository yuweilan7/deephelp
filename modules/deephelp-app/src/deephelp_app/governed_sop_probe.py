"""M14 real model/stdio content and same HTTP chain with retained SOP snapshots."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.business_catalog import (
    business_cases,
    business_config,
    business_registry,
    validate_catalog,
)
from deephelp_app.cases import MySQLCaseRepository
from deephelp_app.conversation import STAGES
from deephelp_app.domain.models import ModelToolCall, ResponseEnvelope, SOPStatus
from deephelp_app.errors import ConfigurationError
from deephelp_app.event_replay import envelope
from deephelp_app.execution import AsyncCalls, ExecutionBudget
from deephelp_app.live_probe import local_path
from deephelp_app.mvp_acceptance import api_client
from deephelp_app.mvp_runtime import (
    BudgetSession,
    LiveAssembly,
    LocalAuth,
    live_app,
    validate_control_paths,
)
from deephelp_app.providers import ProviderConfig, create_gateway
from deephelp_app.sop import SOPExecutor
from deephelp_app.sop_acceptance import proposal_registry, scenario_config, scenarios
from deephelp_app.sop_governance import RegistryStore, bundled_registry
from deephelp_app.sop_probe import sop_question
from deephelp_app.sop_replay import ReplayModel
from deephelp_app.tool_gateway import ToolGateway, process_alive


async def content(
    args: argparse.Namespace, gate: BudgetSession | None, report: dict[str, Any], save: Any
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.AsyncHTTPTransport(retries=0), trust_env=False, timeout=60
    ) as client:
        model = (
            create_gateway(client, ProviderConfig.load(Path(args.providers)), AsyncCalls(1, 45))
            if gate
            else None
        )
        catalog = getattr(args, "business_catalog", False)
        for case in business_cases() if catalog else scenarios().cases:
            registry = (
                business_registry()
                if catalog
                else proposal_registry()
                if case.proposal
                else bundled_registry()
            )
            budget = gate.request() if gate else ExecutionBudget.start(60, 20, 0)
            budget.retry_remaining = 0
            question, context = sop_question(
                budget, case.intent, order=case.order, coupon=case.coupon, raw_text=case.raw_text
            )
            question = question.model_copy(
                update={
                    "versions": registry.pin(question.versions, case.intent),
                    "entities": tuple(
                        e for e in question.entities if e.name not in case.missing_slots
                    ),
                }
            )
            selected = model or ReplayModel(
                [
                    ModelToolCall(
                        call_id=f"selected-{i}",
                        name=tool.value,
                        arguments={
                            "order_id": case.order,
                            **({"coupon_id": case.coupon} if tool.value == "check_coupon" else {}),
                        },
                    )
                    for i, tool in enumerate(case.tools)
                ]
            )
            gateway = ToolGateway(config=business_config() if catalog else scenario_config(case))
            async with gateway.open():
                result = await SOPExecutor(selected, gateway, registry=registry).execute(
                    question, budget, context=context, run_id="m14-" + uuid4().hex
                )
                ledger = await gateway.ledger()
            facts = {f.name: f.value for f in result.facts}
            evidence_ids = {r.evidence_id for r in result.evidence_refs}
            passed = (
                result.status == case.status
                and tuple(r.tool_name for r in ledger) == case.tools
                and result.tool_call_ids == tuple(r.call_id for r in ledger)
                and (not case.error or bool(result.error and result.error.code == case.error))
                and all(facts.get(k) == v for k, v in case.expected_facts.items())
                and (
                    not case.end_node
                    or bool(result.node_path and result.node_path[-1] == case.end_node)
                )
                and all(
                    r.identity == question.identity and r.parameters.order_id == case.order
                    for r in ledger
                )
                and evidence_ids
                <= {e for r in ledger if r.status == "succeeded" for e in r.evidence_ids}
                and bool(gateway.pid and not process_alive(gateway.pid))
                and (not case.missing_slots or (budget.attempts_used == 0 and not ledger))
                and (
                    case.status != SOPStatus.FAILED
                    or (not result.facts and not result.evidence_refs)
                )
                and (
                    not case.proposal
                    or bool(result.plan and result.plan.snapshot_hash == registry.snapshot_hash)
                )
            )
            report["results"].append(
                {
                    "case": case.case_id,
                    "passed": passed,
                    "snapshot": registry.snapshot_hash,
                    "result": result.model_dump(mode="json"),
                    "ledger": [r.model_dump(mode="json") for r in ledger],
                    "budget_used": budget.usage().model_dump(mode="json"),
                    "diagnostics": gateway.diagnostics,
                }
            )
            report["model_diagnostics"] = model.diagnostics if model else []
            save()
            print(
                json.dumps({"case": case.case_id, "passed": passed, "status": result.status.value}),
                flush=True,
            )
            if not passed:
                raise ConfigurationError("M14 scenario content failed; result/ledger preserved")
        report["model_diagnostics"] = model.diagnostics if model else []


async def business(
    args: argparse.Namespace, gate: BudgetSession, report: dict[str, Any], save: Any
) -> None:
    nonce = uuid4().hex[:12]
    directory = local_path(str(Path(args.output).with_suffix(".registry")))
    store = RegistryStore(directory)
    original = bundled_registry()
    store.publish(original)
    auth = LocalAuth(Path(args.auth))
    token, identity = auth.rows[0]
    checks = report["checks"]
    missing_session = "m14-" + nonce + "-missing"
    old_question: str | None = None
    bodies: list[tuple[dict[str, Any], ResponseEnvelope]] = []
    for epoch in ("before_publish", "after_publish"):
        if epoch == "after_publish":
            store.publish(proposal_registry())
            checks["registry_publish_retains_original"] = len(store.history()) == 2
        collection = "dh_m10_events_m14_probe_" + nonce + "_" + epoch
        assembly = LiveAssembly(
            Path.cwd(),
            Path(args.providers),
            Path(args.pointer),
            event_collection=collection,
            sop_directory=directory,
        )
        app = live_app(
            assembly, auth, gate, str(Path(args.output).with_suffix(f".{epoch}.trace.jsonl"))
        )
        async with app.router.lifespan_context(app):
            assert assembly.ledger and assembly.tools and assembly.event_index
            ledger_repo = assembly.ledger
            tool_gateway = assembly.tools
            async with api_client(app, http=True) as client:

                async def ask(
                    name: str,
                    text: str,
                    session: str,
                    epoch: str = epoch,
                    repository: MySQLCaseRepository = ledger_repo,
                    tools: ToolGateway = tool_gateway,
                    **extra: Any,
                ) -> ResponseEnvelope:
                    body = dict(
                        channel="m14-accept",
                        message_id="m14-" + uuid4().hex,
                        session_id=session,
                        raw_text=text,
                        occurred_at=datetime.now(UTC).isoformat(),
                        **extra,
                    )
                    reply = await client.post(
                        "/converse", headers={"Authorization": "Bearer " + token}, json=body
                    )
                    row = ResponseEnvelope.model_validate(reply.json())
                    report["http"].append(
                        {
                            "name": name,
                            "epoch": epoch,
                            "http_status": reply.status_code,
                            "request": body,
                            "response": row.model_dump(mode="json"),
                        }
                    )
                    checks[epoch + ":" + name + ":http"] = reply.status_code == 200
                    checks[epoch + ":" + name + ":same_chain"] = (
                        tuple(s.stage for s in row.stages) == STAGES
                    )
                    q = await repository.question(identity, session, row.question_id or "none")
                    checks[epoch + ":" + name + ":mysql"] = bool(
                        q and q.versions == row.versions and q.status == row.question_status
                    )
                    actual = [r for r in await tools.ledger() if r.run_id == row.run_id]
                    checks[epoch + ":" + name + ":actual_tool_ids"] = row.tool_call_ids == tuple(
                        r.call_id for r in actual
                    )
                    bodies.append((body, row))
                    save()
                    return row

                if epoch == "before_publish":
                    missing = await ask("missing", "我的订单未享受优惠", missing_session)
                    checks["missing_pins_snapshot_without_tools"] = (
                        missing.outcome == "CLARIFY"
                        and not missing.tool_call_ids
                        and missing.versions.sop_snapshot == original.snapshot_hash
                    )
                    old_question = missing.question_id
                    for name, text, expected_fact in (
                        ("discount", "订单000031未享受优惠，请查一下", "discount"),
                        ("coupon", "订单000042的券000009不能用", "coupon_status"),
                        ("activity", "查询订单000053参加的活动", "activity_ids"),
                        ("threshold", "订单DEMO-C01的券COUPON-C01不能用，请核查", "minimum_spend"),
                    ):
                        row = await ask(name, text, "m14-" + nonce + "-" + name)
                        facts = {f.name: f.value for f in row.facts}
                        checks[name + ":answered_evidence"] = (
                            row.outcome == "ANSWERED"
                            and expected_fact in facts
                            and bool(row.evidence_refs)
                        )
                        checks[name + ":snapshot"] = (
                            row.versions.sop_snapshot == original.snapshot_hash
                        )
                        if name == "discount":
                            checks["discount:amounts"] = (
                                "99.90" in row.reply and "10.00" in row.reply
                            )
                        elif name == "coupon":
                            checks["coupon:status"] = facts.get("coupon_status") == "usable"
                        elif name == "activity":
                            checks["activity:ids"] = facts.get("activity_ids") == "ACTIVITY-DEMO-01"
                        else:
                            checks["threshold:two_sources"] = (
                                len(row.tool_call_ids) == 2 and row.sop_node_path[-1] == "threshold"
                            )
                else:
                    continued = await ask(
                        "old_question",
                        "补充：订单DEMO-D01",
                        missing_session,
                        question_hint=old_question,
                    )
                    checks["retained_question_uses_original"] = (
                        continued.question_id == old_question
                        and continued.outcome == "HANDOFF"
                        and continued.versions.sop_snapshot == original.snapshot_hash
                        and continued.sop_plan is None
                    )
                    fresh = await ask(
                        "proposal", "订单DEMO-D01优惠未到账，请核查", "m14-" + nonce + "-proposal"
                    )
                    checks["fresh_question_proposal_not_approved"] = (
                        fresh.outcome == "HANDOFF"
                        and fresh.sop_plan is not None
                        and fresh.next_action == "request_approval"
                        and len(fresh.tool_call_ids) == 1
                        and fresh.versions.sop_snapshot == proposal_registry().snapshot_hash
                    )
                    body, stored = bodies[-1]
                    reply = await client.post(
                        "/converse", headers={"Authorization": "Bearer " + token}, json=body
                    )
                    replay = ResponseEnvelope.model_validate(reply.json())
                    checks["proposal_replay_zero_calls_same_binding"] = (
                        replay.replayed
                        and replay.sop_plan == stored.sop_plan
                        and replay.call_counts.model_calls == replay.call_counts.tool_calls == 0
                    )
                    new_pool = await MySQLCaseRepository.open(Path.cwd())
                    try:
                        replayed = await new_pool.lookup(
                            envelope(
                                body["raw_text"],
                                body["session_id"],
                                identity=identity,
                                channel=body["channel"],
                                message_id=body["message_id"],
                                occurred_at=datetime.fromisoformat(body["occurred_at"]),
                            )
                        )
                        checks["new_mysql_pool_retains_plan"] = bool(
                            replayed
                            and replayed.response
                            and replayed.response.sop_plan == fresh.sop_plan
                        )
                    finally:
                        await new_pool.aclose()
                    checks["rollback_verified"] = store.rollback() == original.snapshot_hash
            event_index = assembly.event_index
            await event_index.rpc(
                partial(event_index.client.drop_collection, collection, timeout=None, retry_times=0)
            )
            checks[epoch + ":event_collection_removed"] = not await event_index.rpc(
                partial(event_index.client.has_collection, collection, timeout=None, retry_times=0)
            )
        checks[epoch + ":mcp_exit"] = bool(
            assembly.tools.pid and not process_alive(assembly.tools.pid)
        )
        save()
    if not all(checks.values()):
        raise ConfigurationError("M14 HTTP content checks failed; full report preserved")


async def run(args: argparse.Namespace) -> int:
    output = local_path(args.output)
    if output.exists():
        raise ConfigurationError("Use a new M14 report path")
    validate_control_paths(
        [Path(args.budget_state), Path(args.providers), Path(args.pointer), Path(args.auth)],
        output=output,
    )
    gate = BudgetSession(local_path(args.budget_state)) if args.live else None
    report: dict[str, Any] = {
        "status": "PENDING",
        "stage": args.stage,
        "live": args.live,
        "results": [],
        "http": [],
        "checks": {},
    }
    if getattr(args, "business_catalog", False):
        report["catalog"] = validate_catalog()
        report["scope"] = (
            "all frozen business outcomes: real model/SDK stdio; HTTP evaluated by M17"
        )

    def save() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if gate:
        gate.open()
    try:
        async with asyncio.timeout(args.timeout):
            await content(args, gate, report, save)
            if gate and not getattr(args, "business_catalog", False):
                await business(args, gate, report, save)
        report["status"] = "PASS"
        return 0
    except Exception as exc:
        report["status"] = "FAIL"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}), flush=True)
        return 1
    finally:
        save()
        if gate:
            gate.close()


def main() -> int:
    p = argparse.ArgumentParser(description="M14 SOP content and HTTP acceptance")
    p.add_argument("--live", action="store_true")
    p.add_argument(
        "--business-catalog", action="store_true", help="Frozen 22-business outcome scope"
    )
    p.add_argument("--stage", choices=["feature", "main"], default="feature")
    p.add_argument("--output", required=True)
    p.add_argument("--budget-state", default=".local/m14/session-budget.json")
    p.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    p.add_argument("--auth", default=".local/m08/auth.json")
    p.add_argument("--pointer", default=".local/m09/active.json")
    p.add_argument("--timeout", type=float, default=1200)
    args = p.parse_args()
    try:
        return asyncio.run(run(args))
    except ValueError, OSError, ConfigurationError:
        print('{"status":"FAIL","reason":"configuration_or_control_path"}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
