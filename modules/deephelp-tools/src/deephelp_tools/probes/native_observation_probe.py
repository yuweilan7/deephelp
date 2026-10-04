"""Fixed native cross-process/official-graph proof. No provider/model calls."""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import httpx

from deephelp_app.adapters.approval_store import ApprovalRepository
from deephelp_app.adapters.langfuse_query import LangfuseRunClient
from deephelp_app.adapters.native_observation import NativeTelemetry
from deephelp_app.adapters.network_mcp import HTTPMCPConfig, NetworkMCPClient
from deephelp_app.adapters.telemetry import environment, observation, trace_id
from deephelp_app.adapters.tempo import TempoClient
from deephelp_app.application.dense import atomic_json
from deephelp_tools.probes.business_probe import Processes, port
from deephelp_tools.probes.observability_probe import approval, cleanup


async def protocol(args: argparse.Namespace, report: dict[str, Any], processes: Processes) -> None:
    for kind, credentials in (
        ("business", args.business_credentials),
        ("retrieval", args.retrieval_credentials),
    ):
        listening = port()
        config = HTTPMCPConfig(
            url=f"http://127.0.0.1:{listening}/mcp", credentials_path=credentials, audience=kind
        )
        command = [
            "-m",
            "deephelp_services." + kind,
            "serve",
            "--port",
            str(listening),
            "--credentials",
            str(credentials),
        ]
        if kind == "retrieval":
            command += [
                "--auth",
                str(args.auth),
                "--pointer",
                str(args.pointer),
                "--corpus",
                str(args.corpus),
            ]
        await processes.start(kind, command, config.url)
        network = NetworkMCPClient(config, config.credentials())
        with observation("probe.fixed." + kind):
            report[kind + "_trace_id"] = trace_id()
            listed = await network.list_tools()
            assert len(listed.tools) >= 2
            # Deterministic schema rejection must cross the real RPC boundary;
            # provider calls cannot occur with the invalid arguments.
            name = "get_order_benefits" if kind == "business" else "compare_intents"
            result = await network.call_tool(name, {}, meta={})
            assert result.is_error
        report["checks"][kind + "_real_mcp_schema_rejection"] = True


async def run(args: argparse.Namespace) -> int:
    if not args.live or not os.getenv("DEEPHELP_LANGFUSE_CONFIG"):
        raise ValueError("Explicit --live and DEEPHELP_LANGFUSE_CONFIG required")
    if args.output.exists():
        raise ValueError("Use a new report path")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="deephelp-m28-"))
    processes = Processes(directory)
    report: dict[str, Any] = {"status": "RUNNING", "checks": {}, "actual_model_calls": 0}
    try:
        async with asyncio.timeout(600):
            with environment("deephelp-app") as runtime:
                assert isinstance(runtime, NativeTelemetry)
                await protocol(args, report, processes)
                await approval(args, report, processes)
                await asyncio.to_thread(runtime.client.flush)
                # Stop gracefully exporting child observations before querying.
                await processes.close()
                async with httpx.AsyncClient(trust_env=False) as http:
                    reader = LangfuseRunClient(
                        http, os.environ["DEEPHELP_LANGFUSE_CONFIG"], TempoClient(http, None)
                    )
                    for kind in ("business", "retrieval"):
                        for _ in range(45):
                            state, rows = await reader.trace(report[kind + "_trace_id"])
                            if any(r["name"] == "mcp." + kind + ".call" for r in rows):
                                break
                            await asyncio.sleep(1)
                        else:
                            raise AssertionError("Cross-process MCP observation missing")
                        report["checks"][kind + "_parent_readback"] = any(
                            r["parentObservationId"] in {v["id"] for v in rows}
                            for r in rows
                            if r["name"] == "mcp." + kind + ".call"
                        )
                        assert not any(r["type"] == "GENERATION" for r in rows)
                    repo = await ApprovalRepository.open(Path.cwd())
                    try:
                        op = await repo.get(report["operation_id"])
                        for _ in range(45):
                            projection = await reader.read(repo.pool, op.run_id, None, 0, 200)
                            if any(r["name"] == "LangGraph" for r in projection["spans"]):
                                break
                            await asyncio.sleep(1)
                        assert projection["status"] == "available"
                        associations = projection["associations"]
                        pauses = {r["trace_id"] for r in associations if r["kind"] == "pause"}
                        resumed = [r for r in associations if r["kind"] == "resume"]
                        assert (
                            pauses
                            and resumed
                            and all(
                                r["trace_id"] not in pauses and r["predecessor_trace_id"] in pauses
                                for r in resumed
                            )
                        )
                        assert not any(r["type"] == "GENERATION" for r in projection["spans"])
                        page = await reader.read(repo.pool, op.run_id, None, 0, 2)
                        assert len(page["spans"]) == 2 and page["next_cursor"] == 2
                        report["approval"] = {
                            "run_id": op.run_id,
                            "spans": projection["total"],
                            "associations": associations,
                        }
                        report["checks"].update(
                            official_callback_readback=True,
                            new_process_resume_predecessor=True,
                            actual_native_pagination=True,
                        )
                    finally:
                        await repo.aclose()
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
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--auth", type=Path, default=Path(".local/m20/retrieval-auth.json"))
    parser.add_argument(
        "--business-credentials", type=Path, default=Path(".local/m20/service.json")
    )
    parser.add_argument(
        "--retrieval-credentials", type=Path, default=Path(".local/m20/retrieval-service.json")
    )
    parser.add_argument("--pointer", type=Path, default=Path(".local/m09/active.json"))
    parser.add_argument("--corpus", type=Path, default=Path("demo/data/m09_corpus.jsonl"))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
