"""M21 fixed real MCP/MySQL/Collector actions; model quality is a separate boundary."""

import argparse
import asyncio
import json
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.adapters.approval_store import ApprovalRepository
from deephelp_app.adapters.business_client import BusinessClient
from deephelp_app.adapters.network_mcp import HTTPMCPConfig
from deephelp_app.adapters.providers import ProviderConfig
from deephelp_app.adapters.retrieval_mcp import RemoteRetriever
from deephelp_app.adapters.retrieval_protocol import binding_from_files
from deephelp_app.adapters.synthetic_rights import RightsClient
from deephelp_app.adapters.telemetry import environment, observation, trace_id
from deephelp_app.adapters.tool_gateway import ToolGateway
from deephelp_app.application.approval import ApprovalService
from deephelp_app.application.dense import atomic_json
from deephelp_app.bootstrap.mvp_runtime import BudgetSession, LocalAuth
from deephelp_app.domain.execution import ExecutionBudget, request_execution
from deephelp_app.domain.models import BusinessOrderInput, Money, OperationStatus, ToolStatus
from deephelp_tools.evaluation.sop_acceptance import proposal_registry
from deephelp_tools.probes.approval_probe import (
    counts,
    decision,
    prepare,
    resume_command,
    spawn_worker,
)
from deephelp_tools.probes.business_probe import Processes, port
from deephelp_tools.probes.mcp_smoke import synthetic_request


async def protocol(args: argparse.Namespace, report: dict[str, Any], processes: Processes) -> None:
    business_port, retrieval_port = port(), port()
    business = HTTPMCPConfig(
        url=f"http://127.0.0.1:{business_port}/mcp", credentials_path=args.business_credentials
    )
    retrieval = HTTPMCPConfig(
        url=f"http://127.0.0.1:{retrieval_port}/mcp",
        credentials_path=args.retrieval_credentials,
        audience="retrieval",
    )
    await processes.start(
        "business",
        [
            "-m",
            "deephelp_services.business",
            "serve",
            "--credentials",
            str(args.business_credentials),
            "--port",
            str(business_port),
        ],
        f"http://127.0.0.1:{business_port}/mcp",
    )
    await processes.start(
        "retrieval",
        [
            "-m",
            "deephelp_services.retrieval",
            "serve",
            "--credentials",
            str(args.retrieval_credentials),
            "--port",
            str(retrieval_port),
            "--auth",
            str(args.auth),
            "--pointer",
            str(args.pointer),
            "--corpus",
            str(args.corpus),
        ],
        f"http://127.0.0.1:{retrieval_port}/mcp",
    )
    owner = LocalAuth(args.auth).rows[0][1]
    order = "M21-" + uuid4().hex[:12]
    with observation("probe.fixed_request"):
        report["protocol_trace_id"] = trace_id()
        await BusinessClient(business).request(
            owner,
            "POST",
            "orders",
            body=BusinessOrderInput(
                order_id=order,
                paid=Money(amount=Decimal("173.45")),
                discount=Money(amount=Decimal("12.30")),
                discount_status="applied",
                sku_ids=("SKU-M21",),
            ).model_dump(mode="json"),
        )
        req, question, context = synthetic_request(
            ExecutionBudget.start(60, 10, 0),
            order_id=order,
            user=owner.user_id,
            tenant=owner.tenant_id,
        )
        async with ToolGateway(http=business).open() as tools:
            result = await tools.execute(
                req, question, ExecutionBudget.start(60, 10, 0), context=context
            )
            assert result.status == ToolStatus.SUCCEEDED
            assert next(
                f.value.amount
                for f in result.facts
                if f.name == "paid" and isinstance(f.value, Money)
            ) == Decimal("173.45")
            report["checks"]["business_real_database_facts"] = True
        binding = binding_from_files(args.pointer, ProviderConfig.load(args.providers), args.corpus)
        gate = BudgetSession(args.budget_state)
        gate.open()
        token = request_execution.set(context)
        try:
            async with RemoteRetriever(retrieval, binding).open() as remote:
                first = await remote.compare(
                    "订单承诺优惠但结账实付不一致", binding.scope, gate.request()
                )
                second = await remote.compare(
                    "订单承诺优惠但结账实付不一致", binding.scope, gate.request()
                )
                assert set(first) == {"dense", "bm25", "hybrid"}
                assert all(first[mode].candidates for mode in first)
                assert all(
                    first[mode].model_dump(exclude={"elapsed_ms"})
                    == second[mode].model_dump(exclude={"elapsed_ms"})
                    for mode in first
                )
                report["checks"]["retrieval_real_routes_cache"] = True
                report["actual_embedding_calls"] = sum(r["model_calls"] for r in remote.diagnostics)
                assert report["actual_embedding_calls"] == 1
                report["retrieval_actions"] = [
                    [
                        {"action": a["action"], "attempts": a["attempts"]}
                        for a in r["receipt"]["actions"]
                    ]
                    for r in remote.diagnostics
                ]
        finally:
            request_execution.reset(token)
            gate.close()
    report["process_ids"] = processes.pids


