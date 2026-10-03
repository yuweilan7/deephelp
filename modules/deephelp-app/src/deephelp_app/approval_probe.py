"""M15 real MySQL/HTTP/process failure matrix and one real model conversation.

Requires --live. Fixed-action MCP preparations test the recovery protocol, while
the separately reported HTTP conversation uses the configured real model.
"""

import argparse
import asyncio
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.approval import ApprovalService
from deephelp_app.approval_store import ApprovalRepository
from deephelp_app.dense import atomic_json
from deephelp_app.domain.checks import response_from_sop
from deephelp_app.domain.models import (
    ApprovalCommand,
    ApprovalStatus,
    Entity,
    EntityName,
    EntitySource,
    IntentCode,
    ModelToolCall,
    OperationRecord,
    OperationStatus,
    Outcome,
    QuestionStatus,
    ResponseEnvelope,
    ResumeCommand,
    RunStatus,
    VersionManifest,
)
from deephelp_app.errors import AppError
from deephelp_app.event_replay import envelope
from deephelp_app.execution import ExecutionBudget
from deephelp_app.live_probe import local_path
from deephelp_app.mvp_acceptance import api_client
from deephelp_app.mvp_runtime import BudgetSession, LiveAssembly, LocalAuth, live_app
from deephelp_app.sop import SOPExecutor
from deephelp_app.sop_acceptance import proposal_registry
from deephelp_app.sop_governance import RegistryStore
from deephelp_app.sop_replay import ReplayModel
from deephelp_app.synthetic_rights import RightsClient
from deephelp_app.tool_gateway import ToolGateway


def decision(op: OperationRecord, kind: str = "approve") -> ApprovalCommand:
    return ApprovalCommand.model_validate(
        {
            "session_id": op.session_id,
            "run_id": op.run_id,
            "expected_question_version": op.question_version,
            "parameters_hash": op.plan.parameters_hash,
            "sop_version": op.plan.sop_version,
            "snapshot_hash": op.plan.snapshot_hash,
            "decision": kind,
        }
    )


def resume_command(op: OperationRecord) -> ResumeCommand:
    return ResumeCommand(session_id=op.session_id, run_id=op.run_id)


async def prepare(repo: ApprovalRepository, session: str) -> OperationRecord:
    request = envelope("订单 DEMO-D01 优惠未到账，请查询", session)
    receipt = await repo.accept(request)
    registry = proposal_registry()
    q = receipt.question.model_copy(
        update={
            "active_intent": IntentCode.DISCOUNT_MISSING,
            "versions": registry.pin(
                VersionManifest(registry="complaints-v1"), IntentCode.DISCOUNT_MISSING
            ),
            "entities": (
                Entity(
                    name=EntityName.ORDER_ID,
                    value="DEMO-D01",
                    source=EntitySource(message_id=request.message_id, excerpt="DEMO-D01"),
                ),
            ),
        }
    )
    budget = ExecutionBudget.start(60, 12, 0)
    model = ReplayModel(
        [
            ModelToolCall(
                call_id=uuid4().hex, name="get_order_benefits", arguments={"order_id": "DEMO-D01"}
            )
        ]
    )
    gateway = ToolGateway()
    async with gateway.open():
        result = await SOPExecutor(model, gateway, registry=registry).execute(
            q,
            budget,
            context=request,
            run_id=receipt.run_id,
        )
        ledger = await gateway.ledger()
    assert result.plan and ledger[0].status == "succeeded"
    response = response_from_sop(
        request,
        run_id=receipt.run_id,
        question_id=q.question_id,
        result=result,
        reply="合成预检等待明确审批。",
        versions=q.versions,
        budget_used=budget.usage(),
    ).model_copy(
        update={
            "outcome": Outcome.PENDING_APPROVAL,
            "question_status": QuestionStatus.WAITING_APPROVAL,
            "run_status": RunStatus.WAITING_APPROVAL,
            "approval_operation_id": result.plan.operation_id,
        }
    )
    q = q.model_copy(
        update={
            "status": QuestionStatus.WAITING_APPROVAL,
            "version": q.version + 1,
            "updated_at": datetime.now(UTC),
            "approval_operation_id": result.plan.operation_id,
        }
    )
    await repo.finish(receipt, q, ResponseEnvelope.model_validate(response.model_dump()))
    return await repo.get(result.plan.operation_id)


