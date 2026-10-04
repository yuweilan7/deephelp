"""Independent Business MCP process/restart against imported PG facts, model calls zero."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from deephelp_app.adapters.network_mcp import HTTPMCPConfig, NetworkMCPClient, ServiceCredentials
from deephelp_app.adapters.postgres import PostgresConfig
from deephelp_app.domain.execution import ExecutionBudget
from deephelp_app.domain.models import ToolInvocationEnvelope, ToolResult
from deephelp_services.business_store import BusinessRepository
from deephelp_services.http_transport import initialize_credentials
from deephelp_tools.evaluation.postgres_migration import canonical, read_snapshot, untyped, verify
from deephelp_tools.probes.business_probe import Processes, port
from deephelp_tools.probes.mcp_smoke import synthetic_request
from deephelp_tools.probes.postgres_probe import require_isolated


async def run(args: argparse.Namespace) -> dict[str, Any]:
    config = PostgresConfig.read(args.config)
    require_isolated(config)
    os.environ["DEEPHELP_POSTGRES_CONFIG"] = str(args.config.resolve())
    header, rows, summary, _ = read_snapshot(args.snapshot)
    calls: list[str] = []
    store = await BusinessRepository.open(args.root)
    try:
        with TemporaryDirectory(prefix="m25-http-", dir=args.workdir) as temporary:
            work = Path(temporary)
            credentials_path = work / "credentials.json"
            initialize_credentials(credentials_path)
            credentials = ServiceCredentials.load(credentials_path)
            process = Processes(work)
            base = "http://127.0.0.1:" + str(port())
            client = NetworkMCPClient(
                HTTPMCPConfig(url=base + "/mcp", credentials_path=credentials_path), credentials
            )
            observations = []
            try:
                for _ in range(2):
                    await process.start(
                        "business",
                        [
                            "-m",
                            "deephelp_services.business",
                            "serve",
                            "--root",
                            str(args.root),
                            "--credentials",
                            str(credentials_path),
                            "--port",
                            base.rsplit(":", 1)[1],
                        ],
                        base + "/records/orders",
                    )
                    assert len((await client.list_tools()).tools) == 2
                    cycle_facts = []
                    for coupon in (False, True):
                        name = "dh_m20_coupons" if coupon else "dh_m20_orders"
                        metadata = next(t for t in header["tables"] if t["name"] == name)
                        row = dict(
                            zip(
                                (c[0] for c in metadata["columns"]),
                                (untyped(v) for v in rows[name][0]),
                                strict=True,
                            )
                        )
                        req, _, context = synthetic_request(
                            ExecutionBudget.start(60, 4, 0),
                            order_id=row["order_id"] if coupon else row["record_id"],
                            coupon_id=row["record_id"] if coupon else None,
                            tenant=row["tenant_id"],
                            user=row["user_id"],
                        )
                        envelope = ToolInvocationEnvelope(
                            request=req,
                            request_id=context.request_id,
                            trace_id=context.trace_id,
                            call_id=uuid4().hex,
                        )
                        calls.append(envelope.call_id)
                        wire = await client.call_tool(
                            req.tool_name.value,
                            req.parameters.model_dump(mode="json", exclude_none=True),
                            meta=credentials.protect(
                                "business",
                                {
                                    "action": "call_tool",
                                    "nonce": envelope.call_id,
                                    "envelope": envelope.model_dump(mode="json"),
                                },
                            ),
                        )
                        assert not wire.is_error
                        result = ToolResult.model_validate(wire.structured_content)
                        evidence = await store.evidence(
                            req.identity, result.evidence_refs[0].evidence_id
                        )
                        assert evidence["result"] == result.model_dump(mode="json")
                        for original in evidence["snapshot"]["records"]:
                            assert original["identity"] == req.identity.model_dump(mode="json")
                        facts = {f.name: f.model_dump(mode="json")["value"] for f in result.facts}
                        if not coupon:
                            body = json.loads(row["body"])
                            assert (
                                facts["paid"] == body["paid"]
                                and facts["discount"] == body["discount"]
                            )
                        else:
                            assert facts["coupon_id"] == row["record_id"]
                        cycle_facts.append(facts)
                    observations.append(cycle_facts)
                    await process.stop("business")
                assert observations[0] == observations[1]
            finally:
                await process.close()
        async with store.pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute("CREATE TEMP TABLE m25_cancel_test(marker INT)")
            await conn.commit()
            await cursor.execute("SELECT pg_backend_pid()")
            backend = (await cursor.fetchone())[0]
            await cursor.execute("INSERT INTO m25_cancel_test VALUES(1)")

            async def sleep_in_database() -> None:
                await cursor.execute("SELECT pg_sleep(30)")

            task = asyncio.create_task(sleep_in_database())
            try:
                async with asyncio.timeout(5):
                    while True:
                        async with store.pool.acquire() as monitor, monitor.cursor() as reader:
                            await reader.execute(
                                "SELECT wait_event FROM pg_stat_activity WHERE pid=%s", (backend,)
                            )
                            row = await reader.fetchone()
                        if row and row[0] == "PgSleep":
                            break
                        await asyncio.sleep(0.02)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                else:
                    raise AssertionError("Database cancellation did not propagate")
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await conn.rollback()
            await cursor.execute("SELECT COUNT(*) FROM m25_cancel_test")
            assert await cursor.fetchone() == (0,)
    finally:
        async with store.pool.acquire() as conn, conn.cursor() as cur:
            for call in calls:
                await cur.execute("DELETE FROM dh_m20_evidence WHERE call_id=%s", (call,))
            await conn.commit()
        await store.aclose()
    fresh = await BusinessRepository.open(args.root)
    try:
        await verify(fresh.pool, header, summary)
    finally:
        await fresh.aclose()
    return {
        "status": "PASS",
        "independent_processes": 2,
        "calls": len(calls),
        "same_facts_after_restart": True,
        "model_calls": 0,
        "model_tokens": 0,
        "cleanup": "PASS",
        "server_observed_cancel_rollback": "PASS",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.timeout <= 900:
        parser.error("New output and finite timeout required")

    async def bounded() -> dict[str, Any]:
        async with asyncio.timeout(args.timeout):
            return await run(args)

    args.output.write_bytes(canonical(asyncio.run(bounded())) + b"\n")


if __name__ == "__main__":
    main()
