"""Explicit M25 real PostgreSQL protocol qualification; never a business cutover."""

import argparse
import asyncio
import hashlib
import json
import platform
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import psycopg_pool
from psycopg import AsyncConnection, sql
from psycopg.errors import LockNotAvailable, UniqueViolation

from deephelp_app.adapters.postgres import PostgresConfig, postgres_pool, run_postgres


def require_isolated(config: PostgresConfig) -> None:
    if not re.fullmatch(r"deephelp_m25_[a-z0-9_]+", config.dbname):
        raise ValueError("Protocol probe requires a dedicated deephelp_m25_* database")


async def mysql_inventory(root: Path) -> dict[str, Any]:
    """Schema and exact counts only, all source reads in one read-only snapshot."""
    from deephelp_app.adapters.ledger import MySQLLedger

    source = await MySQLLedger.open(root)
    try:
        async with source.pool.acquire() as conn, conn.cursor() as cur:
            try:
                await cur.execute(
                    "SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                )
                await cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
                await cur.execute(
                    "SELECT @@version,@@transaction_isolation,@@transaction_read_only"
                )
                version, isolation, read_only = await cur.fetchone()
                assert read_only == 1 and isolation == "REPEATABLE-READ"
                await cur.execute(
                    "SELECT TABLE_NAME,ENGINE FROM information_schema.tables "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"
                )
                tables = []
                for name, engine in await cur.fetchall():
                    assert re.fullmatch(r"[a-zA-Z0-9_]+", name)
                    await cur.execute(f"SELECT COUNT(*) FROM `{name}`")
                    count = (await cur.fetchone())[0]
                    await cur.execute(
                        "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY,COLLATION_NAME "
                        "FROM information_schema.columns WHERE TABLE_SCHEMA=DATABASE() "
                        "AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                        (name,),
                    )
                    columns = [list(row) for row in await cur.fetchall()]
                    await cur.execute(
                        "SELECT INDEX_NAME,NON_UNIQUE,SEQ_IN_INDEX,COLUMN_NAME,SUB_PART "
                        "FROM information_schema.statistics WHERE TABLE_SCHEMA=DATABASE() "
                        "AND TABLE_NAME=%s ORDER BY INDEX_NAME,SEQ_IN_INDEX",
                        (name,),
                    )
                    indexes = [list(row) for row in await cur.fetchall()]
                    tables.append(
                        {
                            "table": name,
                            "engine": engine,
                            "rows": count,
                            "columns": columns,
                            "indexes": indexes,
                        }
                    )
                await cur.execute(
                    "SELECT TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME "
                    "FROM information_schema.key_column_usage WHERE TABLE_SCHEMA=DATABASE() "
                    "AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY TABLE_NAME,ORDINAL_POSITION"
                )
                foreign_keys = [list(row) for row in await cur.fetchall()]
                return {
                    "version": version,
                    "isolation": isolation,
                    "read_only": bool(read_only),
                    "tables": tables,
                    "foreign_keys": foreign_keys,
                    "scope": "metadata and counts; no row export, data migration or source writes",
                }
            finally:
                await conn.rollback()
    finally:
        await source.aclose()


async def protocol(config: PostgresConfig, report: dict[str, Any]) -> None:
    require_isolated(config)
    schema = "m25_protocol_" + uuid4().hex
    report["schema"] = schema
    checks: list[str] = report.setdefault("checks", [])
    connections: list[AsyncConnection[Any]] = []
    backend_pids: set[int] = set()
    created = False
    passed = False
    async with postgres_pool(config) as pool:

        async def query(statement: str, params: tuple[Any, ...] = ()) -> list[Any]:
            async with pool.connection() as conn:
                connections.append(conn)
                backend_pids.add(conn.info.backend_pid)
                async with conn.transaction():
                    await conn.execute(
                        sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
                    )
                    return await (await conn.execute(statement, params)).fetchall()

        async def action(statement: str, params: tuple[Any, ...] = ()) -> None:
            async with pool.connection() as conn, conn.transaction():
                await conn.execute(
                    sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
                )
                await conn.execute(statement, params)

        async def check(name: str, operation: Callable[[], Awaitable[None]]) -> None:
            report["active_check"] = name
            await operation()
            checks.append(name)

        async def wait_for_activity(predicate: Callable[[list[Any]], bool]) -> None:
            async with asyncio.timeout(3):
                while True:
                    rows = await query(
                        "SELECT pid,state,wait_event,pg_blocking_pids(pid) FROM pg_stat_activity "
                        "WHERE datname=current_database()"
                    )
                    if predicate(rows):
                        return
                    await asyncio.sleep(0.02)

        try:
            async with pool.connection() as conn:
                info = await (
                    await conn.execute(
                        "SELECT current_database(),version(),"
                        "current_setting('transaction_isolation'),"
                        "current_setting('TimeZone')"
                    )
                ).fetchone()
                assert (
                    info is not None
                    and info[0] == config.dbname
                    and info[2:] == ("read committed", "UTC")
                )
                report["server_version"] = info[1]
                report["isolation"] = info[2]
                report["timezone"] = info[3]
                await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await action(
                'CREATE TABLE facts (id text COLLATE "C" PRIMARY KEY, version bigint NOT NULL, '
                "amount numeric(20,2), nullable text, enabled boolean, occurred_at timestamptz, "
                "signed_json json, searchable_json jsonb, blob bytea)"
            )
            await action(
                "CREATE TABLE outbox (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
                "fact_id text NOT NULL REFERENCES facts(id), version bigint NOT NULL, "
                "done boolean NOT NULL DEFAULT false, UNIQUE(fact_id,version))"
            )
            await action(
                "CREATE TABLE effects (operation_id text PRIMARY KEY REFERENCES facts(id), "
                "body text)"
            )

            async def types() -> None:
                raw = '{ "b":2, "a":1, "a":3 }'
                binary = b"\x00\xffcheckpoint\x00"
                instant = datetime(2026, 10, 4, 1, 2, 3, 456789, tzinfo=UTC)
                await action(
                    "INSERT INTO facts VALUES (%s,1,%s,NULL,true,%s,%s::json,%s::jsonb,%s)",
                    ("000031", Decimal("219.87"), instant, raw, raw, binary),
                )
                row = (
                    await query(
                        "SELECT id,amount,nullable,enabled,occurred_at,signed_json::text,"
                        "searchable_json,blob FROM facts WHERE id=%s",
                        ("000031",),
                    )
                )[0]
                assert row == (
                    "000031",
                    Decimal("219.87"),
                    None,
                    True,
                    instant,
                    raw,
                    {"a": 3, "b": 2},
                    binary,
                )
                assert (
                    hashlib.sha256(row[5].encode()).digest()
                    == hashlib.sha256(raw.encode()).digest()
                )
                await action("INSERT INTO facts(id,version) VALUES ('Case',1),('case',1)")
                assert len(await query("SELECT id FROM facts WHERE id IN ('Case','case')")) == 2
                checks.append("jsonb_is_not_signed_original")

            await check("exact_types_utc_case_and_signed_bytes", types)

            async def rollback() -> None:
                try:
                    async with pool.connection() as conn, conn.transaction():
                        await conn.execute(
                            sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
                        )
                        await conn.execute("INSERT INTO facts(id,version) VALUES ('rollback',1)")
                        raise ValueError("intentional rollback")
                except ValueError:
                    pass
                assert not await query("SELECT id FROM facts WHERE id='rollback'")

            await check("transaction_rollback_and_pool_reuse", rollback)

            async def unique() -> None:
                async def accept() -> list[Any]:
                    return await query(
                        "INSERT INTO facts(id,version) VALUES ('message',1) "
                        "ON CONFLICT (id) DO NOTHING RETURNING id"
                    )

                results = await asyncio.gather(*(accept() for _ in range(8)))
                assert sum(bool(row) for row in results) == 1
                assert await query("SELECT count(*) FROM facts WHERE id='message'") == [(1,)]
                try:
                    await action("INSERT INTO facts(id,version) VALUES ('message',99)")
                except UniqueViolation:
                    pass
                else:
                    raise AssertionError("Expected unique violation")
                assert await query("SELECT version FROM facts WHERE id='message'") == [(1,)]

            await check("eight_unique_receivers_and_conflict_rollback", unique)

            async def cas() -> None:
                results = await asyncio.gather(
                    *(
                        query(
                            "UPDATE facts SET version=2 WHERE id='message' AND version=1 "
                            "RETURNING version"
                        )
                        for _ in range(8)
                    )
                )
                assert sum(bool(row) for row in results) == 1

            await check("eight_version_cas_single_winner", cas)

            async def atomic() -> None:
                for fail in (True, False):
                    try:
                        async with pool.connection() as conn, conn.transaction():
                            await conn.execute(
                                sql.SQL("SET LOCAL search_path TO {}").format(
                                    sql.Identifier(schema)
                                )
                            )
                            cur = await conn.execute(
                                "UPDATE facts SET version=3 WHERE id='message' "
                                "AND version=2 RETURNING version"
                            )
                            assert await cur.fetchone() == (3,)
                            await conn.execute(
                                "INSERT INTO outbox(fact_id,version) VALUES ('message',3)"
                            )
                            if fail:
                                raise ValueError("fail before commit")
                    except ValueError:
                        assert await query("SELECT version FROM facts WHERE id='message'") == [(2,)]
                        assert not await query("SELECT id FROM outbox WHERE fact_id='message'")
                assert await query("SELECT version FROM facts WHERE id='message'") == [(3,)]
                assert await query("SELECT count(*) FROM outbox WHERE fact_id='message'") == [(1,)]

            await check("fact_and_outbox_atomic_commit_rollback", atomic)

            async def read_committed() -> None:
                async with pool.connection() as reader, reader.transaction():
                    await reader.execute(
                        sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
                    )
                    assert await (
                        await reader.execute("SELECT version FROM facts WHERE id='message'")
                    ).fetchone() == (3,)
                    await action("UPDATE facts SET version=4 WHERE id='message'")
                    assert await (
                        await reader.execute("SELECT version FROM facts WHERE id='message'")
                    ).fetchone() == (4,)

            await check("read_committed_statement_snapshot", read_committed)

            async def effect_lock() -> None:
                async with pool.connection() as writer, writer.transaction():
                    await writer.execute(
                        sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
                    )
                    await writer.execute("SELECT id FROM facts WHERE id='message' FOR UPDATE")
                    await writer.execute("INSERT INTO effects VALUES ('message','committed')")

                    async def reconcile() -> list[Any]:
                        async with pool.connection() as reader, reader.transaction():
                            await reader.execute(
                                sql.SQL("SET LOCAL search_path TO {}").format(
                                    sql.Identifier(schema)
                                )
                            )
                            await reader.execute(
                                "SELECT id FROM facts WHERE id='message' FOR UPDATE"
                            )
                            return await (
                                await reader.execute(
                                    "SELECT body FROM effects WHERE operation_id='message'"
                                )
                            ).fetchall()

                    task = asyncio.create_task(reconcile())
                    try:
                        await wait_for_activity(lambda rows: any(row[3] for row in rows))
                        assert not task.done()
                    except BaseException:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        raise
                assert await task == [("committed",)]

            await check("effect_query_lock_prevents_false_absent", effect_lock)

            async def skip_locked() -> None:
                await action("INSERT INTO outbox(fact_id,version) VALUES ('Case',1),('case',1)")
                async with pool.connection() as first, first.transaction():
                    await first.execute(
                        sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
                    )
                    claimed = await (
                        await first.execute(
                            "SELECT id FROM outbox WHERE NOT done "
                            "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED"
                        )
                    ).fetchone()
                    other = await query(
                        "SELECT id FROM outbox WHERE NOT done ORDER BY id "
                        "LIMIT 1 FOR UPDATE SKIP LOCKED"
                    )
                    assert claimed is not None and other and other[0] != claimed

            await check("skip_locked_independent_outbox_claim", skip_locked)

            async def lock_timeout() -> None:
                async with pool.connection() as first, first.transaction():
                    await first.execute(
                        sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
                    )
                    await first.execute("SELECT id FROM facts WHERE id='message' FOR UPDATE")
                    try:
                        async with pool.connection() as second, second.transaction():
                            await second.execute(
                                sql.SQL("SET LOCAL search_path TO {}").format(
                                    sql.Identifier(schema)
                                )
                            )
                            await second.execute("SET LOCAL lock_timeout='100ms'")
                            await second.execute("UPDATE facts SET version=999 WHERE id='message'")
                    except LockNotAvailable:
                        pass
                    else:
                        raise AssertionError("Expected lock timeout")
                assert await query("SELECT version FROM facts WHERE id='message'") == [(4,)]

            await check("bounded_lock_timeout_rollback", lock_timeout)

            async def cancel() -> None:
                async def long_write() -> None:
                    async with pool.connection() as conn, conn.transaction():
                        await conn.execute(
                            sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
                        )
                        await conn.execute("INSERT INTO facts(id,version) VALUES ('cancelled',1)")
                        await conn.execute("SELECT pg_sleep(8)")

                task = asyncio.create_task(long_write())
                try:
                    await wait_for_activity(lambda rows: any(row[2] == "PgSleep" for row in rows))
                    task.cancel()
                    result = await asyncio.gather(task, return_exceptions=True)
                    assert isinstance(result[0], asyncio.CancelledError)
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                assert not await query("SELECT id FROM facts WHERE id='cancelled'")
                assert await query("SELECT 42") == [(42,)]

            await check("server_observed_cancel_rolls_back_and_reuses_pool", cancel)
            passed = True
        finally:
            if created and passed:
                async with pool.connection() as conn:
                    await conn.execute(
                        sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
                    )
                report["schema_removed"] = True
            else:
                report["schema_removed"] = False
    assert pool.closed and all(conn.closed for conn in connections)
    async with await AsyncConnection.connect(**config.connection_kwargs()) as observer:
        rows = await (
            await observer.execute(
                "SELECT pid FROM pg_stat_activity WHERE pid=ANY(%s)", (sorted(backend_pids),)
            )
        ).fetchall()
        assert rows == []
        assert not await (
            await observer.execute("SELECT 1 FROM pg_namespace WHERE nspname=%s", (schema,))
        ).fetchall()
    checks.append("pool_connections_and_backends_closed_schema_removed")
    report.pop("active_check", None)


async def probe(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "status": "RUNNING",
        "python": platform.python_version(),
        "platform": platform.system(),
        "psycopg": psycopg.__version__,
        "psycopg_pool": psycopg_pool.__version__,
        "implementation": psycopg.pq.__impl__,
        "model_calls": 0,
        "model_tokens": 0,
        "targets": (["postgres_protocol"] if args.config else [])
        + (["mysql_readonly_inventory"] if args.inventory_source else []),
        "started_at": datetime.now(UTC).isoformat(),
        "checks": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with asyncio.timeout(args.timeout):
            assert sys.version_info[:3] == (3, 14, 7)
            if args.inventory_source:
                report["active_check"] = "source_read_only_inventory"
                report["source_inventory"] = await mysql_inventory(args.inventory_source)
                report.pop("active_check", None)
            if args.config:
                await protocol(PostgresConfig.read(args.config), report)
            report["status"] = "PASS"
    except asyncio.CancelledError:
        report["status"] = "CANCELLED"
        raise
    except Exception as exc:
        report["status"] = "FAIL"
        report["error_type"] = type(exc).__name__
        report["sqlstate"] = getattr(exc, "sqlstate", None)
        # Server errors can contain SQL values or connection details; retain structure only.
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "platform": report["platform"],
                "checks": len(report["checks"]),
                "active_check": report.get("active_check"),
                "error_type": report.get("error_type"),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["status"] == "PASS" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", type=Path, help="Explicit isolated PostgreSQL config JSON")
    parser.add_argument("--inventory-source", type=Path, help="Existing root; MySQL reads only")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()
    if not args.live or not (args.config or args.inventory_source):
        parser.error("Explicit --live and --config or --inventory-source required")
    if not 0 < args.timeout <= 600:
        parser.error("Timeout must be within (0,600] seconds")
    if args.output.exists() or any(
        args.output.resolve() == p.resolve() for p in (args.config,) if p
    ):
        parser.error("Output must be new and different from configuration")
    return run_postgres(probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