async def counts(repo: ApprovalRepository, operation_id: str) -> dict[str, int]:
    async with repo.pool.acquire() as conn, conn.cursor() as cursor:
        await cursor.execute(
            "SELECT COUNT(*) FROM dh_m15_synthetic_effects WHERE operation_id=%s", (operation_id,)
        )
        effect_count = int((await cursor.fetchone())[0])
        await cursor.execute(
            "SELECT kind,COUNT(*) FROM dh_m15_synthetic_calls WHERE operation_id=%s GROUP BY kind",
            (operation_id,),
        )
        calls = dict(await cursor.fetchall())
        await conn.rollback()
    return {
        "effects": effect_count,
        "execute_calls": int(calls.get("execute", 0)),
        "query_calls": int(calls.get("query", 0)),
    }


async def worker(args: argparse.Namespace) -> None:
    repo = await ApprovalRepository.open(Path.cwd())
    # A short, real lease expiry keeps the owned crash matrix bounded.
    repo.lease_seconds = 8
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
            service = ApprovalService(
                repo,
                RightsClient(client, args.port, args.key.read_bytes()),
                proposal_registry().snapshot_hash,
                fault=lambda stage: os._exit(87) if stage == args.fault else None,
            )
            op = await repo.get(args.operation)
            budget = ExecutionBudget.start(60, 8, 0)
            if args.action == "pause":
                await service.pause(op.plan.operation_id, budget)
            elif args.action == "approve":
                await service.decide(op.plan.identity, op.plan.operation_id, decision(op))
            else:
                result = await service.resume(
                    op.plan.identity, op.plan.operation_id, resume_command(op), budget
                )
                if result.status in {OperationStatus.SUCCEEDED, OperationStatus.CANCELLED}:
                    assert not (await service.graph(budget).aget_state(service.config(result))).next
    finally:
        await repo.aclose()


async def spawn_worker(
    args: argparse.Namespace, operation: str, action: str, fault: str = ""
) -> int:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "deephelp_app.approval_probe",
        "worker",
        "--operation",
        operation,
        "--action",
        action,
        "--port",
        str(args.port),
        "--key",
        str(args.key),
        "--fault",
        fault,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        async with asyncio.timeout(75):
            _, error = await process.communicate()
        code = process.returncode
        assert code is not None
        if code not in {0, 87}:
            raise RuntimeError(error.decode(errors="replace")[-3000:])
        return code
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def start_rights(args: argparse.Namespace, fault_path: Path) -> asyncio.subprocess.Process:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "deephelp_app.synthetic_rights",
        "--port",
        str(args.port),
        "--key",
        str(args.key),
        "--fault-path",
        str(fault_path),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=1) as client:
            for _ in range(100):
                if process.returncode is not None:
                    raise RuntimeError("Owned synthetic rights process exited at startup")
                try:
                    if (
                        await client.get(f"http://127.0.0.1:{args.port}/health")
                    ).status_code == 200:
                        return process
                except httpx.TransportError:
                    pass
                await asyncio.sleep(0.1)
        raise RuntimeError("Synthetic service startup timed out")
    except BaseException:
        await stop(process)
        raise


async def stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
    await process.wait()


