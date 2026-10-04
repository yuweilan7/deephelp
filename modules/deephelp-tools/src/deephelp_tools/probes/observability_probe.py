"""M21 fixed real MCP/PostgreSQL/Collector actions; model quality is a separate boundary."""

import argparse
import asyncio
import json
import os
import tempfile
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
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
from deephelp_app.adapters.tempo import TempoClient
from deephelp_app.adapters.tool_gateway import ToolGateway
from deephelp_app.adapters.trace import MemoryTrace
from deephelp_app.api.app import create_app
from deephelp_app.application.approval import ApprovalService
from deephelp_app.application.dense import atomic_json
from deephelp_app.bootstrap.mvp_runtime import BudgetSession, LocalAuth
from deephelp_app.bootstrap.settings import Settings
from deephelp_app.domain.execution import ExecutionBudget, request_execution
from deephelp_app.domain.models import BusinessOrderInput, Money, OperationStatus, ToolStatus
from deephelp_tools.demo.approval_prepare import prepare
from deephelp_tools.evaluation.samples import load_corpus
from deephelp_tools.evaluation.sop_acceptance import proposal_registry
from deephelp_tools.learning.mvp_replay import ReplayAssembly
from deephelp_tools.probes.approval_probe import (
    counts,
    decision,
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
            async with (
                httpx.AsyncClient(trust_env=False, timeout=20) as http,
                AsyncExitStack() as approval_stack,
            ):

                def lost_response(stage: str) -> None:
                    if stage == "after_tool":
                        raise OSError("Synthetic lost observation")

                service = await approval_stack.enter_async_context(
                    ApprovalService.open(
                        repo,
                        RightsClient(http, args.port, args.key.read_bytes()),
                        proposal_registry().snapshot_hash,
                        fault=lost_response,
                    )
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


async def platform(args: argparse.Namespace, report: dict[str, Any]) -> None:
    """Real platform and original application paths; fixed engine responses, no model API."""
    auth = LocalAuth(args.platform_auth)
    owner_token = next(
        t
        for t, i in auth.rows
        if i == next(c for c in load_corpus().cases if c.case_id == "M02-005").identity
    )
    assembly, sink = ReplayAssembly(), MemoryTrace()

    @asynccontextmanager
    async def factory(http: httpx.AsyncClient, trace: Any) -> Any:
        async with assembly.open(http, trace) as service:
            yield service

    app = create_app(
        Settings(mode="test", request_timeout=60, max_attempts=40),
        trace=sink,
        transport=httpx.AsyncHTTPTransport(retries=0),
        identity_provider=auth,
        conversation_factory=factory,
        tempo_endpoint=args.tempo_endpoint,
        grafana_url=args.grafana_url,
    )
    from deephelp_app.adapters.telemetry import current

    runtime = current()
    assert runtime is not None
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app),
            base_url="http://local",
            headers={"Authorization": "Bearer " + owner_token},
        ) as api,
    ):
        runtime = current()
        assert runtime is not None
        body = dict(
            channel="m21",
            session_id="platform-" + uuid4().hex,
            message_id=uuid4().hex,
            raw_text=next(
                c for c in load_corpus().cases if c.case_id == "M02-005"
            ).message.raw_text,
            occurred_at=datetime.now(UTC).isoformat(),
        )
        response = await api.post("/converse", json=body)
        report["fixed_pipeline_response"] = {
            "http_status": response.status_code,
            "outcome": response.json().get("outcome"),
            "error_code": (response.json().get("error") or {}).get("code"),
        }
        assert response.status_code == 200 and response.json()["outcome"] == "ANSWERED"
        receipt = response.json()
        report["platform_trace_id"] = response.headers["x-otel-trace-id"]
        report["platform_run_id"] = receipt["run_id"]
        assert runtime.provider.force_flush(5000)
        endpoint = f"/observability/runs/{receipt['run_id']}"
        pages = []
        for _attempt in range(20):
            page = (
                await api.get(endpoint, params={"session_id": body["session_id"], "page_size": 5})
            ).json()
            if page["status"] == "available":
                break
            await asyncio.sleep(0.5)
        assert page["status"] == "available"
        assert page["otel_trace_id"] == report["platform_trace_id"] and page["grafana_url"]
        pages.extend(page["spans"])
        while page["next_cursor"] is not None:
            page = (
                await api.get(
                    endpoint,
                    params={
                        "session_id": body["session_id"],
                        "page_size": 5,
                        "cursor": page["next_cursor"],
                    },
                )
            ).json()
            pages.extend(page["spans"])
            assert len(pages) <= 20000
        assert len(pages) == page["total"] and len({s["span_id"] for s in pages}) == len(pages)
        assert any(s["name"].startswith("stage.") for s in pages)
        denied = await api.get(endpoint, params={"session_id": "other"})
        assert denied.status_code == 404
        replay = (await api.post("/converse", json=body)).json()
        assert replay["replayed"] and replay["call_counts"]["model_calls"] == 0
        report["checks"].update(
            scoped_actual_tempo_projection=True,
            pagination_without_duplicates=True,
            private_grafana_deeplink=True,
            replay_zero_new_model_calls=True,
        )
        report["projected_spans"] = len(pages)
        report["source"] = (
            "fixed ReplayAssembly; actual SDK/Collector/Tempo; no model quality claim"
        )
    async with httpx.AsyncClient(trust_env=False) as api:
        reused = json.loads(args.reuse_report.read_text(encoding="utf-8"))["protocol_trace_id"]
        trace = await TempoClient(api, args.tempo_endpoint).read(reused, 0, 200)
        assert trace["status"] == "available" and trace["total"] == 42
        assert {s["service"] for s in trace["spans"]} == {
            "deephelp-app",
            "deephelp-business-mcp",
            "deephelp-retrieval-mcp",
        }
        report["checks"]["same_actual_cross_mcp_trace_in_tempo"] = True
        report["reused_trace_id"] = reused
        # One scrape window is needed; no model calls or repeated business actions.
        await asyncio.sleep(6)
        data = (
            await api.get(
                args.prometheus_endpoint + "/api/v1/query",
                params={"query": "sum(deephelp_operations_total)"},
            )
        ).json()
        assert data["status"] == "success" and float(data["data"]["result"][0]["value"][1]) > 0
        data = (
            await api.get(
                args.prometheus_endpoint + "/api/v1/query",
                params={"query": "sum(deephelp_duration_seconds_count)"},
            )
        ).json()
        assert data["data"]["result"] and float(data["data"]["result"][0]["value"][1]) > 0
        report["checks"]["prometheus_actual_counts_histograms"] = True