async def approval(args: argparse.Namespace, report: dict[str, Any], processes: Processes) -> None:
    args.port = port()
    args.key = args.output.parent / "synthetic-rights.key"
    if not args.key.exists():
        args.key.write_bytes(os.urandom(32))
    await processes.start(
        "rights",
        [
            "-m",
            "deephelp_tools.demo.synthetic_rights",
            "--port",
            str(args.port),
            "--key",
            str(args.key),
        ],
        f"http://127.0.0.1:{args.port}/health",
    )
    repo = await ApprovalRepository.open(Path.cwd())
    try:
        await repo.migrate()
        with observation("probe.approval_prepare"):
            op = await prepare(repo, "m21-observation-" + uuid4().hex)
        # Each actual worker is a separate process/trace; ledger controls the action.
        assert await spawn_worker(args, op.plan.operation_id, "pause") == 0
        assert await spawn_worker(args, op.plan.operation_id, "approve") == 0
        assert await spawn_worker(args, op.plan.operation_id, "resume") == 0
        before = await counts(repo, op.plan.operation_id)
        assert before == {"effects": 1, "execute_calls": 1, "query_calls": 0}
        assert await spawn_worker(args, op.plan.operation_id, "resume") == 0
        assert await counts(repo, op.plan.operation_id) == before
        final = await repo.get(op.plan.operation_id)
        assert final.status == OperationStatus.SUCCEEDED
        report["checks"]["approval_process_interrupt_resume_replay"] = True
        report["operation_id"] = op.plan.operation_id
        # Reconcile UNKNOWN with a lost reply, using fixed actions and no model.
        with observation("probe.approval_unknown"):
            unknown = await prepare(repo, "m21-unknown-" + uuid4().hex)
            async with httpx.AsyncClient(trust_env=False, timeout=20) as http:

                def lost_response(stage: str) -> None:
                    if stage == "after_tool":
                        raise OSError("Synthetic lost observation")

                service = ApprovalService(
                    repo,
                    RightsClient(http, args.port, args.key.read_bytes()),
                    proposal_registry().snapshot_hash,
                    fault=lost_response,
                )
                await service.pause(unknown.plan.operation_id, ExecutionBudget.start(60, 10, 0))
                await service.decide(
                    unknown.plan.identity, unknown.plan.operation_id, decision(unknown)
                )
                observed = await service.resume(
                    unknown.plan.identity,
                    unknown.plan.operation_id,
                    resume_command(unknown),
                    ExecutionBudget.start(60, 10, 0),
                )
                assert observed.status == OperationStatus.UNKNOWN
            assert await spawn_worker(args, unknown.plan.operation_id, "resume") == 0
            assert await counts(repo, unknown.plan.operation_id) == {
                "effects": 1,
                "execute_calls": 1,
                "query_calls": 1,
            }
            report["checks"]["unknown_reconciled_without_redispatch"] = True
            report["unknown_operation_id"] = unknown.plan.operation_id
    finally:
        await repo.aclose()


def cleanup(directory: Path) -> None:
    if directory.is_symlink() or directory.is_junction():
        raise ValueError("Changed diagnostic path")
    for name in ("business.log", "retrieval.log", "rights.log"):
        target = directory / name
        if target.is_symlink() or target.is_junction():
            raise ValueError("Changed diagnostic file")
        target.unlink(missing_ok=True)
    directory.rmdir()


async def run(args: argparse.Namespace) -> int:
    if not args.live or not os.getenv("DEEPHELP_OTLP_ENDPOINT"):
        raise ValueError("Explicit --live and DEEPHELP_OTLP_ENDPOINT required")
    if args.output.exists():
        raise ValueError("Use a new final report path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"status": "RUNNING", "checks": {}, "model_quality": "REUSED_M20"}
    # An interrupted attempt retains only its logs; a successful run removes exact own files.
    directory = Path(tempfile.mkdtemp(prefix="deephelp-m21-"))
    processes = Processes(directory)
    try:
        with environment("deephelp-app") as telemetry:
            assert telemetry is not None
            if args.mode in {"protocol", "all"}:
                await protocol(args, report, processes)
            if args.mode in {"approval", "all"}:
                await approval(args, report, processes)
            assert telemetry.provider.force_flush(5000)
            report["checks"]["sdk_export_completed"] = telemetry.exporter.failures == 0
        report["status"] = "PASS" if all(report["checks"].values()) else "FAIL"
        return 0 if report["status"] == "PASS" else 1
    except Exception as exc:
        report.update(
            status="FAIL", error_type=type(exc).__name__, diagnostics_directory=str(directory)
        )
        raise
    finally:
        await processes.close()
        atomic_json(args.output, report)
        if report["status"] == "PASS":
            await asyncio.to_thread(cleanup, directory)
        print(json.dumps({"status": report["status"], "checks": report["checks"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mode", choices=["protocol", "approval", "all"], default="all")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth", type=Path, default=Path(".local/m20/retrieval-auth.json"))
    parser.add_argument(
        "--business-credentials", type=Path, default=Path(".local/m20/service.json")
    )
    parser.add_argument(
        "--retrieval-credentials", type=Path, default=Path(".local/m20/retrieval-service.json")
    )
    parser.add_argument("--pointer", type=Path, default=Path(".local/m09/active.json"))
    parser.add_argument(
        "--providers", type=Path, default=Path("modules/deephelp-app/providers.example.json")
    )
    parser.add_argument("--corpus", type=Path, default=Path("demo/data/m09_corpus.jsonl"))
    parser.add_argument("--budget-state", type=Path, default=Path(".local/m21/budget.json"))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