async def real_http(args: argparse.Namespace, report: dict[str, Any]) -> None:
    gate = BudgetSession(args.budget_state)
    gate.open()
    auth = LocalAuth(local_path(args.auth))
    directory = args.output.parent / (args.output.stem + "-sop")
    RegistryStore(directory).publish(proposal_registry())
    collection = "dh_m10_events_m15_" + uuid4().hex[:12]
    assembly = LiveAssembly(
        Path.cwd(),
        Path(args.providers),
        local_path(args.pointer),
        sop_directory=directory,
        event_collection=collection,
        rights_port=args.port,
        rights_key=args.key,
    )
    app = live_app(assembly, auth, gate, str(args.output.with_suffix(".trace.jsonl")))
    headers = {"Authorization": "Bearer " + auth.rows[0][0]}
    try:
        async with app.router.lifespan_context(app), api_client(app, http=True) as api:
            request = envelope("订单 DEMO-D01 优惠未到账，请查询", "m15-http-" + uuid4().hex)
            body = request.model_dump(
                mode="json",
                include=set(request.__class__.model_fields)
                - {"schema_version", "identity", "request_id", "trace_id", "received_at"},
            )
            response = await api.post("/converse", json=body, headers=headers)
            value = ResponseEnvelope.model_validate(response.json())
            report["real_http"] = {
                "http_status": response.status_code,
                "response": value.model_dump(mode="json"),
            }
            atomic_json(args.output, report)
            assert response.status_code == 200 and value.outcome == Outcome.PENDING_APPROVAL
            service = app.state.resources.conversation.approvals
            op = await service.repo.get(value.approval_operation_id)
            path = "/operations/" + op.plan.operation_id
            checks = report["checks"]
            unauthorized = await api.post(
                path + "/approval", json=decision(op).model_dump(mode="json")
            )
            checks["approval_authentication"] = unauthorized.status_code == 401
            tampered = decision(op).model_dump(mode="json") | {"parameters_hash": "tampered"}
            checks["http_parameter_binding"] = (
                await api.post(path + "/approval", json=tampered, headers=headers)
            ).status_code == 409
            # A plain new message is accepted normally and cannot resume the old operation.
            ordinary = body | {"message_id": uuid4().hex, "raw_text": "好的"}
            ack = await api.post("/converse", json=ordinary, headers=headers)
            checks["ordinary_message_does_not_approve"] = (
                await service.repo.get(op.plan.operation_id)
            ).approval_status == ApprovalStatus.PENDING and ack.status_code == 200
            decided = await api.post(
                path + "/approval", json=decision(op).model_dump(mode="json"), headers=headers
            )
            checks["pending_no_effect"] = (await counts(service.repo, op.plan.operation_id))[
                "effects"
            ] == 0
            hinted = ordinary | {
                "message_id": uuid4().hex,
                "question_hint": op.plan.question_id,
                "expected_question_version": op.question_version,
                "raw_text": "更正为订单 000031",
            }
            checks["pending_correction_blocked"] = (
                await api.post(
                    "/converse",
                    json=hinted,
                    headers=headers,
                )
            ).status_code == 409
            memory = app.state.resources.conversation.memory
            window = await memory.load(op.plan.identity, op.session_id, gate.request())
            cache = memory.cache
            cache_key = cache.key(op.plan.identity, op.session_id, window.generation)
            await cache.client.delete(cache_key, cache_key + ":keywords")
            checks["isolated_cache_deleted"] = not await cache.client.exists(cache_key)
            index = assembly.event_index
            assert index is not None
            await index.rpc(
                partial(index.client.drop_collection, collection, timeout=None, retry_times=0)
            )
            checks["isolated_milvus_projection_deleted"] = not await index.rpc(
                partial(index.client.has_collection, collection, timeout=None, retry_times=0)
            )
            async with service.repo.pool.acquire() as conn, conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT COUNT(*) FROM dh_m10_outbox WHERE question_id=%s AND done=FALSE",
                    (op.plan.question_id,),
                )
                checks["projection_lag_observed"] = (await cursor.fetchone())[0] >= 1
                await conn.rollback()
            checks["http_explicit_approval"] = (
                decided.status_code == 200 and decided.json()["approval_status"] == "APPROVED"
            )
            resumed = await api.post(
                path + "/resume", json=resume_command(op).model_dump(mode="json"), headers=headers
            )
            final = OperationRecord.model_validate(resumed.json())
            checks["http_effect_and_fact"] = (
                resumed.status_code == 200
                and final.status == OperationStatus.SUCCEEDED
                and bool(
                    final.response
                    and any(
                        f.name == "synthetic_adjustment_status" and f.value == "applied"
                        for f in final.response.facts
                    )
                )
            )
            repeated = await api.post(
                path + "/resume", json=resume_command(op).model_dump(mode="json"), headers=headers
            )
            checks["http_resume_replay"] = repeated.status_code == 200 and await counts(
                service.repo, op.plan.operation_id
            ) == {"effects": 1, "execute_calls": 1, "query_calls": 0}
            replay = ResponseEnvelope.model_validate(
                (await api.post("/converse", json=body, headers=headers)).json()
            )
            checks["original_receipt_replays_final"] = (
                replay.replayed and replay.outcome == Outcome.ANSWERED
            )
            view = await api.get(
                f"/debug/runs/{op.run_id}/flow",
                params={"session_id": op.session_id},
                headers=headers,
            )
            checks["m16_current_approval_view"] = (
                view.status_code == 200 and view.json()["data"]["approval"]["status"] == "SUCCEEDED"
            )
            report["real_http"]["final"] = final.model_dump(mode="json")
            atomic_json(args.output, report)
            assert all(checks.values()), checks
    finally:
        gate.close()


