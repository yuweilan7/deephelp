"""Fixed M16 content acceptance over authenticated loopback HTTP and owned stdio."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.app import create_app
from deephelp_app.cases import MySQLCaseRepository
from deephelp_app.cases_fake import MemoryCaseRepository
from deephelp_app.domain.models import RequestEnvelope, ResponseEnvelope
from deephelp_app.errors import ConfigurationError
from deephelp_app.event_replay import ReplayJudge
from deephelp_app.live_probe import local_path
from deephelp_app.mcp_mock import FaultSpec, MockConfig
from deephelp_app.mvp_acceptance import api_client
from deephelp_app.mvp_replay import ReplayAssembly
from deephelp_app.mvp_runtime import (
    BudgetSession,
    LiveAssembly,
    LocalAuth,
    live_app,
    validate_control_paths,
    validate_trace_path,
)
from deephelp_app.settings import Settings
from deephelp_app.tool_gateway import process_alive
from deephelp_app.trace import JsonlTrace


async def run(args: argparse.Namespace) -> int:
    output = local_path(args.output)
    trace_path = output.with_suffix(".trace.jsonl")
    controls = [
        local_path(args.auth),
        local_path(args.budget_state),
        local_path(args.pointer),
        Path(args.providers),
    ]
    validate_control_paths(controls, output=output)
    validate_trace_path(trace_path, controls)
    if output.exists() or trace_path.exists():
        raise ConfigurationError("Use new M16 report/trace paths")
    auth = LocalAuth(local_path(args.auth))
    token, identity = auth.rows[0]
    nonce = "m16-" + uuid4().hex
    collection = "dh_m10_events_m16_" + uuid4().hex
    report: dict[str, Any] = {
        "status": "PENDING",
        "live": args.live,
        "stage": args.stage,
        "checks": {},
        "responses": [],
        "views": [],
    }
    checks = report["checks"]

    def save() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    gate = BudgetSession(local_path(args.budget_state)) if args.live else None
    assembly: LiveAssembly | ReplayAssembly
    if gate:
        assembly = LiveAssembly(
            Path.cwd(),
            Path(args.providers),
            local_path(args.pointer),
            event_collection=collection,
            reply_polish=True,
            mock=MockConfig(faults={"DEMO-D04": FaultSpec(delay_seconds=2)}),
        )
        app = live_app(assembly, auth, gate, str(trace_path))
    else:
        assembly = ReplayAssembly(ledger=MemoryCaseRepository(), event_judge=ReplayJudge())
        app = create_app(
            Settings(mode="test", request_timeout=60, max_attempts=40),
            trace=JsonlTrace(trace_path),
            identity_provider=auth,
            conversation_factory=assembly.open,
        )
    bodies: list[dict[str, Any]] = []
    try:
        if gate:
            gate.open()
        async with asyncio.timeout(args.timeout), app.router.lifespan_context(app):
            service = app.state.resources.conversation
            actual_tools = service.sop.tools

            class TimeoutTools:
                async def execute(
                    self, request: Any, question: Any, budget: Any, **kwargs: Any
                ) -> Any:
                    if request.parameters.order_id == "DEMO-D04":
                        request = request.model_copy(update={"timeout_seconds": 0.2})
                    return await actual_tools.execute(request, question, budget, **kwargs)

            service.sop.tools = TimeoutTools()
            async with api_client(app, http=True) as client:
                headers = {"Authorization": "Bearer " + token}

                async def ask(name: str, text: str, session: str, **extra: Any) -> ResponseEnvelope:
                    body = dict(
                        channel="m16-accept",
                        session_id=session,
                        message_id=uuid4().hex,
                        raw_text=text,
                        occurred_at=datetime.now(UTC).isoformat(),
                        **extra,
                    )
                    bodies.append(body)
                    result = await client.post("/converse", headers=headers, json=body)
                    row = ResponseEnvelope.model_validate(result.json())
                    report["responses"].append(
                        {"case": name, "body": body, "response": row.model_dump(mode="json")}
                    )
                    checks[name + ":http"] = result.status_code == 200 and row.run_id is not None
                    views: dict[str, Any] = {}
                    for view in ("intent", "turns", "flow"):
                        reply = await client.get(
                            f"/debug/runs/{row.run_id}/{view}",
                            headers=headers,
                            params={"session_id": session},
                        )
                        views[view] = reply.json()
                        checks[name + ":" + view] = (
                            reply.status_code == 200
                            and reply.json().get("trace_id") == row.trace_id
                            and not reply.json().get("incomplete")
                        )
                    report["views"].append({"case": name, "views": views})
                    flow = views["flow"].get("data", {})
                    if row.sop_node_path:
                        nodes = flow.get("sop_nodes", [])
                        checks[name + ":node_path"] = [n["node_id"] for n in nodes] == list(
                            row.sop_node_path
                        )
                        checks[name + ":node_times"] = all(
                            n["started_at"] <= n["finished_at"] for n in nodes
                        )
                    checks[name + ":version"] = views["intent"].get("data", {}).get(
                        "versions"
                    ) == row.versions.model_dump(mode="json")
                    checks[name + ":trace_tools"] = {
                        t["call_id"] for t in flow.get("tools", []) if t["call_id"]
                    } == set(row.tool_call_ids)
                    checks[name + ":input_version"] = (
                        views["turns"]
                        .get("data", {})
                        .get("input", {})
                        .get("accepted_question_version", 0)
                        >= 1
                    )
                    if row.facts:
                        checks[name + ":evidence"] = bool(row.evidence_refs) and all(
                            set(f.evidence_ids) <= {e.evidence_id for e in row.evidence_refs}
                            for f in row.facts
                        )
                    save()
                    return row

                for name, text, fact in (
                    ("discount", "订单000031未享受优惠，请查一下", "discount"),
                    ("coupon", "订单000042的券000009不能用", "coupon_status"),
                    ("activity", "查询订单000053参加的活动", "activity_ids"),
                ):
                    row = await ask(name, text, nonce + name)
                    facts = {f.name: f.value for f in row.facts}
                    checks[name + ":content"] = row.outcome == "ANSWERED" and fact in facts
                    if args.live:
                        checks[name + ":real_polish"] = bool(
                            row.reply_presentation and row.reply_presentation.mode == "polished"
                        )
                    if name == "discount":
                        checks[name + ":amounts"] = "99.90" in row.reply and "10.00" in row.reply
                    elif name == "coupon":
                        checks[name + ":status"] = facts.get("coupon_status") == "usable"
                    else:
                        checks[name + ":activity"] = facts.get("activity_ids") == "ACTIVITY-DEMO-01"

                session = nonce + "turns"
                missing = await ask("missing", "我的订单未享受优惠", session)
                checks["missing:no_tools"] = (
                    missing.outcome == "CLARIFY" and not missing.tool_call_ids
                )
                completed = await ask("supplement", "补充订单000031", session)
                checks["supplement:membership"] = (
                    completed.question_id == missing.question_id and completed.outcome == "ANSWERED"
                )
                if args.stage == "feature":
                    await ask("unknown", "今天天气怎么样", nonce + "unknown")
                    if args.live:
                        timeout = await ask(
                            "timeout", "订单DEMO-D04未享受优惠，请查一下", nonce + "timeout"
                        )
                        timeout_flow = report["views"][-1]["views"]["flow"]["data"]
                        checks["timeout:content"] = (
                            timeout.outcome == "ERROR"
                            and not timeout.facts
                            and bool(timeout.tool_call_ids)
                        )
                        checks["timeout:location"] = timeout_flow["stages"][9][
                            "status"
                        ] == "failed" and any(
                            t.get("result", {}).get("error", {}).get("code") == "TIMEOUT"
                            for t in timeout_flow["tools"]
                        )
                        checks["timeout:node"] = any(
                            n["status"] == "failed" and n.get("error_code") == "TIMEOUT"
                            for n in timeout_flow["sop_nodes"]
                        )

                body, stored = bodies[0], report["responses"][0]["response"]
                replay = ResponseEnvelope.model_validate(
                    (await client.post("/converse", headers=headers, json=body)).json()
                )
                checks["replay:zero_calls"] = (
                    replay.replayed
                    and replay.run_id == stored["run_id"]
                    and replay.call_counts.model_calls == replay.call_counts.tool_calls == 0
                )
                debug = await client.get(
                    f"/debug/runs/{replay.run_id}/flow",
                    headers=headers,
                    params={"session_id": body["session_id"]},
                )
                checks["replay:original_trace"] = debug.json().get("trace_id") == stored["trace_id"]
                denial = await client.get(
                    f"/debug/runs/{replay.run_id}/flow", params={"session_id": body["session_id"]}
                )
                checks["auth:required"] = denial.status_code == 401
                denial = await client.get(
                    f"/debug/runs/{replay.run_id}/flow",
                    headers=headers,
                    params={"session_id": "foreign"},
                )
                checks["auth:session_isolated"] = denial.status_code == 404
                exported = await client.get(
                    f"/debug/runs/{replay.run_id}/flow",
                    headers=headers,
                    params={"session_id": body["session_id"], "export": "true"},
                )
                checks["export:redacted"] = (
                    exported.status_code == 200
                    and "000031" not in exported.text
                    and "synthetic-user-a" not in exported.text
                    and "attachment" in exported.headers.get("content-disposition", "")
                )
                assert assembly.tools is not None
                ledger = await assembly.tools.ledger()
                checks["tools:real_ledger"] = all(
                    set(r["response"]["tool_call_ids"]) <= {entry.call_id for entry in ledger}
                    for r in report["responses"]
                )
                save()
            if args.live:
                assert isinstance(assembly, LiveAssembly)
                index = assembly.event_index
                assert index is not None
                await index.rpc(
                    partial(index.client.drop_collection, collection, timeout=None, retry_times=0)
                )
                checks["event_collection:removed"] = not await index.rpc(
                    partial(index.client.has_collection, collection, timeout=None, retry_times=0)
                )
        assert assembly.tools is not None
        checks["mcp:exit"] = bool(assembly.tools.pid and not process_alive(assembly.tools.pid))

        # Reopen only trace resources: diagnostic reads need no model or tool execution.
        reopened = create_app(
            Settings(mode="test"), trace=JsonlTrace(trace_path), identity_provider=auth
        )
        async with (
            reopened.router.lifespan_context(reopened),
            api_client(reopened, http=True) as client,
        ):
            value = await client.get(
                f"/debug/runs/{stored['run_id']}/flow",
                headers={"Authorization": "Bearer " + token},
                params={"session_id": body["session_id"]},
            )
            checks["trace:new_resources"] = (
                value.status_code == 200 and value.json()["trace_id"] == stored["trace_id"]
            )
        if gate:
            repository = await MySQLCaseRepository.open(Path.cwd())
            try:
                receipt = await repository.lookup(
                    RequestEnvelope.model_validate(
                        {
                            **body,
                            "identity": identity,
                            "request_id": uuid4().hex,
                            "trace_id": uuid4().hex,
                            "received_at": datetime.now(UTC),
                        }
                    )
                )
                checks["mysql:new_pool"] = bool(
                    receipt
                    and receipt.response
                    and receipt.response.reply == stored["reply"]
                    and receipt.response.reply_presentation
                    == ResponseEnvelope.model_validate(stored).reply_presentation
                )
            finally:
                await repository.aclose()
        if not all(checks.values()):
            raise ConfigurationError("M16 content checks failed; report preserved")
        report["status"] = "PASS"
        return 0
    except Exception as exc:
        report["status"] = "FAIL"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)
        return 1
    finally:
        if gate:
            report["cumulative_attempts"] = gate.state["attempts"]
            gate.close()
        save()
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "checks": len(checks),
                    "failed": [k for k, v in checks.items() if not v],
                },
                ensure_ascii=False,
            )
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=["feature", "main"], default="feature")
    parser.add_argument("--auth", default=".local/m16/auth.json")
    parser.add_argument("--budget-state", default=".local/m16/session-budget.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    if not 0 < args.timeout <= 3600:
        parser.error("Timeout must be within 0..3600 seconds")
    try:
        return asyncio.run(run(args))
    except (ConfigurationError, OSError) as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
