"""Real complete-release CAS/crash, running-version and M15 retention/reconciliation acceptance."""

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.approval_probe import counts, decision, resume_command, start_rights, stop
from deephelp_app.approval_store import ApprovalRepository
from deephelp_app.dense import atomic_json
from deephelp_app.domain.models import ReleasePointer, ResponseEnvelope
from deephelp_app.errors import AppError, ConfigurationError
from deephelp_app.event_replay import envelope
from deephelp_app.flywheel_assets import build
from deephelp_app.flywheel_store import FeedbackStore
from deephelp_app.flywheel_validation import cleanup_event_collection, validate
from deephelp_app.live_probe import local_path
from deephelp_app.mvp_acceptance import api_client
from deephelp_app.mvp_runtime import BudgetSession, LocalAuth, live_app
from deephelp_app.release_assets import prepare_manifest, release_hash, verify_complete
from deephelp_app.release_runtime import ReleaseRuntime
from deephelp_app.release_store import ReleaseStore
from deephelp_app.sop_acceptance import proposal_registry
from deephelp_app.tool_gateway import process_alive


async def publisher(args: argparse.Namespace) -> None:
    store = await ReleaseStore.open(Path.cwd())
    try:

        def fault(stage: str) -> None:
            if stage == args.fault:
                os._exit(88)

        await store.switch(args.channel, args.release, args.expected_revision, fault=fault)
    finally:
        await store.aclose()


