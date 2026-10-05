"""Explicit read-only PG inventory and actual installed HTTP/metrics content checks."""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
from psycopg import AsyncConnection, sql

from deephelp_app.adapters.persistence.postgres import PostgresConfig


async def inventory(config: Path) -> dict[str, Any]:
    async with await AsyncConnection.connect(
        **PostgresConfig.read(config).connection_kwargs()
    ) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            await cursor.execute(
                "SELECT schemaname,tablename FROM pg_tables "
                "WHERE schemaname NOT IN ('pg_catalog','information_schema') ORDER BY 1,2"
            )
            tables = await cursor.fetchall()
            result = {}
            for schema, table in tables:
                await cursor.execute(
                    sql.SQL("SELECT row_to_json(t)::text FROM {} t").format(
                        sql.Identifier(schema, table)
                    )
                )
                hashes = sorted(
                    hashlib.sha256(row[0].encode()).digest() for row in await cursor.fetchall()
                )
                result[schema + "." + table] = {
                    "rows": len(hashes),
                    "sha256": hashlib.sha256(b"".join(hashes)).hexdigest(),
                }
            await connection.rollback()
            return result


async def run(args: argparse.Namespace) -> dict[str, Any]:
    async with asyncio.timeout(args.timeout):
        tables = await inventory(args.config)
        if args.compare_config:
            assert tables == await inventory(args.compare_config), (
                "Database restore contents differ"
            )
        if args.compare_inventory:
            assert tables == json.loads(args.compare_inventory.read_text())["tables"], (
                "Database snapshot contents differ"
            )
        report: dict[str, Any] = {
            "status": "PASS",
            "tables": tables,
            "model_calls": 0,
            "comparisons": bool(args.compare_config or args.compare_inventory),
        }
        if args.base_url:
            token = json.loads(args.auth.read_text())["tokens"][0]["token"]
            async with httpx.AsyncClient(
                base_url=args.base_url,
                headers={"Authorization": "Bearer " + token},
                timeout=5,
                trust_env=False,
            ) as client:
                health = await client.get("/health")
                assert health.status_code == 200 and health.json()["status"] == "ok"
                records = await client.get("/business/orders")
                assert records.status_code == 200 and records.json()
                history = await client.get("/history/sessions")
                assert history.status_code == 200
                rejected = await client.get(
                    "/business/orders", headers={"Authorization": "Bearer invalid"}
                )
                assert rejected.status_code == 401
                schema = await client.post("/business/orders", json={})
                assert schema.status_code == 422
                report["http"] = {
                    "business_records": records.json(),
                    "history": history.json(),
                    "unauthenticated": rejected.status_code,
                    "schema": schema.status_code,
                }
        if args.metrics_ports:
            async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
                for port in args.metrics_ports:
                    response = await client.get(f"http://127.0.0.1:{port}/metrics")
                    assert response.status_code == 200 and "target_info" in response.text
                    assert "deephelp_operations_total" in response.text
                    report.setdefault("metrics", {})[str(port)] = {
                        "bytes": len(response.content),
                        "sha256": hashlib.sha256(response.content).hexdigest(),
                    }
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--compare-config", type=Path)
    parser.add_argument("--compare-inventory", type=Path)
    parser.add_argument("--base-url")
    parser.add_argument("--auth", type=Path)
    parser.add_argument("--metrics-ports", type=int, nargs="*")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or not 1 <= args.timeout <= 900 or (args.base_url and not args.auth):
        parser.error("New output, finite limits and explicit HTTP credentials required")
    try:
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            report = runner.run(run(args))
    except Exception as exc:
        args.output.write_text(json.dumps({"status": "FAIL", "error": type(exc).__name__}))
        raise
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("PASS: actual PG/installed HTTP/metrics checks; model calls 0")


if __name__ == "__main__":
    main()