async def failure(args: argparse.Namespace, report: dict[str, Any], processes: Processes) -> None:
    """Caller deliberately stops Collector first; one actual DB tool call must succeed."""
    from deephelp_app.adapters.telemetry import current

    runtime = current()
    assert runtime is not None
    business_port = port()
    business = HTTPMCPConfig(
        url=f"http://127.0.0.1:{business_port}/mcp", credentials_path=args.business_credentials
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
        business.url,
    )
    owner = LocalAuth(args.auth).rows[0][1]
    rows = await BusinessClient(business).request(owner, "GET", "orders")
    order = next(r for r in rows["items"] if r["data"]["order_id"].startswith("M21-"))
    req, question, context = synthetic_request(
        ExecutionBudget.start(60, 10, 0),
        order_id=order["data"]["order_id"],
        user=owner.user_id,
        tenant=owner.tenant_id,
    )
    with observation("probe.export_failure"):
        report["failure_trace_id"] = trace_id()
        async with ToolGateway(http=business).open() as tools:
            tool_budget = ExecutionBudget.start(60, 10, 0)
            result = await tools.execute(req, question, tool_budget, context=context)
            assert tool_budget.tool_steps_used == 1 and tool_budget.retries_used == 0
            report["actual_tool_dispatches"] = tool_budget.tool_steps_used
    assert result.status == ToolStatus.SUCCEEDED
    paid = next(
        f.value.amount for f in result.facts if f.name == "paid" and isinstance(f.value, Money)
    )
    assert paid == Decimal("173.45")
    assert runtime.provider.force_flush(5000)
    if args.mode == "failure":
        assert runtime.exporter.failures > 0
    else:
        assert runtime.exporter.failures == 0
        async with httpx.AsyncClient(trust_env=False) as api:
            try:
                ready = await api.get(args.tempo_endpoint + "/ready", timeout=2)
                assert ready.status_code != 200
            except httpx.TransportError:
                pass
    report["checks"].update(
        actual_db_result_during_export_failure=True,
        no_model_calls=True,
    )
    report["checks"][
        "diagnostic_failure_counted"
        if args.mode == "failure"
        else "collector_accepted_while_backend_stopped"
    ] = True
    report["failed_export_spans"] = runtime.exporter.failures
    report["paid"] = str(paid)


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
            if args.mode == "platform":
                await platform(args, report)
            if args.mode in {"failure", "backend-failure"}:
                await failure(args, report, processes)
            assert telemetry.provider.force_flush(5000)
            if args.mode != "failure":
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
    parser.add_argument(
        "--mode",
        choices=["protocol", "approval", "all", "platform", "failure", "backend-failure"],
        default="all",
    )
    parser.add_argument("--tempo-endpoint", default="http://127.0.0.1:13200")
    parser.add_argument("--grafana-url", default="http://127.0.0.1:13000")
    parser.add_argument("--prometheus-endpoint", default="http://127.0.0.1:19090")
    parser.add_argument(
        "--reuse-report", type=Path, default=Path(".local/m21/standard-protocol-v3.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth", type=Path, default=Path(".local/m20/retrieval-auth.json"))
    parser.add_argument("--platform-auth", type=Path, default=Path(".local/m21/auth.json"))
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