async def crash_publish(channel: str, target: str, revision: int, fault: str) -> int:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "deephelp_app.release_probe",
        "publisher",
        "--live",
        "--channel",
        channel,
        "--release",
        target,
        "--expected-revision",
        str(revision),
        "--fault",
        fault,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        async with asyncio.timeout(60):
            _, error = await process.communicate()
        if process.returncode != 88:
            raise ConfigurationError("Publisher failed: " + error.decode(errors="replace")[-1500:])
        return process.returncode
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def probe(args: argparse.Namespace) -> int:
    if not args.live:
        raise ConfigurationError("Complete release acceptance requires --live")
    output = local_path(args.output)
    if output.exists():
        raise ConfigurationError("Use a new immutable release acceptance directory")
    output.mkdir()
    if not args.key.exists():
        args.key.write_bytes(os.urandom(32))
    if not args.budget_state.exists():
        atomic_json(
            args.budget_state,
            dict(
                max_calls=1500,
                max_tokens=6000000,
                max_cost_cny="75",
                attempts=0,
                tokens=0,
                charged_tokens=0,
                cost_upper_cny="0",
                uncertain_attempts=0,
            ),
        )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        args.port = sock.getsockname()[1]
    faults = output / "faults.json"
    atomic_json(faults, {})
    server = await start_rights(args, faults)
    gate = BudgetSession(args.budget_state)
    gate.open()
    feedback = await FeedbackStore.open(Path.cwd())
    store = await ReleaseStore.open(Path.cwd())
    approvals = await ApprovalRepository.open(Path.cwd())
    report: dict[str, Any] = dict(
        status="RUNNING", checks={}, channel="m18-release-" + uuid4().hex[:16]
    )
    channel = report["channel"]
    runtime = None

    def save() -> None:
        atomic_json(output / "report.json", report)

    try:
        await approvals.migrate()
        await store.migrate()
        assets = []
        registries = []
        validations = []
        for letter in ("a", "b"):
            directory = (
                Path(args.prepared_assets) / letter if args.prepared_assets else output / letter
            )
            if not args.prepared_assets:
                await build(
                    feedback,
                    [],
                    directory,
                    channel + "-" + letter,
                    gate,
                    Path(args.providers),
                    Path(args.fasttext_pointer),
                )
            registry = proposal_registry().model_copy(
                update={"registry_version": channel + "-" + letter}
            )
            validation_path = output / (letter + "-validation.json")
            validation = await validate(
                feedback,
                directory / "assets.json",
                validation_path,
                gate,
                Path(args.providers),
                Path(args.auth),
                baseline=output / "a-validation.json" if letter == "b" else None,
                registry=registry,
                persist_assets_validation=False,
            )
            report["checks"][letter + "_regression"] = validation["status"] == "PASS"
            assets.append(directory / "assets.json")
            registries.append(registry)
            validations.append(validation_path)
            report["prepared_assets_reused"] = args.prepared_assets
            save()
        manifests = []
        for i, letter in enumerate(("a", "b", "c")):
            manifest = prepare_manifest(
                assets[min(i, 1)],
                Path(args.providers),
                registries[min(i, 1)],
                channel + "-" + letter,
                validation_path=validations[min(i, 1)],
            )
            await verify_complete(manifest, gate)
            identity = await store.register(manifest)
            atomic_json(output / (letter + "-release.json"), manifest.model_dump(mode="json"))
            manifests.append(manifest)
            report.setdefault("releases", {})[letter] = identity
        a, b, c = (release_hash(m) for m in manifests)
        first = await store.switch(channel, a, 0)
        report["checks"]["initial_complete_publish"] = first.revision == 1
        auth = LocalAuth(Path(args.auth))
        entered, proceed = asyncio.Event(), asyncio.Event()

        async def hold(receipt: Any) -> None:
            if receipt.question.session_id.endswith("-holding"):
                report["held_run_id"] = receipt.run_id
                entered.set()
                await proceed.wait()

        runtime = ReleaseRuntime(
            channel, rights_port=args.port, rights_key=args.key, after_accept=hold
        )
        app = live_app(runtime, auth, gate, str(output / "trace.jsonl"))
        headers = {"Authorization": "Bearer " + auth.rows[0][0]}
        async with app.router.lifespan_context(app), api_client(app, http=True) as client:

            async def send(text: str, suffix: str, **extra: Any) -> ResponseEnvelope:
                req = envelope(text, channel + "-" + suffix)
                body = (
                    req.model_dump(
                        mode="json",
                        exclude={
                            "schema_version",
                            "identity",
                            "request_id",
                            "trace_id",
                            "received_at",
                        },
                    )
                    | extra
                )
                result = await client.post("/converse", json=body, headers=headers)
                response = ResponseEnvelope.model_validate(result.json())
                report.setdefault("messages", []).append(response.model_dump(mode="json"))
                save()
                return response

            service = app.state.resources.conversation.approvals
            missing = await send("优惠券用不了，请核查", "slots")
            report["checks"]["old_missing_slots_no_tool"] = (
                missing.outcome == "CLARIFY" and not missing.tool_call_ids
            )
            operations = []
            for kind in ("pending", "approved", "unknown"):
                response = await send("订单 DEMO-D01 优惠未到账，请查询", kind)
                assert response.outcome == "PENDING_APPROVAL"
                op = await service.repo.get(response.approval_operation_id)
                operations.append(op)
                if kind != "pending":
                    await service.decide(op.plan.identity, op.plan.operation_id, decision(op))
                if kind == "unknown":
                    atomic_json(faults, {op.plan.operation_id: "lost_response"})
                    uncertain = await service.resume(
                        op.plan.identity, op.plan.operation_id, resume_command(op), gate.request()
                    )
                    atomic_json(faults, {})
                    report["checks"]["real_unknown_with_durable_effect"] = (
                        uncertain.status == "UNKNOWN"
                        and (await counts(service.repo, op.plan.operation_id))["effects"] == 1
                    )
            held = asyncio.create_task(send("订单 000031 优惠未到账，请查询", "holding"))
            try:
                async with asyncio.timeout(90):
                    await entered.wait()
                refs = await store.references(a)
                report["checks"]["running_pin_committed"] = any(
                    r["run_id"] == report["held_run_id"] and r["run_status"] == "RUNNING"
                    for r in refs
                )
                await store.switch(channel, b, 1)
            finally:
                proceed.set()
            held_response = await held
            report["checks"]["running_keeps_whole_old_release"] = (
                held_response.versions.release_manifest == a and held_response.outcome == "ANSWERED"
            )
            fresh = await send("请查订单 000053 参加的活动", "new")
            report["checks"]["new_run_uses_whole_new_release"] = (
                fresh.versions.release_manifest == b and fresh.outcome == "ANSWERED"
            )
            q = await service.repo.question(
                auth.rows[0][1], channel + "-slots", missing.question_id
            )
            assert q is not None
            follow = await send(
                "订单 000042 的优惠券 000009 不能用",
                "slots",
                question_hint=q.question_id,
                expected_question_version=q.version,
            )
            report["checks"]["continuation_keeps_all_old_assets"] = (
                follow.versions.release_manifest == a and follow.outcome == "ANSWERED"
            )
            await store.switch(channel, c, 2)
            before = await store.active(channel)
            try:
                await store.retire(a)
            except ConfigurationError:
                report["checks"]["pending_unknown_assets_retained"] = (
                    await store.active(channel)
                ) == before
            else:
                report["checks"]["pending_unknown_assets_retained"] = False
            refs = await store.references(a)
            report["references_during_wait"] = refs
            report["checks"]["waiting_and_unknown_refs_present"] = {"PREPARED", "UNKNOWN"}.issubset(
                {r["operation_status"] for r in refs}
            )
            pending, approved, uncertain = operations
            for op, action in ((pending, "approve"), (approved, "resume")):
                try:
                    if action == "approve":
                        await service.decide(op.plan.identity, op.plan.operation_id, decision(op))
                    else:
                        await service.resume(
                            op.plan.identity,
                            op.plan.operation_id,
                            resume_command(op),
                            gate.request(),
                        )
                except AppError as exc:
                    report["checks"]["changed_sop_blocks_" + action] = (
                        exc.status_code == 409
                        and (await counts(service.repo, op.plan.operation_id))["effects"] == 0
                    )
                else:
                    report["checks"]["changed_sop_blocks_" + action] = False
            final = await service.resume(
                uncertain.plan.identity,
                uncertain.plan.operation_id,
                resume_command(uncertain),
                gate.request(),
            )
            replay = await service.resume(
                uncertain.plan.identity,
                uncertain.plan.operation_id,
                resume_command(uncertain),
                gate.request(),
            )
            stats = await counts(service.repo, uncertain.plan.operation_id)
            report["reconciled_counts"] = stats
            report["checks"]["unknown_reconciles_original_effect"] = (
                final.status == replay.status == "SUCCEEDED"
                and stats == dict(effects=1, execute_calls=1, query_calls=1)
                and final.plan == uncertain.plan
                and final.response.versions.release_manifest == a
            )
            for op, kind in ((pending, "reject"), (approved, "revoke")):
                closed = await service.decide(
                    op.plan.identity, op.plan.operation_id, decision(op, kind)
                )
                report["checks"][kind + "_original_plan_unchanged"] = (
                    closed.status == "CANCELLED" and closed.plan == op.plan
                )
            concurrent = await asyncio.gather(
                store.switch(channel, b, 3), store.switch(channel, b, 3), return_exceptions=True
            )
            report["checks"]["concurrent_rollback_one_revision_winner"] = (
                sum(isinstance(x, ReleasePointer) for x in concurrent) == 1
                and sum(isinstance(x, AppError) and x.status_code == 409 for x in concurrent) == 1
            )
            idem = await store.switch(channel, b, 4)
            report["checks"]["same_release_idempotent"] = idem.revision == 4
            for point in ("before_commit", "after_commit"):
                code = await crash_publish(channel, c, 4, point)
                observed = await store.active(channel)
                report["checks"]["publisher_crash_" + point] = (
                    code == 88
                    and observed.active == (b if point == "before_commit" else c)
                    and observed.revision == (4 if point == "before_commit" else 5)
                )
                save()
            await store.switch(channel, b, 5)
            reader = await ReleaseStore.open(Path.cwd())
            try:
                report["checks"]["fresh_mysql_pool_single_authority"] = (
                    await reader.active(channel)
                ).active == b
            finally:
                await reader.aclose()
        for manifest in manifests:
            await verify_complete(manifest, gate)
        report["checks"]["all_retained_artifacts_full_readback"] = True
        report["checks"]["mcp_processes_exited"] = all(
            r.tools and r.tools.pid and not process_alive(r.tools.pid)
            for r in runtime.assemblies.values()
        )
        report["status"] = "PASS" if all(report["checks"].values()) else "REJECTED"
    except BaseException as exc:
        report["status"] = "FAILED"
        report["error"] = dict(type=type(exc).__name__, message=str(exc))
        raise
    finally:
        if runtime:
            for assembly in runtime.assemblies.values():
                if assembly.event_collection:
                    await cleanup_event_collection(assembly.event_collection)
        report["cumulative_usage"] = gate.state
        save()
        gate.close()
        await approvals.aclose()
        await store.aclose()
        await feedback.aclose()
        await stop(server)
    print(json.dumps(dict(status=report["status"], checks=report["checks"]), ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="accept", choices=("accept", "publisher"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", default=".local/m18-release/feature-v1")
    parser.add_argument(
        "--prepared-assets", help="Reuse immutable a/b builds, but rerun full regression"
    )
    parser.add_argument(
        "--budget-state", type=local_path, default=Path(".local/m18-release/budget.json")
    )
    parser.add_argument("--key", type=local_path, default=Path(".local/m15/rights.key"))
    parser.add_argument("--providers", default="modules/deephelp-app/providers.example.json")
    parser.add_argument("--fasttext-pointer", default=".local/m13/active.json")
    parser.add_argument("--auth", default=".local/m17/auth.json")
    parser.add_argument("--channel")
    parser.add_argument("--release")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--fault", default="")
    args = parser.parse_args()
    if args.command == "publisher":
        asyncio.run(publisher(args))
    else:

        async def bounded() -> int:
            async with asyncio.timeout(1800):
                return await probe(args)

        raise SystemExit(asyncio.run(bounded()))


if __name__ == "__main__":
    main()
