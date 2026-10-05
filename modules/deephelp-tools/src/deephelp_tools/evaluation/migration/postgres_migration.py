"""Explicit isolated M25 snapshot/import/verification; never writes the old source."""

import argparse
import asyncio
import gzip
import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

from deephelp_app.adapters.persistence.postgres import PostgresConfig, PostgresPool
from deephelp_tools.evaluation.migration.mysql_source import MySQLSource
from deephelp_tools.probes.migration.postgres_probe import require_isolated

MIGRATIONS = (
    "008_mvp.sql",
    "010_memory.sql",
    "015_approval.sql",
    "018_flywheel.sql",
    "018_release.sql",
    "021_observation.sql",
)
LEGACY = {"dh_m15_checkpoints", "dh_m15_checkpoint_writes"}


def identifier(value: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise ValueError("Unsafe snapshot identifier")
    return '"' + value + '"'


def target(name: str) -> str:
    return ("m25_legacy." if name in LEGACY else "public.") + identifier(name)


def ddl() -> str:
    root = files("deephelp_app").joinpath("resources/migrations")
    return "\n".join(root.joinpath(name).read_text(encoding="utf-8") for name in MIGRATIONS) + (
        "\n" + files("deephelp_services").joinpath("resources/020_business.sql").read_text()
    )


def application_tables() -> set[str]:
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS (?:m25_legacy\.)?(dh_[a-z0-9_]+)", ddl()))


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def typed(value: Any, column_type: str) -> Any:
    if value is None:
        return None
    if column_type == "tinyint(1)":
        if value not in (0, 1, False, True):
            raise ValueError("Source boolean is outside its target range")
        return ["bool", bool(value)]
    if isinstance(value, datetime):
        when = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return ["utc", when.isoformat(timespec="microseconds")]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise ValueError("Source integer exceeds PostgreSQL bigint")
        return ["int", value]
    if isinstance(value, str):
        return ["text", value]
    raise ValueError("Unsupported persistent source value")


def untyped(value: Any) -> Any:
    if value is None:
        return None
    kind, body = value
    if kind == "utc":
        return datetime.fromisoformat(body)
    if kind == "decimal":
        return Decimal(body)
    if kind == "bytes":
        return bytes.fromhex(body)
    if kind in {"int", "text", "bool"}:
        return body
    raise ValueError("Unsupported snapshot value")


def row_values(row: Any, columns: list[list[Any]]) -> list[Any]:
    return [typed(value, column[1]) for value, column in zip(row, columns, strict=True)]


async def export_snapshot(root: Path, path: Path) -> dict[str, Any]:
    source = await MySQLSource.open(root)
    try:
        async with source.pool.acquire() as conn, conn.cursor() as cur:
            try:
                await cur.execute("SET SESSION time_zone='+00:00'")
                await cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY")
                await cur.execute(
                    "SELECT @@version,@@server_uuid,@@GLOBAL.gtid_executed,"
                    "@@transaction_isolation,@@transaction_read_only"
                )
                version, server, gtid, isolation, readonly = await cur.fetchone()
                if isolation != "REPEATABLE-READ" or readonly != 1:
                    raise ValueError("Stable read-only source snapshot is required")
                await cur.execute(
                    "SELECT TABLE_NAME,ENGINE FROM information_schema.tables "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_TYPE='BASE TABLE' ORDER BY TABLE_NAME"
                )
                names = await cur.fetchall()
                expected = application_tables()
                if {name for name, _ in names} != expected | {"p00_probe"}:
                    raise ValueError("Source table inventory differs; classify every table first")
                header: dict[str, Any] = {
                    "format": "deephelp-m25-snapshot-v1",
                    "id": uuid4().hex,
                    "started_at": datetime.now(UTC).isoformat(),
                    "version": version,
                    "source_server_uuid": server,
                    "gtid_executed": gtid,
                    "isolation": isolation,
                    "read_only": True,
                    "tables": [],
                }
                for name, engine in names:
                    identifier(name)
                    if engine != "InnoDB":
                        raise ValueError("Every source table must participate in the snapshot")
                    await cur.execute(
                        "SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLLATION_NAME "
                        "FROM information_schema.columns WHERE TABLE_SCHEMA=DATABASE() "
                        "AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                        (name,),
                    )
                    columns = [list(row) for row in await cur.fetchall()]
                    await cur.execute(
                        "SELECT COLUMN_NAME FROM information_schema.statistics "
                        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME='PRIMARY' "
                        "ORDER BY SEQ_IN_INDEX",
                        (name,),
                    )
                    primary = [row[0] for row in await cur.fetchall()]
                    if not primary:
                        raise ValueError("A stable primary key is required")
                    header["tables"].append(
                        {
                            "name": name,
                            "columns": columns,
                            "primary": primary,
                            "application": name in expected,
                        }
                    )
                await cur.execute(
                    "SELECT TABLE_NAME,COLUMN_NAME,REFERENCED_TABLE_NAME,REFERENCED_COLUMN_NAME "
                    "FROM information_schema.key_column_usage WHERE TABLE_SCHEMA=DATABASE() "
                    "AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY TABLE_NAME,ORDINAL_POSITION"
                )
                header["foreign_keys"] = [list(row) for row in await cur.fetchall()]
                summary: dict[str, Any] = {"tables": {}, "snapshot_id": header["id"]}
                # Exclusive creation keeps an interrupted export visible and never overwrites it.
                with (
                    path.open("xb") as raw,
                    gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream,
                ):
                    stream.write(canonical(header) + b"\n")
                    for table in header["tables"]:
                        name, columns = table["name"], table["columns"]
                        selected = ",".join(identifier(col[0]).replace('"', "`") for col in columns)
                        ordered = ",".join(
                            identifier(col).replace('"', "`") for col in table["primary"]
                        )
                        await cur.execute(f"SELECT {selected} FROM `{name}` ORDER BY {ordered}")
                        digest, count = hashlib.sha256(), 0
                        while rows := await cur.fetchmany(500):
                            for row in rows:
                                values = row_values(row, columns)
                                digest.update(canonical(values) + b"\n")
                                stream.write(canonical({"table": name, "values": values}) + b"\n")
                                count += 1
                        summary["tables"][name] = {"rows": count, "sha256": digest.hexdigest()}
                    summary["completed_at"] = datetime.now(UTC).isoformat()
                    stream.write(canonical({"complete": summary}) + b"\n")
                return {**summary, "source_read_only": True, "model_calls": 0}
            finally:
                await conn.rollback()
    finally:
        await source.aclose()


def read_snapshot(path: Path) -> tuple[dict[str, Any], dict[str, list[Any]], dict[str, Any], str]:
    with path.open("rb") as raw:
        file_hash = hashlib.file_digest(raw, "sha256").hexdigest()
    with gzip.open(path, "rb") as stream:
        header = json.loads(next(stream))
        if header["format"] != "deephelp-m25-snapshot-v1" or not header["read_only"]:
            raise ValueError("Unsupported source snapshot")
        tables = {t["name"]: t for t in header["tables"]}
        if len(tables) != len(header["tables"]) or set(tables) != application_tables() | {
            "p00_probe"
        }:
            raise ValueError("Snapshot inventory differs")
        rows: dict[str, list[Any]] = {name: [] for name in tables}
        summary = None
        for line in stream:
            item = json.loads(line)
            if summary is not None:
                raise ValueError("Data follows the completed snapshot")
            if "complete" in item:
                summary = item["complete"]
            else:
                name = item["table"]
                if len(item["values"]) != len(tables[name]["columns"]):
                    raise ValueError("Snapshot row width differs")
                rows[name].append(item["values"])
        if summary is None:
            raise ValueError("Snapshot export did not complete")
        for name, data in rows.items():
            digest = hashlib.sha256(b"".join(canonical(row) + b"\n" for row in data)).hexdigest()
            if summary["tables"][name] != {"rows": len(data), "sha256": digest}:
                raise ValueError("Snapshot content checksum differs")
            for row in data:
                for value in row:
                    untyped(value)
        return header, rows, summary, file_hash


async def schema(pool: PostgresPool) -> None:
    async with pool.acquire() as conn, conn.cursor() as cur:
        for statement in ddl().split(";"):
            if statement.strip():
                await cur.execute(statement)
        await cur.execute(
            "CREATE TABLE IF NOT EXISTS m25_legacy.migration_receipts ("
            "table_name TEXT PRIMARY KEY, snapshot_hash TEXT NOT NULL, "
            "committed_rows BIGINT NOT NULL CHECK(committed_rows>=0))"
        )
        await conn.commit()


async def verify(
    pool: PostgresPool, header: dict[str, Any], summary: dict[str, Any]
) -> dict[str, Any]:
    results = {}
    async with pool.acquire() as conn, conn.cursor() as cur:
        await cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for table in header["tables"]:
            name = table["name"]
            if not table["application"]:
                continue
            selected = ",".join(identifier(col[0]) for col in table["columns"])
            ordered = ",".join(
                identifier(col) + ' COLLATE "C"'
                if any(c[0] == col and ("char" in c[1] or "text" in c[1]) for c in table["columns"])
                else identifier(col)
                for col in table["primary"]
            )
            digest, count = hashlib.sha256(), 0
            # Server cursor bounds transfer size and keeps a slow SSH client from
            # leaving a complete multi-megabyte SELECT idle in a transaction.
            async with conn.cursor(name="m25_verify") as reader:
                await reader.execute(f"SELECT {selected} FROM {target(name)} ORDER BY {ordered}")
                while data := await reader.fetchmany(100):
                    for row in data:
                        digest.update(canonical(row_values(row, table["columns"])) + b"\n")
                        count += 1
            results[name] = {"rows": count, "sha256": digest.hexdigest()}
            if results[name] != summary["tables"][name]:
                raise ValueError("Target count or original typed bytes differ: " + name)
        # These relationships have no source FKs; prove the imported joins explicitly.
        checks = (
            "SELECT COUNT(*) FROM dh_m08_messages m LEFT JOIN dh_m08_runs r USING(run_id) "
            "WHERE r.run_id IS NULL",
            "SELECT COUNT(*) FROM dh_m08_runs r LEFT JOIN dh_m08_questions q USING(question_id) "
            "WHERE q.question_id IS NULL",
            "SELECT COUNT(*) FROM dh_m10_members n LEFT JOIN dh_m08_messages m "
            "USING(tenant_id,user_id,channel,message_id) WHERE m.message_id IS NULL",
            "SELECT COUNT(*) FROM dh_m10_members n LEFT JOIN dh_m08_questions q USING(question_id) "
            "WHERE q.question_id IS NULL OR n.tenant_id<>q.tenant_id OR n.user_id<>q.user_id "
            "OR n.session_id<>q.session_id",
            "SELECT COUNT(*) FROM dh_m18_active a LEFT JOIN dh_m18_releases r "
            "ON r.release_hash=a.active_hash WHERE a.active_hash IS NOT NULL "
            "AND r.release_hash IS NULL",
            "SELECT COUNT(*) FROM dh_m18_active a LEFT JOIN dh_m18_releases r "
            "ON r.release_hash=a.previous_hash WHERE a.previous_hash IS NOT NULL "
            "AND r.release_hash IS NULL",
            "SELECT COUNT(*) FROM dh_m18_run_releases p "
            "LEFT JOIN dh_m18_releases r USING(release_hash) "
            "WHERE r.release_hash IS NULL",
            "SELECT COUNT(*) FROM dh_m15_operations o LEFT JOIN dh_m08_runs r USING(run_id) "
            "LEFT JOIN dh_m08_questions q ON q.question_id=o.question_id "
            "WHERE r.run_id IS NULL OR q.question_id IS NULL",
        )
        relation_counts = []
        for query in checks:
            await cur.execute(query)
            relation_counts.append((await cur.fetchone())[0])
        if any(relation_counts):
            raise ValueError("Imported application references are incomplete")
        await conn.rollback()
    return {"tables": results, "relation_orphans": relation_counts, "original_bytes": "PASS"}


async def import_snapshot(pool: PostgresPool, path: Path, *, stop_after: int = 0) -> dict[str, Any]:
    header, rows, summary, digest = read_snapshot(path)
    await schema(pool)
    metadata = {t["name"]: t for t in header["tables"] if t["application"]}
    # Explicit compound FK ordering; remaining tables carry references validated after import.
    order = ["dh_m20_orders", "dh_m20_activities"] + sorted(
        set(metadata) - {"dh_m20_orders", "dh_m20_activities"}
    )
    batches = 0
    for name in order:
        table, data = metadata[name], rows[name]
        async with pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO m25_legacy.migration_receipts VALUES (%s,%s,0) "
                "ON CONFLICT(table_name) DO NOTHING",
                (name, digest),
            )
            await cur.execute(
                "SELECT snapshot_hash,committed_rows FROM m25_legacy.migration_receipts "
                "WHERE table_name=%s FOR UPDATE",
                (name,),
            )
            stored_hash, offset = await cur.fetchone()
            if stored_hash != digest or offset > len(data):
                raise ValueError("Resume requires the identical complete source snapshot")
            await conn.commit()
        columns = ",".join(identifier(c[0]) for c in table["columns"])
        placeholders = ",".join("%s" for _ in table["columns"])
        query = (
            f"INSERT INTO {target(name)} ({columns}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        )
        while offset < len(data):
            batch = data[offset : offset + 500]
            async with pool.acquire() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT snapshot_hash,committed_rows FROM m25_legacy.migration_receipts "
                    "WHERE table_name=%s FOR UPDATE",
                    (name,),
                )
                if await cur.fetchone() != (digest, offset):
                    raise ValueError("Another importer advanced this receipt")
                await cur.executemany(query, [tuple(untyped(v) for v in row) for row in batch])
                await cur.execute(
                    "UPDATE m25_legacy.migration_receipts SET committed_rows=%s "
                    "WHERE table_name=%s",
                    (offset + len(batch), name),
                )
                await conn.commit()
            offset += len(batch)
            batches += 1
            if stop_after and batches >= stop_after:
                return {
                    "status": "INTERRUPTED",
                    "committed_batches": batches,
                    "snapshot_sha256": digest,
                }
    result = await verify(pool, header, summary)
    async with pool.acquire() as conn, conn.cursor() as cur:
        for name, column in (
            ("dh_m10_outbox", "event_id"),
            ("dh_m15_audit", "event_id"),
            ("dh_m18_tasks", "job_id"),
            ("dh_m18_release_events", "event_id"),
        ):
            await cur.execute(
                f"SELECT setval(pg_get_serial_sequence(%s,%s), "
                f"COALESCE(MAX({identifier(column)}),1),MAX({identifier(column)}) IS NOT NULL) "
                f"FROM {target(name)}",
                ("public." + name, column),
            )
        await conn.commit()
    return {"status": "PASS", "snapshot_sha256": digest, "committed_batches": batches, **result}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    async with asyncio.timeout(args.timeout):
        if args.command == "export":
            return {"status": "PASS", **await export_snapshot(args.source_root, args.snapshot)}
        config = PostgresConfig.read(args.target_config)
        require_isolated(config)
        pool = await PostgresPool.open(config)
        try:
            if args.command == "import":
                return await import_snapshot(
                    pool, args.snapshot, stop_after=args.stop_after_batches
                )
            header, _, summary, digest = read_snapshot(args.snapshot)
            if args.command == "read":
                from deephelp_app.adapters.approval.approval_store import ApprovalRepository
                from deephelp_app.adapters.persistence.history import PostgresHistory
                from deephelp_app.domain.errors import AppError
                from deephelp_app.domain.models.common import VerifiedIdentity
                from deephelp_app.domain.models.conversation import RequestEnvelope
                from deephelp_services.business.repository import BusinessRepository
                from deephelp_tools.evaluation.migration.legacy_checkpoints import (
                    LegacyCheckpointArchive,
                )

                repo = ApprovalRepository(pool)
                async with pool.acquire() as conn, conn.cursor() as cur:
                    await cur.execute("SELECT body FROM dh_m08_messages ORDER BY run_id LIMIT 1")
                    request = RequestEnvelope.model_validate_json((await cur.fetchone())[0])
                before = await repo.lookup(request)
                after = await repo.accept(request)
                assert before == after and after is not None and not after.acquired
                history = PostgresHistory(pool)
                page = await history.messages(request.identity, request.session_id, 0, 100)
                assert any(item.run_id == after.run_id for item in page.items)
                try:
                    await history.messages(
                        VerifiedIdentity(tenant_id=uuid4().hex, user_id="other"),
                        request.session_id,
                        0,
                        10,
                    )
                except AppError as exc:
                    assert exc.status_code == 404
                else:
                    raise ValueError("Restored history ownership guard failed")
                async with pool.acquire() as conn, conn.cursor() as cur:
                    await cur.execute(
                        "SELECT tenant_id,user_id,record_id FROM dh_m20_orders "
                        "ORDER BY record_id LIMIT 1"
                    )
                    tenant, user, order_id = await cur.fetchone()
                    await cur.execute(
                        "SELECT thread_id FROM m25_legacy.dh_m15_checkpoints "
                        "ORDER BY thread_id LIMIT 1"
                    )
                    thread = (await cur.fetchone())[0]
                order = await BusinessRepository(repo).get(
                    "orders", VerifiedIdentity(tenant_id=tenant, user_id=user), order_id
                )
                assert order.data is not None
                assert await LegacyCheckpointArchive(pool).aget_tuple(
                    {"configurable": {"thread_id": thread}}
                )
                return {
                    "status": "PASS",
                    "snapshot_sha256": digest,
                    "original_receipt": True,
                    "protected_history": True,
                    "business_record": True,
                    "legacy_checkpoint": True,
                    "new_effects": 0,
                    "new_model_calls": 0,
                    "new_tool_calls": 0,
                }
            return {
                "status": "PASS",
                "snapshot_sha256": digest,
                **await verify(pool, header, summary),
            }
        finally:
            await pool.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("export", "import", "verify", "read"))
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--target-config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--stop-after-batches", type=int, default=0)
    args = parser.parse_args()
    if not 1 <= args.timeout <= 1800 or args.stop_after_batches < 0:
        parser.error("Invalid finite migration limits")
    if (args.command == "export" and not args.source_root) or (
        args.command != "export" and not args.target_config
    ):
        parser.error("An explicit source or target is required")
    if args.output.exists() or args.output.resolve() == args.snapshot.resolve():
        parser.error("A new separate report path is required")
    try:
        result = asyncio.run(run(args))
    except Exception as exc:
        result = {"status": "FAIL", "error": type(exc).__name__}
        if isinstance(exc, ValueError):
            result["reason"] = str(exc)
        args.output.write_bytes(canonical(result) + b"\n")
        raise SystemExit(1) from None
    args.output.write_bytes(canonical(result) + b"\n")
    raise SystemExit(0 if result["status"] == "PASS" else 2)


if __name__ == "__main__":
    main()
