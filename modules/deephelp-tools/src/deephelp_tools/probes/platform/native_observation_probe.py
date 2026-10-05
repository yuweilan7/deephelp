"""Native fixed protocol proof or explicitly selected live business content."""

import argparse
import asyncio
import json
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from deephelp_app.adapters.approval.approval_store import ApprovalRepository
from deephelp_app.adapters.mcp.shared.network_mcp import HTTPMCPConfig, NetworkMCPClient
from deephelp_app.adapters.observability.langfuse_query import LangfuseRunClient
from deephelp_app.adapters.observability.native_observation import NativeTelemetry, configuration
from deephelp_app.adapters.observability.telemetry import environment, observation, trace_id
from deephelp_app.application.dense import atomic_json
from deephelp_tools.probes.approval.approval_probe import native_approval as approval
from deephelp_tools.probes.protocol.business_probe import Processes, port, reclaim_owned_lock


def cleanup(directory: Path) -> None:
    if any(p.is_symlink() or p.is_junction() for p in [directory, *directory.parents]):
        raise ValueError("Changed diagnostic path")
    for name in ("business.log", "retrieval.log", "rights.log", "application.log"):
        target = directory / name
        if target.is_symlink() or target.is_junction():
            raise ValueError("Changed diagnostic file")
        target.unlink(missing_ok=True)
    directory.rmdir()