async def matrix(args: argparse.Namespace) -> None:
    if not args.live:
        raise ValueError("This probe requires --live; ordinary pytest remains offline")
    if args.output.exists():
        raise ValueError("Use a new report path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.key.exists():
        args.key.write_bytes(os.urandom(32))
    if not args.budget_state.exists():
        atomic_json(
            args.budget_state,
            {
                "max_calls": 500,
                "max_tokens": 2000000,
                "max_cost_cny": "50",
                "attempts": 0,
                "tokens": 0,
                "charged_tokens": 0,
                "cost_upper_cny": "0",
                "uncertain_attempts": 0,
                "stages": {},
            },
        )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        args.port = sock.getsockname()[1]
    faults = args.output.with_suffix(".faults.json")
    atomic_json(faults, {})
    server = await start_rights(args, faults)
    repo = await ApprovalRepository.open(Path.cwd())
    report: dict[str, Any] = {"status": "RUNNING", "stage": args.stage, "checks": {}, "matrix": []}
    try:
        await repo.migrate()
        await real_http(args, report)
        for name, stage in (
            ("before_interrupt", "before_checkpoint"),
            ("approval_checkpoint_gap", "after_decision"),
            ("before_tool", "before_tool"),
            ("effect_before_ledger", "after_tool"),
            ("ledger_before_checkpoint", "after_business_commit"),
            ("lost_tool_response", "lost_response"),
            ("downstream_process_crash", "crash_after_effect"),
            ("definite_absence", "before_effect"),
        ):
            op = await prepare(repo, "m15-" + name + "-" + uuid4().hex)
            if name == "before_interrupt":
                assert await spawn_worker(args, op.plan.operation_id, "pause", stage) == 87
            else:
                assert await spawn_worker(args, op.plan.operation_id, "pause") == 0
            if name == "approval_checkpoint_gap":
                assert await spawn_worker(args, op.plan.operation_id, "approve", stage) == 87
            else:
                assert await spawn_worker(args, op.plan.operation_id, "approve") == 0
            if stage in {"before_tool", "after_tool", "after_business_commit"}:
                assert await spawn_worker(args, op.plan.operation_id, "resume", stage) == 87
                await asyncio.sleep(8)  # real lease expiry, no test rewrites of the operation lease
            elif stage in {"lost_response", "crash_after_effect", "before_effect"}:
                atomic_json(faults, {op.plan.operation_id: stage})
                assert await spawn_worker(args, op.plan.operation_id, "resume") == 0
                uncertain = await repo.get(op.plan.operation_id)
                assert uncertain.status == OperationStatus.UNKNOWN
                if stage == "crash_after_effect":
                    assert await server.wait() == 86
                    atomic_json(faults, {})
                    server = await start_rights(args, faults)
                else:
                    atomic_json(faults, {})
            assert await spawn_worker(args, op.plan.operation_id, "resume") == 0
            final = await repo.get(op.plan.operation_id)
            before_replay = await counts(repo, op.plan.operation_id)
            assert await spawn_worker(args, op.plan.operation_id, "resume") == 0
            after_replay = await counts(repo, op.plan.operation_id)
            row = {
                "case": name,
                "operation_id": op.plan.operation_id,
                "status": final.status.value,
                **after_replay,
                "history": await repo.history(op.plan.operation_id),
            }
            report["matrix"].append(row)
            atomic_json(args.output, report)
            assert final.status == OperationStatus.SUCCEEDED and final.response
            assert after_replay == before_replay and after_replay["effects"] == 1
            assert after_replay["execute_calls"] == (2 if stage == "before_effect" else 1)
            if stage in {
                "before_tool",
                "after_tool",
                "lost_response",
                "crash_after_effect",
                "before_effect",
            }:
                assert after_replay["query_calls"] >= 1
        async with httpx.AsyncClient(trust_env=False) as client:
            service = ApprovalService(
                repo,
                RightsClient(client, args.port, args.key.read_bytes()),
                proposal_registry().snapshot_hash,
            )
            for kind in ("reject", "revoke", "expired"):
                op = await prepare(repo, "m15-" + kind + "-" + uuid4().hex)
                if kind == "revoke":
                    await service.decide(op.plan.identity, op.plan.operation_id, decision(op))
                if kind == "expired":
                    async with repo.locked(op.plan.operation_id) as (cursor, current, q, lease):
                        current = current.model_copy(
                            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
                        )
                        await repo.save(cursor, current, "probe_expiry")
                        await cursor.execute(
                            "UPDATE dh_m15_operations SET expires_at=%s WHERE operation_id=%s",
                            (current.expires_at.replace(tzinfo=None), current.plan.operation_id),
                        )
                final = await service.decide(
                    op.plan.identity,
                    op.plan.operation_id,
                    decision(op, "approve" if kind == "expired" else kind),
                )
                stats = await counts(repo, op.plan.operation_id)
                report["checks"][kind + "_no_effect"] = (
                    final.status == OperationStatus.CANCELLED
                    and stats["effects"] == stats["execute_calls"] == 0
                )
            op = await prepare(repo, "m15-concurrent-" + uuid4().hex)
            decisions = await asyncio.gather(
                *[
                    service.decide(op.plan.identity, op.plan.operation_id, decision(op))
                    for _ in range(2)
                ]
            )
            report["checks"]["concurrent_approvers"] = (
                all(d.approval_status == ApprovalStatus.APPROVED for d in decisions)
                and len(
                    [h for h in await repo.history(op.plan.operation_id) if h["kind"] == "approved"]
                )
                == 1
            )
            results = await asyncio.gather(
                *[
                    service.resume(
                        op.plan.identity,
                        op.plan.operation_id,
                        resume_command(op),
                        ExecutionBudget.start(60, 8, 0),
                    )
                    for _ in range(2)
                ],
                return_exceptions=True,
            )
            report["checks"]["concurrent_resume_unique_effect"] = (
                any(
                    isinstance(r, OperationRecord) and r.status == OperationStatus.SUCCEEDED
                    for r in results
                )
                and (await counts(repo, op.plan.operation_id))["effects"] == 1
            )
            # All three dimensions are checked against the same persisted operation.
            for field in ("tenant_id", "user_id", "session_id", "run_id"):
                identity = (
                    op.plan.identity.model_copy(update={field: "foreign"})
                    if field in {"tenant_id", "user_id"}
                    else op.plan.identity
                )
                try:
                    await repo.scoped(
                        identity,
                        "foreign" if field == "session_id" else op.session_id,
                        "foreign" if field == "run_id" else op.run_id,
                        op.plan.operation_id,
                    )
                except AppError as exc:
                    report["checks"][field + "_isolation"] = exc.status_code == 404
                else:
                    report["checks"][field + "_isolation"] = False
        assert all(report["checks"].values()), report["checks"]
        report["status"] = "PASS"
    except BaseException:
        report["status"] = "FAIL"
        raise
    finally:
        atomic_json(args.output, report)
        await repo.aclose()
        await stop(server)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="accept", choices=["accept", "worker"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--stage", choices=["feature", "main"], default="feature")
    parser.add_argument("--output", type=local_path, default=Path(".local/m15/feature.json"))
    parser.add_argument("--budget-state", type=local_path, default=Path(".local/m15/budget.json"))
    parser.add_argument("--key", type=local_path, default=Path(".local/m15/rights.key"))
    parser.add_argument("--auth", default=".local/m08/auth.json")
    parser.add_argument("--pointer", default=".local/m09/active.json")
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--operation")
    parser.add_argument("--action", choices=["pause", "approve", "resume"], default="resume")
    parser.add_argument("--fault", default="")
    args = parser.parse_args()
    asyncio.run(worker(args) if args.command == "worker" else matrix(args))


if __name__ == "__main__":
    main()
