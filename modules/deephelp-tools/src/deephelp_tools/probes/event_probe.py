"""Real model/Milvus/MySQL/Redis acceptance on frozen M11 synthetic sequences."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.adapters.cases import MySQLCaseRepository
from deephelp_app.adapters.providers import EndpointConfig, ProviderConfig
from deephelp_app.application.event_cluster import PersistentEventAggregation
from deephelp_app.bootstrap.event_runtime import live_events
from deephelp_app.bootstrap.local_paths import local_path
from deephelp_app.bootstrap.mvp_runtime import BudgetSession, validate_control_paths
from deephelp_app.domain.errors import AppError, ConfigurationError
from deephelp_app.domain.models import (
    ErrorCode,
    LifecycleCommand,
    QuestionStatus,
    RequestEnvelope,
    VerifiedIdentity,
)
from deephelp_tools.learning.event_replay import envelope, sequences


async def storage_checks(
    events: PersistentEventAggregation,
    repo: MySQLCaseRepository,
    gate: BudgetSession,
    checks: dict[str, bool],
    nonce: str,
) -> None:
    session = "m11-storage-" + nonce[:16]
    request = envelope("订单900001优惠没到账", session)
    response = await events.process(request, gate.request())
    assert response.question_id is not None
    q = await repo.question(request.identity, session, response.question_id)
    assert q is not None
    continuation = envelope("补充订单900001", session)
    await repo.transition(
        q.identity,
        q.question_id,
        LifecycleCommand(
            session_id=session,
            expected_version=q.version,
            target=QuestionStatus.WAITING_SLOT,
            reason="合成并发版本变更",
            evidence_source="slot_check",
            evidence_ref="m11-storage-cas",
        ),
    )
    try:
        await repo.accept(continuation, attribution=(q.question_id, q.version))
        checks["attribution_cas_reject"] = False
    except AppError as exc:
        checks["attribution_cas_reject"] = exc.code == ErrorCode.VERSION_CONFLICT
    checks["cas_rollback_no_message"] = await repo.lookup(continuation) is None
    current = await repo.question(request.identity, session, q.question_id)
    assert current is not None
    checks["cas_members_unchanged"] = current.member_message_ids == q.member_message_ids
    for label, changes in (
        ("tenant", {"identity": VerifiedIdentity(tenant_id="other", user_id="synthetic-user-a")}),
        ("user", {"identity": VerifiedIdentity(tenant_id="synthetic-tenant", user_id="other")}),
        ("session", {"session_id": "other"}),
    ):
        unauthorized = envelope("补充订单900001", session, **changes)
        try:
            await repo.accept(unauthorized, attribution=(q.question_id, current.version))
            checks["transaction_scope:" + label] = False
        except AppError as exc:
            checks["transaction_scope:" + label] = exc.code == ErrorCode.FORBIDDEN
        checks["scope_rollback:" + label] = await repo.lookup(unauthorized) is None
    duplicate = envelope("另外订单900002优惠没到账", session + "-duplicates")
    results = await asyncio.gather(
        *(events.process(duplicate, gate.request()) for _ in range(6)), return_exceptions=True
    )
    successes = [r for r in results if not isinstance(r, BaseException)]
    checks["concurrent_duplicate_one_owner"] = sum(not r.replayed for r in successes) == 1
    checks["concurrent_duplicate_safe_pending"] = all(
        not isinstance(r, BaseException)
        or (isinstance(r, AppError) and r.code == ErrorCode.VERSION_CONFLICT)
        for r in results
    )
    replay = await events.process(duplicate, gate.request())
    checks["duplicate_terminal_same_run"] = replay.replayed and all(
        r.run_id == replay.run_id for r in successes
    )
    window = await repo.snapshot(duplicate.identity, duplicate.session_id)
    checks["duplicate_one_fact_member"] = (
        len(window.active_questions) == 1 and len(window.history) == 1
    )


async def run(args: argparse.Namespace) -> int:
    if not args.live:
        raise ConfigurationError("Real event acceptance requires --live")
    output, state = local_path(args.output), local_path(args.budget_state)
    validate_control_paths([state, Path(args.providers), Path(args.judge_provider)], output=output)
    if output.exists():
        raise ConfigurationError("Use a new M11 report path")
    gate = BudgetSession(state)
    gate.open()
    nonce = uuid4().hex
    collection = "dh_m10_events_m11_probe_" + nonce[:16]
    report: dict[str, Any] = {
        "status": "PENDING",
        "stage": args.stage,
        "collection": collection,
        "checks": {},
        "turns": [],
        "sessions": [],
    }
    checks = report["checks"]

    def save() -> None:
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        async with (
            asyncio.timeout(900),
            live_events(
                Path.cwd(),
                ProviderConfig.load(Path(args.providers)),
                collection=collection,
                namespace="deephelp:m10:probe:m11:" + nonce,
                judge_endpoint=EndpointConfig.model_validate_json(
                    await asyncio.to_thread(Path(args.judge_provider).read_text, encoding="utf-8")
                ),
            ) as (events, worker, index, model),
        ):
            repo = worker.repository
            aliases: dict[str, str] = {}
            for sequence in sequences():
                session = "m11-" + nonce[:16] + "-" + sequence["id"]
                report["sessions"].append(session)
                aliases.clear()
                for position, turn in enumerate(sequence["turns"]):
                    request = envelope(turn["text"], session)
                    response = await events.process(request, gate.request())
                    result = response.event_cluster
                    assert result and result.persisted
                    key = f"{sequence['id']}:{position + 1}"
                    report["turns"].append(
                        {
                            "key": key,
                            "request": request.model_dump(mode="json"),
                            "response": response.model_dump(mode="json"),
                        }
                    )
                    checks[key + ":disposition"] = result.disposition == turn["disposition"]
                    checks[key + ":events"] = len(result.events) == turn["events"]
                    checks[key + ":no_classification_or_tools"] = (
                        response.intent_decision is None and not response.tool_call_ids
                    )
                    if turn["event"] in aliases:
                        checks[key + ":membership"] = response.question_id == aliases[turn["event"]]
                    else:
                        checks[key + ":independent"] = response.question_id not in aliases.values()
                    assert response.question_id is not None
                    aliases[turn["event"]] = response.question_id
                    question = await repo.question(request.identity, session, response.question_id)
                    assert question is not None
                    values = {e.name.value: e.value for e in question.entities}
                    for name, value in turn.get("entities", {}).items():
                        checks[key + ":" + name] = values.get(name) == value
                    if turn.get("correction"):
                        checks[key + ":correction_sources"] = bool(question.conflicts) and (
                            question.conflicts[-1].previous.value == "000007"
                            and question.conflicts[-1].replacement.value == "000008"
                            and question.conflicts[-1].resolution == "explicit_correction"
                        )
                    if turn.get("waiting"):
                        question = await repo.transition(
                            question.identity,
                            question.question_id,
                            LifecycleCommand(
                                session_id=session,
                                expected_version=question.version,
                                target=QuestionStatus.WAITING_SLOT,
                                reason="合成缺槽证据",
                                evidence_source="slot_check",
                                evidence_ref="m11-synthetic-slots",
                            ),
                        )
                        checks[key + ":waiting_slot_eligible"] = (
                            question.status == QuestionStatus.WAITING_SLOT
                        )
                    if result.judgement and result.disposition == "attached":
                        checks[key + ":grounded_judgement"] = (
                            result.judgement.confidence == "high"
                            and len(result.judgement.citations) >= 2
                        )
                    # Project this task's terminal event only.
                    async with repo.pool.acquire() as conn, conn.cursor() as cursor:
                        await cursor.execute(
                            "SELECT event_id FROM dh_m10_outbox "
                            "WHERE question_id=%s AND version=%s",
                            (question.question_id, question.version),
                        )
                        row = await cursor.fetchone()
                        await conn.rollback()
                    assert row is not None
                    checks[key + ":outbox_projection"] = (
                        await worker.once(gate.request(), event_id=int(row[0])) == "projected"
                    )
                    checks[key + ":replay"] = (
                        await events.process(request, gate.request())
                    ).replayed
                    if turn.get("close"):
                        await repo.transition(
                            question.identity,
                            question.question_id,
                            LifecycleCommand(
                                session_id=session,
                                expected_version=question.version,
                                target=QuestionStatus.RESOLVED,
                                reason="合成用户明确结束旧事件",
                                evidence_source="user_confirmation",
                                evidence_ref="m11-synthetic-confirmation",
                            ),
                        )
                    save()
                    if not all(checks.values()):
                        raise AppError(
                            code=ErrorCode.MODEL_OUTPUT_INVALID,
                            message="M11 sequence content check failed",
                        )
                print(json.dumps({"sequence": sequence["id"], "checks": len(checks)}), flush=True)
            # Actual old ACTIVE vector remains; MySQL must veto it after closure.
            await storage_checks(events, repo, gate, checks, nonce)
            session = report["sessions"][-2]
            request = envelope("补充订单000007优惠问题", session)
            result = await events.service.aggregate(request, gate.request())
            checks["actual_stale_vector_veto"] = any(
                c.excluded_reason == "stale_closed_or_scope" for c in result.candidates
            )
            for changes in (
                {
                    "identity": VerifiedIdentity(
                        tenant_id="other-tenant", user_id="synthetic-user-a"
                    )
                },
                {"identity": VerifiedIdentity(tenant_id="synthetic-tenant", user_id="other-user")},
                {"session_id": "other-session"},
            ):
                isolated = await events.service.aggregate(
                    envelope("补充订单000007", session, **changes), gate.request()
                )
                checks["isolation:" + str(changes)] = (
                    not isolated.candidates and isolated.target_question_id is None
                )
            # Rebuild one latest event after deletion; membership is read from a fresh MySQL pool.
            qid = report["turns"][2]["response"]["question_id"]
            session = report["sessions"][0]
            identity = envelope("x", session).identity
            question = await repo.question(identity, session, qid)
            assert question is not None
            await index.rpc(lambda: index.client.drop_collection(collection, timeout=None))
            await index.initialize()
            await index.put(question, gate.request())
            candidates = await index.candidates(
                identity, session, "优惠券不能用", gate.request(), top_k=5
            )
            checks["delete_rebuild_from_mysql"] = any(
                c.question_id == qid and c.version == question.version for c in candidates
            )
            report["diagnostics"] = getattr(model, "diagnostics", [])
            checks["all_tools_zero"] = all(
                not t["response"]["tool_call_ids"] for t in report["turns"]
            )
            report["status"] = "PASS" if all(checks.values()) else "FAIL"
            report["metrics"] = {
                "turns": len(report["turns"]),
                "correct_attribution": sum(checks[k] for k in checks if k.endswith(":disposition")),
                "wrong_merges": sum(
                    not checks[k]
                    for k in checks
                    if k.endswith(":membership") or k.endswith(":independent")
                ),
                "wrong_event_counts": sum(not checks[k] for k in checks if k.endswith(":events")),
                "clarifications": sum(
                    t["response"]["event_cluster"]["disposition"] == "clarify"
                    for t in report["turns"]
                ),
            }
            # Keep MySQL audit; only the dedicated vector collection is removed.
            await index.rpc(lambda: index.client.drop_collection(collection, timeout=None))
        fresh = await MySQLCaseRepository.open(Path.cwd())
        try:
            original = report["turns"][2]["request"]
            receipt = await fresh.lookup(RequestEnvelope.model_validate(original))
            checks["fresh_pool_persisted_graph"] = bool(
                receipt
                and receipt.response
                and receipt.response.event_cluster
                and receipt.response.event_cluster.persisted
            )
            report["status"] = "PASS" if all(checks.values()) else "FAIL"
        finally:
            await fresh.aclose()
    except AppError as exc:
        report.update(
            status="FAIL",
            error={
                "code": exc.code,
                "message": exc.safe_message,
                "provider_request_id": exc.provider_request_id,
            },
        )
    except Exception as exc:
        report.update(status="FAIL", error={"type": type(exc).__name__})
        raise
    finally:
        report["cumulative_budget"] = gate.state
        save()
        gate.close()
    print(
        json.dumps(
            {
                "status": report["status"],
                "checks": len(checks),
                "metrics": report.get("metrics"),
                "failed": [k for k, value in checks.items() if not value],
            }
        )
    )
    return 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=["feature", "main"], default="feature")
    parser.add_argument("--budget-state", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--judge-provider", default="modules/deephelp-app/event-judge.example.json")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (AppError, ConfigurationError) as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