async def business(args: argparse.Namespace, report: dict[str, Any], processes: Processes) -> None:
    from deephelp_app.bootstrap.runtime.auth import LocalAuth

    auth = LocalAuth(args.auth)
    token, owner = auth.rows[0]
    headers = {"Authorization": "Bearer " + token}
    other = {"Authorization": "Bearer " + auth.rows[1][0]}
    ports = {name: port() for name in ("business", "retrieval", "application")}
    urls = {name: f"http://127.0.0.1:{value}" for name, value in ports.items()}
    for name, credentials in (
        ("business", args.business_credentials),
        ("retrieval", args.retrieval_credentials),
    ):
        command = [
            "-m",
            "deephelp_services." + name,
            "serve",
            "--port",
            str(ports[name]),
            "--credentials",
            str(credentials),
        ]
        if name == "retrieval":
            command += [
                "--auth",
                str(args.auth),
                "--pointer",
                str(args.pointer),
                "--corpus",
                str(args.corpus),
            ]
        await processes.start(name, command, urls[name] + "/mcp")
    previous = json.loads(args.source.read_text()) if args.source else None
    nonce = (
        previous["event_collection"].removeprefix("dh_m10_events_m28_")
        if previous
        else uuid4().hex[:12]
    )
    app_args = [
        "-m",
        "deephelp_tools.learning.workbench.app",
        "serve",
        "--port",
        str(ports["application"]),
        "--auth",
        str(args.auth),
        "--pointer",
        str(args.pointer),
        "--corpus",
        str(args.corpus),
        "--budget-state",
        str(args.budget_state),
        "--trace-path",
        str(args.trace_path),
        "--business-mcp-url",
        urls["business"] + "/mcp",
        "--service-credentials",
        str(args.business_credentials),
        "--retrieval-mcp-url",
        urls["retrieval"] + "/mcp",
        "--retrieval-credentials",
        str(args.retrieval_credentials),
        "--event-collection",
        "dh_m10_events_m28_" + nonce,
    ]
    report["event_collection"] = "dh_m10_events_m28_" + nonce
    await processes.start("application", app_args, urls["application"] + "/health")
    report["checks"]["three_real_processes"] = True
    async with httpx.AsyncClient(base_url=urls["application"], trust_env=False, timeout=110) as api:

        async def request(
            method: str, path: str, body: Any = None, *, foreign: bool = False, expected: int = 200
        ) -> Any:
            response = await api.request(
                method,
                path,
                headers=other if foreign else headers,
                **({"json": body} if body is not None else {}),
            )
            if response.status_code != expected:
                report["diagnostic"] = {
                    "status": response.status_code,
                    "path": path,
                    "body": response.json(),
                }
                raise AssertionError("Native content API returned an unexpected status")
            return response.json()

        order_id, coupon_id = "00092801", "00092802"
        if not previous:
            await request(
                "POST",
                "/business/orders",
                {
                    "order_id": order_id,
                    "paid": {"amount": "173.45"},
                    "discount": {"amount": "12.30"},
                    "discount_status": "applied",
                    "sku_ids": ["SKU-M28"],
                },
            )
        now = datetime.now(UTC)
        if not previous:
            await request(
                "POST",
                "/business/coupons",
                {
                    "coupon_id": coupon_id,
                    "order_id": order_id,
                    "status": "threshold_not_met",
                    "minimum_spend": {"amount": "200.00"},
                    "valid_from": (now - timedelta(days=1)).isoformat(),
                    "valid_until": (now + timedelta(days=30)).isoformat(),
                },
            )
        samples = [
            ("order", f"订单 {order_id} 承诺有优惠，但结账实付不一致，请查实际优惠"),
            ("coupon", f"订单 {order_id} 券 {coupon_id} 为什么不可用？"),
        ]
        report["cases"] = []
        for name, text in samples:
            if args.sample != "all" and args.sample != name:
                continue
            body = (
                next(c["request"] for c in previous["cases"] if c["sample"] == name)
                if previous
                else {
                    "session_id": "m28-" + name + "-" + nonce,
                    "message_id": uuid4().hex,
                    "channel": "web",
                    "raw_text": text,
                    "occurred_at": now.isoformat(),
                }
            )
            before = json.loads(args.budget_state.read_text())
            value = await request("POST", "/converse", body)
            if previous:
                assert value["replayed"] and json.loads(args.budget_state.read_text()) == before
                original = next(c["response"] for c in previous["cases"] if c["sample"] == name)
                assert value["run_id"] == original["run_id"] and value["facts"] == original["facts"]
                value = original
            case = {"sample": name, "request": body, "response": value}
            report["cases"].append(case)
            assert value["outcome"] == "ANSWERED" and value["call_counts"]["tool_calls"] == (
                1 if name == "order" else 2
            )
            facts = {r["name"]: r["value"] for r in value["facts"]}
            if name == "order":
                assert (
                    facts["paid"]["amount"] == "173.45" and facts["discount"]["amount"] == "12.30"
                )
            else:
                assert facts["minimum_spend"]["amount"] == "200.00"
                assert facts["coupon_status"] == "threshold_not_met"
            before = json.loads(args.budget_state.read_text())
            replay = await request("POST", "/converse", body)
            assert replay["run_id"] == value["run_id"] and replay["replayed"]
            assert json.loads(args.budget_state.read_text()) == before
            path = f"/observability/runs/{value['run_id']}?session_id={body['session_id']}"
            for _ in range(50):
                projection = await request("GET", path + "&page_size=200")
                if projection["status"] == "available" and any(
                    row.get("type") == "GENERATION" for row in projection["spans"]
                ):
                    break
                await asyncio.sleep(1)
            assert projection["source"] == "langfuse" and projection["status"] == "available"
            rows = projection["spans"]
            names = {row["name"] for row in rows}
            generations = [r for r in rows if r.get("type") == "GENERATION"]
            assert {"mcp.business.call", "postgresql.execute"} <= names
            if name == "order":
                assert {
                    "mcp.retrieval.call",
                    "retrieval.dense",
                    "retrieval.bm25",
                    "retrieval.fusion",
                } <= names
            assert generations and all(
                r["model"] and r["usage"] and r["version"] for r in generations
            )
            serialized = json.dumps(projection, ensure_ascii=False)
            assert order_id not in serialized and coupon_id not in serialized
            assert not any(secret in serialized for secret in (token, "reasoning_content"))
            await request("GET", path, foreign=True, expected=404)
            pages = await request("GET", path + "&page_size=2")
            assert len(pages["spans"]) == 2 and pages["next_cursor"] == 2
            for view in ("intent", "turns", "flow"):
                await request(
                    "GET", f"/debug/runs/{value['run_id']}/{view}?session_id={body['session_id']}"
                )
            case["observation"] = {
                "trace_id": projection["otel_trace_id"],
                "total": projection["total"],
                "generation_count": len(generations),
                "usage": [r["usage"] for r in generations],
                "names": sorted(names),
                "langfuse_url": projection["langfuse_url"],
            }
            if previous:
                old = next(c.get("observation") for c in previous["cases"] if c["sample"] == name)
                if old:
                    assert len(generations) == old["generation_count"]
            report["checks"][name + "_content_native_usage_scoped_replay"] = True
        report["observed_generation_count"] = sum(
            c["observation"]["generation_count"] for c in report["cases"]
        )
        report["actual_model_calls"] = 0 if previous else report["observed_generation_count"]
        report["checks"]["three_views_same_authorized_run"] = True
        atomic_json(args.output, report)
        if args.browser_control:
            context = {
                "url": urls["application"],
                "cases": [
                    {
                        "run_id": c["response"]["run_id"],
                        "session_id": c["request"]["session_id"],
                        "observation": c["observation"],
                    }
                    for c in report["cases"]
                ],
            }
            atomic_json(args.browser_control.with_suffix(".context.json"), context)
            print(json.dumps({"browser_ready": context}), flush=True)
            deadline = time.monotonic() + 300
            while not args.browser_control.exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("Browser verification control was not completed")
                await asyncio.sleep(0.2)
            assert args.browser_control.read_text().strip() == "PASS"
            report["checks"]["browser_same_run_projection"] = True
    if args.fault_checks:
        original_config = os.environ["DEEPHELP_LANGFUSE_CONFIG"]
        config = configuration(original_config)
        report["faults"] = {}
        for name, change in (
            ("disabled", {"enabled": False}),
            ("unavailable", {"host": "http://127.0.0.1:9"}),
        ):
            if "application" in processes.running:
                await processes.stop("application")
            await reclaim_owned_lock(args.budget_state.with_suffix(".lock"))
            target = processes.logs / (name + ".json")
            atomic_json(target, {**config, **change})
            os.environ["DEEPHELP_LANGFUSE_CONFIG"] = str(target)
            started = time.monotonic()
            try:
                await processes.start("application", app_args, urls["application"] + "/health")
                before = json.loads(args.budget_state.read_text())
                async with httpx.AsyncClient(
                    base_url=urls["application"], trust_env=False, timeout=110
                ) as api:
                    for case in report["cases"]:
                        result = await api.post("/converse", headers=headers, json=case["request"])
                        result.raise_for_status()
                        replay = result.json()
                        assert replay["replayed"] and replay["run_id"] == case["response"]["run_id"]
                        assert replay["facts"] == case["response"]["facts"]
                    if name == "unavailable":
                        case = report["cases"][0]
                        result = await api.get(
                            f"/observability/runs/{case['response']['run_id']}",
                            headers=headers,
                            params={"session_id": case["request"]["session_id"]},
                        )
                        result.raise_for_status()
                        assert result.json()["status"] == "unavailable"
                assert json.loads(args.budget_state.read_text()) == before
                report["faults"][name] = {
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "new_model_calls": 0,
                }
                report["checks"][name + "_replay_no_business_or_model_repeat"] = True
            finally:
                os.environ["DEEPHELP_LANGFUSE_CONFIG"] = original_config
                if "application" in processes.running:
                    await processes.stop("application")
                await reclaim_owned_lock(args.budget_state.with_suffix(".lock"))
                if any(p.is_symlink() or p.is_junction() for p in [target, *target.parents]):
                    raise ValueError("Changed private fault configuration path")
                target.unlink()


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
        async with asyncio.timeout(900 if args.mode == "business" else 600):
            with environment("deephelp-app") as runtime:
                assert isinstance(runtime, NativeTelemetry)
                if args.mode == "business":
                    await business(args, report, processes)
                    report["status"] = "PASS" if all(report["checks"].values()) else "FAIL"
                    return 0 if report["status"] == "PASS" else 1
                await protocol(args, report, processes)
                await approval(args, report, processes)
                await asyncio.to_thread(runtime.client.flush)
                # Stop gracefully exporting child observations before querying.
                await processes.close()
                async with httpx.AsyncClient(trust_env=False) as http:
                    reader = LangfuseRunClient(http, os.environ["DEEPHELP_LANGFUSE_CONFIG"])
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
        if args.mode == "business":
            await reclaim_owned_lock(args.budget_state.with_suffix(".lock"))
        atomic_json(args.output, report)
        if report["status"] == "PASS":
            await asyncio.to_thread(cleanup, directory)
        print(json.dumps({"status": report["status"], "checks": report["checks"]}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mode", choices=["proof", "business"], default="proof")
    parser.add_argument("--sample", choices=["all", "order", "coupon"], default="all")
    parser.add_argument(
        "--source", type=Path, help="Read/replay existing business receipts; no new model calls"
    )
    parser.add_argument("--budget-state", type=Path, default=Path(".local/m28/budget.json"))
    parser.add_argument("--trace-path", type=Path, default=Path(".local/m28/trace.jsonl"))
    parser.add_argument("--browser-control", type=Path)
    parser.add_argument("--fault-checks", action="store_true")
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
