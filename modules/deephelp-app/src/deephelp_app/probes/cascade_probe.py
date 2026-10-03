"""Real HTTP content acceptance for the single M12 conversation graph."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.conversation import STAGES
from deephelp_app.domain.models import Outcome, QuestionStatus, ResponseEnvelope
from deephelp_app.errors import ConfigurationError
from deephelp_app.evaluation.mvp_acceptance import api_client
from deephelp_app.event_cluster import PersistentEventAggregation
from deephelp_app.learning.event_replay import envelope
from deephelp_app.local_paths import local_path
from deephelp_app.mvp_runtime import (
    BudgetSession,
    LiveAssembly,
    LocalAuth,
    live_app,
    validate_control_paths,
)
from deephelp_app.tool_gateway import process_alive


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        raise ConfigurationError("M12 cloud content acceptance requires --live")
    output, state = local_path(args.output), local_path(args.budget_state)
    validate_control_paths(
        [state, Path(args.auth), Path(args.pointer), Path(args.providers)], output=output
    )
    if output.exists():
        raise ConfigurationError("Use a new M12 report path")
    nonce = uuid4().hex[:16]
    collection = "dh_m10_events_m12_probe_" + nonce
    assembly = LiveAssembly(
        Path.cwd(), Path(args.providers), Path(args.pointer), event_collection=collection
    )
    auth, gate = LocalAuth(Path(args.auth)), BudgetSession(state)
    gate.open()
    app = live_app(assembly, auth, gate, str(output.with_suffix(".trace.jsonl")))
    report: dict[str, Any] = {
        "status": "PENDING",
        "stage": args.stage,
        "checks": {},
        "turns": [],
        "sessions": [],
        "collection": collection,
    }
    checks = report["checks"]
    requests: list[tuple[dict[str, Any], ResponseEnvelope]] = []

    def save() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        async with asyncio.timeout(1200), app.router.lifespan_context(app):
            service = app.state.resources.conversation
            assert service and service.events and assembly.ledger and assembly.tools
            ledger_repo = assembly.ledger
            identity = envelope("x", "x").identity
            token = next(t for t, i in auth.rows if i == identity)
            headers = {"Authorization": "Bearer " + token}
            async with api_client(app, http=True) as client:

                async def ask(name: str, text: str, session: str, **extra: Any) -> ResponseEnvelope:
                    body = dict(
                        channel="m12-accept",
                        message_id="m12-" + uuid4().hex,
                        session_id=session,
                        raw_text=text,
                        occurred_at=datetime.now(UTC).isoformat(),
                        **extra,
                    )
                    reply = await client.post("/converse", headers=headers, json=body)
                    row = ResponseEnvelope.model_validate(reply.json())
                    report["turns"].append(
                        {
                            "name": name,
                            "http_status": reply.status_code,
                            "response": row.model_dump(mode="json"),
                            "request": body,
                        }
                    )
                    checks[name + ":http"] = reply.status_code == 200
                    checks[name + ":13_nodes"] = tuple(s.stage for s in row.stages) == STAGES
                    checks[name + ":terminal_nodes"] = all(
                        s.status == "completed" for s in row.stages[-2:]
                    )
                    checks[name + ":at_most_one_main"] = (
                        sum(s.intent_calls for s in row.stages) <= 1
                    )
                    checks[name + ":current_request_counts"] = (
                        row.call_counts.intent_calls == sum(s.intent_calls for s in row.stages)
                        and row.call_counts.model_calls == sum(s.model_calls for s in row.stages)
                        and row.call_counts.retrieval_calls
                        == sum(s.retrieval_calls for s in row.stages)
                        and row.call_counts.tool_calls == len(row.tool_call_ids)
                    )
                    checks[name + ":persisted_event"] = bool(
                        row.event_cluster and row.event_cluster.persisted
                    )
                    q = await ledger_repo.question(identity, session, row.question_id or "none")
                    checks[name + ":mysql_terminal"] = bool(q and q.status == row.question_status)
                    if row.missing_slots or row.outcome in {Outcome.CLARIFY, Outcome.HANDOFF}:
                        checks[name + ":no_unsafe_tools"] = not row.tool_call_ids or bool(row.facts)
                    requests.append((body, row))
                    save()
                    return row

                for name, text, evidence in (
                    ("discount", "订单000031未享受优惠，请查一下", ("99.90", "10.00")),
                    ("coupon", "订单000042的券000009不能用", ("usable", "000009")),
                    ("activity", "查询订单000053参加的活动", ("ACTIVITY",)),
                ):
                    session = "m12-" + nonce + "-" + name
                    report["sessions"].append(session)
                    row = await ask(name, text, session)
                    checks[name + ":facts"] = row.outcome == Outcome.ANSWERED and all(
                        s in row.reply for s in evidence
                    )
                    checks[name + ":main_once"] = sum(s.intent_calls for s in row.stages) == 1
                    checks[name + ":rule_layer"] = bool(
                        row.intent_decision and row.intent_decision.cascade_steps[0].layer == "rule"
                    )

                session = "m12-" + nonce + "-interleaved"
                report["sessions"].append(session)
                a = await ask("a_missing", "我的订单未享受优惠", session)
                b = await ask("b_missing", "另外订单000053的券不能用", session)
                checks["two_open_questions"] = (
                    a.question_id != b.question_id
                    and a.question_status == b.question_status == QuestionStatus.WAITING_SLOT
                )
                ambiguous = await ask("ambiguous", "补充：刚才那个问题", session)
                checks["ambiguous_stops_600"] = (
                    ambiguous.outcome == Outcome.CLARIFY
                    and not ambiguous.tool_call_ids
                    and sum(s.intent_calls for s in ambiguous.stages) == 0
                )
                corrected = await ask("b_correction", "更正刚才的券问题：订单000042", session)
                checks["correction_target"] = (
                    corrected.question_id == b.question_id and not corrected.tool_call_ids
                )
                q = await assembly.ledger.question(identity, session, b.question_id or "none")
                checks["correction_chain"] = bool(
                    q
                    and q.conflicts
                    and q.conflicts[-1].previous.value == "000053"
                    and q.conflicts[-1].replacement.value == "000042"
                    and q.conflicts[-1].resolution == "explicit_correction"
                )
                done_b = await ask("b_slot", "补充：券000009", session)
                checks["auto_b_content"] = (
                    done_b.question_id == b.question_id
                    and done_b.outcome == Outcome.ANSWERED
                    and "000042" in done_b.reply
                    and "usable" in done_b.reply
                )
                done_a = await ask("a_slot", "刚才优惠那单，订单000031", session)
                checks["auto_a_content"] = (
                    done_a.question_id == a.question_id
                    and done_a.outcome == Outcome.ANSWERED
                    and "99.90" in done_a.reply
                )
                checks["auto_keep_pinned_intents"] = all(
                    r.intent_decision
                    and r.intent_decision.reason_code == "confirmed_event_context"
                    and sum(s.intent_calls for s in r.stages) == 1
                    for r in (corrected, done_b, done_a)
                )

                # A real M11 predecessor event has entities/members but no primary intent yet.
                memory_session = "m12-" + nonce + "-memory"
                report["sessions"].append(memory_session)
                seeded = await PersistentEventAggregation(assembly.ledger, service.events).process(
                    envelope("请查询订单000053参加的促销活动及活动名称", memory_session),
                    gate.request(),
                )
                memory = await ask("memory_cascade", "补充：请继续查询", memory_session)
                checks["memory_attached"] = memory.question_id == seeded.question_id
                checks["memory_content"] = (
                    memory.outcome == Outcome.ANSWERED and "ACTIVITY" in memory.reply
                )
                checks["memory_retrieval_stage"] = bool(
                    memory.intent_decision
                    and any(s.layer == "memory" for s in memory.intent_decision.cascade_steps)
                )
                checks["memory_retrieval_takeover"] = bool(
                    memory.intent_decision
                    and memory.intent_decision.reason_code == "memory_retrieval_selected"
                    and memory.intent_decision.cascade_steps[-1].layer == "memory"
                )
                checks["memory_main_once"] = sum(s.intent_calls for s in memory.stages) == 1

                for name, text, expected in (
                    ("current_layer", "SKU-A7 订单00123456 满减未减免", Outcome.ERROR),
                    ("unknown", "今天天气怎么样", Outcome.HANDOFF),
                    ("fallback", "请核查订单000031，结算时该少付的金额一分没少", Outcome.ANSWERED),
                    ("multiple", "订单000031优惠没到账；订单000042券不能用", Outcome.CLARIFY),
                    ("orphan", "补充订单000031", Outcome.CLARIFY),
                ):
                    session = "m12-" + nonce + "-" + name
                    report["sessions"].append(session)
                    row = await ask(name, text, session)
                    checks[name + ":outcome"] = row.outcome == expected
                    if name == "current_layer":
                        checks["current_layer:takeover"] = bool(
                            row.intent_decision
                            and row.intent_decision.reason_code == "current_retrieval_selected"
                        )
                        checks["current_layer:ownership_guard"] = (
                            not row.facts and row.error is not None
                        )
                    if name in {"unknown", "fallback"}:
                        checks[name + ":fallback_layer"] = bool(
                            row.intent_decision
                            and row.intent_decision.cascade_steps[-1].layer == "fallback"
                        )
                    if name == "fallback":
                        checks["fallback:registered_discount"] = bool(
                            row.intent_decision
                            and row.intent_decision.final_code
                            and row.intent_decision.final_code.value == "DISCOUNT_MISSING"
                        )
                        checks["fallback:amounts"] = all(v in row.reply for v in ("99.90", "10.00"))

                body, original = requests[
                    -3
                ]  # Fallback business result, then terminal replays below.
                tools_before = len(await assembly.tools.ledger())
                models_before = len(assembly.gateway.diagnostics) + len(
                    assembly.judge_gateway.diagnostics
                )
                for index in {0, 3, len(requests) - 3}:
                    body, original = requests[index]
                    replay = ResponseEnvelope.model_validate(
                        (await client.post("/converse", headers=headers, json=body)).json()
                    )
                    checks[f"replay:{index}"] = (
                        replay.replayed
                        and replay.run_id == original.run_id
                        and replay.budget_used.attempts == 0
                        and replay.event_cluster == original.event_cluster
                        and not any(replay.call_counts.model_dump().values())
                    )
                checks["replay_no_external_calls"] = tools_before == len(
                    await assembly.tools.ledger()
                ) and models_before == len(assembly.gateway.diagnostics) + len(
                    assembly.judge_gateway.diagnostics
                )
                forbidden = await client.post(
                    "/converse",
                    headers=headers,
                    json={**body, "message_id": uuid4().hex, "question_hint": "not-owned"},
                )
                checks["foreign_hint_rejected"] = (
                    forbidden.status_code == 403 and forbidden.json()["outcome"] == "REJECTED"
                )
                ledger = await assembly.tools.ledger()
                checks["actual_tool_ids"] = {c for _, r in requests for c in r.tool_call_ids} == {
                    r.call_id for r in ledger
                }
                checks["verified_facts_have_evidence"] = all(
                    r.evidence_refs and all(f.evidence_ids for f in r.facts)
                    for _, r in requests
                    if r.outcome == Outcome.ANSWERED
                )
                report["tool_ledger"] = [r.model_dump(mode="json") for r in ledger]
                report["diagnostics"] = assembly.gateway.diagnostics
                report["judge_diagnostics"] = assembly.judge_gateway.diagnostics
            assert assembly.event_index
            event_index = assembly.event_index
            await event_index.rpc(
                lambda: event_index.client.drop_collection(collection, timeout=None, retry_times=0)
            )
            checks["probe_collection_deleted"] = True
        assert assembly.tools
        checks["mcp_exit"] = assembly.tools.pid is not None and not process_alive(
            assembly.tools.pid
        )

        # New pools/client processes re-read terminal events without model or tool execution.
        from deephelp_app.cases import MySQLCaseRepository

        repo = await MySQLCaseRepository.open(Path.cwd())
        try:
            for body, original in requests:
                q = await repo.question(
                    identity, body["session_id"], original.question_id or "none"
                )
                checks["new_pool:" + body["message_id"]] = (
                    bool(q and q.status == original.question_status)
                    if original.question_id not in {a.question_id, b.question_id}
                    else bool(q and q.status == QuestionStatus.RESOLVED)
                )
        finally:
            await repo.aclose()
        report["status"] = "PASS" if all(checks.values()) else "FAIL"
        save()
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "checks": len(checks),
                    "failed": [k for k, v in checks.items() if not v],
                    "turns": len(requests),
                },
                ensure_ascii=False,
            )
        )
        return 0 if report["status"] == "PASS" else 1
    except Exception as exc:
        report.update(status="FAIL", error=type(exc).__name__)
        save()
        raise
    finally:
        gate.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="M12 full graph real content acceptance")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=("feature", "main"), default="feature")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--auth", default=".local/m08/auth.json")
    parser.add_argument("--budget-state", required=True)
    parser.add_argument("--output", required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
