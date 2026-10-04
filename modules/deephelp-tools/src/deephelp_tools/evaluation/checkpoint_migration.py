"""Lossless, restartable conversion through official aput/aput_writes only.

Input is a completed, checksum-validated read-only MySQL snapshot from M25.
Run with the destination offline; never import over an executing thread.
"""

import argparse
import asyncio
import copy
import hashlib
import json
from collections import defaultdict
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import httpx
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import WRITES_IDX_MAP, CheckpointTuple
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from deephelp_app.adapters.checkpoints import configuration
from deephelp_app.adapters.postgres import PostgresConfig, run_postgres
from deephelp_app.adapters.postgres_checkpoints import (
    SCHEMA,
    PostgresCheckpoints,
    official_checkpoints,
)
from deephelp_app.application.dense import atomic_json
from deephelp_app.domain.models import OperationRecord, OperationStatus, Question
from deephelp_tools.evaluation.postgres_migration import canonical, read_snapshot, untyped
from deephelp_tools.probes.postgres_probe import require_isolated


def normalized(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [type(value).__name__, [normalized(v) for v in value]]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    if isinstance(value, (date, datetime)):
        return [type(value).__name__, value.isoformat()]
    if isinstance(value, BaseModel):
        return [type(value).__qualname__, normalized(value.model_dump())]
    if is_dataclass(value) and not isinstance(value, type):
        return [
            type(value).__qualname__,
            {f.name: normalized(getattr(value, f.name)) for f in fields(value)},
        ]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("Unqualified checkpoint value type: " + type(value).__qualname__)


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(normalized(value))).hexdigest()


def ordered_writes(rows: list[tuple[int, str, Any, str]]) -> tuple[list[tuple[str, Any]], str]:
    """Place reserved writes in ordinal gaps; preserve every positive/negative idx."""
    if len({row[3] for row in rows}) != 1 or len({row[0] for row in rows}) != len(rows):
        raise ValueError("Task paths/indices cannot be represented by one public write call")
    reserved = []
    ordinary = {}
    for index, channel, value, _ in sorted(rows):
        if channel in WRITES_IDX_MAP:
            if index != WRITES_IDX_MAP[channel]:
                raise ValueError("Reserved write index differs from the locked codec")
            reserved.append((channel, value))
        elif index < 0 or index >= len(rows):
            raise ValueError("Sparse write indices cannot be represented by public API")
        else:
            ordinary[index] = (channel, value)
    result = []
    for index in range(len(rows)):
        if index in ordinary:
            result.append(ordinary[index])
        elif reserved:
            result.append(reserved.pop(0))
        else:
            raise ValueError("A missing write cannot be silently invented")
    return result, rows[0][3]


class SnapshotCheckpoints:
    """Read-only old typed tuple interface over the verified stable snapshot."""

    def __init__(self, path: Path) -> None:
        _, rows, _, self.source_hash = read_snapshot(path)
        self.serde = JsonPlusSerializer()
        self.items: dict[tuple[str, str, str], CheckpointTuple] = {}
        self.writes: dict[tuple[str, str, str], dict[str, list[Any]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for encoded in rows["dh_m15_checkpoint_writes"]:
            thread, ns, cid, task, idx, channel, kind, blob, task_path = map(untyped, encoded)
            self.writes[thread, ns, cid][task].append(
                (idx, channel, self.serde.loads_typed((kind, blob)), task_path)
            )
        for encoded in rows["dh_m15_checkpoints"]:
            thread, ns, cid, parent, ct, cp, mt, md = map(untyped, encoded)
            key = (thread, ns, cid)
            if key in self.items:
                raise ValueError("Duplicate source checkpoint key")
            checkpoint = self.serde.loads_typed((ct, cp))
            if checkpoint["id"] != cid or checkpoint["v"] != 4:
                raise ValueError("Source checkpoint ID/version differs")
            self.items[key] = CheckpointTuple(
                configuration(thread, ns, cid),
                checkpoint,
                self.serde.loads_typed((mt, md)),
                configuration(thread, ns, parent) if parent else None,
                [
                    (task, channel, value)
                    for task, values in sorted(self.writes[key].items())
                    for _, channel, value, _ in sorted(values)
                ],
            )
        # Fail before any target write when source integrity cannot be preserved.
        for key, item in self.items.items():
            if (
                item.parent_config
                and (key[0], key[1], item.parent_config["configurable"]["checkpoint_id"])
                not in self.items
            ):
                raise ValueError("Source parent is missing")
            for values in self.writes[key].values():
                ordered_writes(values)
        if set(self.writes) - set(self.items):
            raise ValueError("Orphan source pending writes")
        blobs: dict[Any, str] = {}
        for (thread, ns, _), item in self.items.items():
            for channel, value in item.checkpoint["channel_values"].items():
                if channel not in item.checkpoint["channel_versions"]:
                    raise ValueError("Source channel has no version")
                blob_key = (thread, ns, channel, str(item.checkpoint["channel_versions"][channel]))
                signature = fingerprint(value)
                if blob_key in blobs and blobs[blob_key] != signature:
                    raise ValueError("Source reuses a channel version for different content")
                blobs[blob_key] = signature

    async def aget_tuple(self, config: Any) -> CheckpointTuple | None:
        c = config["configurable"]
        if cid := c.get("checkpoint_id"):
            return self.items.get((c["thread_id"], c.get("checkpoint_ns", ""), cid))
        items = [
            v
            for (t, n, _), v in sorted(self.items.items())
            if t == c["thread_id"] and n == c.get("checkpoint_ns", "")
        ]
        return items[-1] if items else None


async def target_writes(saver: PostgresCheckpoints, item: CheckpointTuple) -> dict[str, list[Any]]:
    # Read the installed official schema to prove idx/task_path, which get_tuple
    # does not expose. Writes still exclusively use the official public API.
    from deephelp_app.adapters.postgres import on_pool_loop

    async def read() -> dict[str, list[Any]]:
        c = item.config["configurable"]
        pool = cast(AsyncConnectionPool[AsyncConnection[Any]], saver.official.conn)
        async with pool.connection() as connection:
            rows = await (
                await connection.execute(
                    "SELECT task_id,idx,channel,type,blob,task_path FROM checkpoint_writes "
                    "WHERE thread_id=%s AND checkpoint_ns=%s AND checkpoint_id=%s "
                    "ORDER BY task_id,idx",
                    (c["thread_id"], c["checkpoint_ns"], c["checkpoint_id"]),
                )
            ).fetchall()
        result: dict[str, list[Any]] = defaultdict(list)
        for r in rows:
            result[r["task_id"]].append(
                (
                    r["idx"],
                    r["channel"],
                    saver.serde.loads_typed((r["type"], r["blob"])),
                    r["task_path"],
                )
            )
        return dict(result)

    return await on_pool_loop(saver.owner, read())


def core(item: CheckpointTuple) -> Any:
    return (item.config, item.checkpoint, item.metadata, item.parent_config)


async def convert(
    source: SnapshotCheckpoints, saver: PostgresCheckpoints, *, verify_only: bool = False
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "source_sha256": source.source_hash,
        "threads": [],
        "checkpoint_count": len(source.items),
        "new_checkpoints": 0,
    }
    threads: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for key, item in sorted(source.items.items()):
        threads[key[:2]].append((key, item))
    for (thread, ns), items in threads.items():
        existing = [
            x
            async for x in saver.alist({"configurable": {"thread_id": thread, "checkpoint_ns": ns}})
        ]
        source_ids = {key[2] for key, _ in items}
        if any(x.checkpoint["id"] not in source_ids for x in existing):
            raise ValueError("Destination thread has progressed; import cannot overwrite it")
        for key, item in items:
            old = await saver.aget_tuple(item.config)
            if old and fingerprint(core(old)) != fingerprint(core(item)):
                raise ValueError("Destination checkpoint conflict; no overwrite")
            raw = await target_writes(saver, item)
            for task, values in raw.items():
                for value in values:
                    if value not in source.writes[key].get(task, []):
                        raise ValueError("Destination write conflict; no overwrite")
            if not verify_only:
                # Supply all versions: the official saver decides inline/blob
                # representation. Original IDs/parents/versions stay untouched.
                await saver.aput(
                    cast(
                        RunnableConfig,
                        item.parent_config
                        or {"configurable": {"thread_id": thread, "checkpoint_ns": ns}},
                    ),
                    copy.deepcopy(item.checkpoint),
                    copy.deepcopy(item.metadata),
                    item.checkpoint["channel_versions"],
                )
                report["new_checkpoints"] += int(old is None)
                for task, values in source.writes[key].items():
                    writes, path = ordered_writes(values)
                    await saver.aput_writes(item.config, writes, task, path)
            restored = await saver.aget_tuple(item.config)
            if restored is None or fingerprint(core(restored)) != fingerprint(core(item)):
                raise ValueError("Official readback differs from source state")
            if fingerprint(await target_writes(saver, item)) != fingerprint(
                {t: sorted(v) for t, v in source.writes[key].items()}
            ):
                raise ValueError("Task/index/path/value readback differs")
        report["threads"].append(
            {
                "thread_id": thread,
                "namespace": ns,
                "checkpoints": len(items),
                "source_digest": fingerprint(
                    [
                        (core(item), {t: sorted(v) for t, v in source.writes[key].items()})
                        for key, item in items
                    ]
                ),
                "latest_id": items[-1][0][2],
                "status": "PASS",
            }
        )
    return report | {"status": "PASS"}


async def audit_history(
    source: SnapshotCheckpoints, saver: PostgresCheckpoints, pool: Any, snapshot: Path
) -> dict[str, Any]:
    """Read actual graph state and original ledger bindings, without resuming."""
    from deephelp_app.adapters.approval_store import ApprovalRepository
    from deephelp_app.adapters.synthetic_rights import RightsClient, binding_hash
    from deephelp_app.application.approval import ApprovalService
    from deephelp_app.domain.execution import ExecutionBudget

    _, rows, _, _ = read_snapshot(snapshot)
    repo = ApprovalRepository(pool)
    result = []
    for row in rows["dh_m15_operations"]:
        original = OperationRecord.model_validate_json(untyped(row[6]))
        op = await repo.get(original.plan.operation_id)
        if binding_hash(op) != binding_hash(original):
            raise ValueError("Original operation/parameter/subject/SOP binding differs")
        if op.pending_response.versions != original.pending_response.versions:
            raise ValueError("Original runtime/asset binding differs")
        service = ApprovalService(
            repo, cast(RightsClient, None), op.plan.snapshot_hash, saver=saver
        )
        config = service.config(op)
        state = await service.graph(ExecutionBudget.start(30, 8, 0)).aget_state(config)
        if state.values and state.values.get("operation_id") != op.plan.operation_id:
            raise ValueError("Graph operation reference differs")
        item = await saver.aget_tuple(config)
        result.append(
            {
                "operation_id": op.plan.operation_id,
                "run_id": op.run_id,
                "operation_status": op.status.value,
                "approval_status": op.approval_status.value,
                "checkpoint": item.checkpoint["id"] if item else None,
                "next": list(state.next),
                "pending_writes": len(item.pending_writes or []) if item else 0,
                "interrupts": sum(len(t.interrupts) for t in state.tasks),
                "binding_sha256": binding_hash(op),
                "release_manifest": op.pending_response.versions.release_manifest,
                "checkpoint_gap": item is None,
                "status": "PASS",
            }
        )
    return {
        "status": "PASS",
        "scope": "read-only graph/ledger audit; no recovery/effects",
        "source_sha256": source.source_hash,
        "operations": result,
        "model_calls": 0,
    }


async def recover_history(
    source: SnapshotCheckpoints,
    saver: PostgresCheckpoints,
    pool: Any,
    snapshot: Path,
    port: int,
    key: bytes,
    output: Path | None = None,
) -> dict[str, Any]:
    """Recover the unchanged M25 authority; expired unknowns are queried, never resent.

    This qualification runs only against an isolated copy. Every original effect
    and call count is checked before mutation; current model planning is absent.
    """
    from deephelp_app.adapters.approval_store import TERMINAL, ApprovalRepository
    from deephelp_app.adapters.synthetic_rights import RightsClient, binding_hash
    from deephelp_app.application.approval import ApprovalService
    from deephelp_app.bootstrap.release_runtime import ReleasedApprovals
    from deephelp_app.domain.errors import AppError
    from deephelp_app.domain.execution import ExecutionBudget
    from deephelp_tools.probes.approval_probe import counts, resume_command

    _, rows, _, _ = read_snapshot(snapshot)
    originals = [
        OperationRecord.model_validate_json(untyped(r[6])) for r in rows["dh_m15_operations"]
    ]
    repo = ApprovalRepository(pool)
    questions = {untyped(r[0]): untyped(r[6]) for r in rows["dh_m08_questions"]}
    invalid = set()
    expected: dict[str, dict[str, int]] = {
        op.plan.operation_id: {"effects": 0, "execute_calls": 0, "query_calls": 0}
        for op in originals
    }
    for row in rows["dh_m15_synthetic_effects"]:
        expected[untyped(row[0])]["effects"] += 1
    for row in rows["dh_m15_synthetic_calls"]:
        expected[untyped(row[1])][untyped(row[2]) + "_calls"] += 1
    # Complete the read-only preflight for ALL operations before changing one.
    await audit_history(source, saver, pool, snapshot)
    for original in originals:
        current = await repo.get(original.plan.operation_id)
        if (
            current != original
            or await counts(repo, original.plan.operation_id)
            != expected[original.plan.operation_id]
        ):
            raise ValueError("Recovery requires a fresh original ledger/effect copy")
        if original.status not in TERMINAL and original.expires_at > datetime.now(UTC):
            raise ValueError("Historical fixed recovery requires expired, non-executable plans")
        if original.status not in TERMINAL and original.pending_response.versions.release_manifest:
            raise ValueError("A nonterminal complete release requires its original runtime")
        async with pool.acquire() as conn, conn.cursor() as cursor:
            await cursor.execute(
                "SELECT body FROM dh_m08_questions WHERE question_id=%s",
                (original.plan.question_id,),
            )
            question_row = await cursor.fetchone()
            await conn.rollback()
        if not question_row or json.loads(question_row[0]) != json.loads(
            questions[original.plan.question_id]
        ):
            raise ValueError("Original question/authority differs before recovery")
        try:
            Question.model_validate_json(questions[original.plan.question_id])
        except ValueError:
            invalid.add(original.plan.operation_id)

    class TerminalRouter:
        async def entry(self, identity: str) -> Any:
            raise AssertionError("Terminal recovery must not load a replacement release")

    results = []
    async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
        rights = RightsClient(client, port, key)
        for original in originals:
            operation = original.plan.operation_id
            service = ApprovalService(repo, rights, original.plan.snapshot_hash, saver=saver)
            error = None
            try:
                if original.pending_response.versions.release_manifest:
                    recovered = await ReleasedApprovals(TerminalRouter(), cast(Any, repo)).resume(
                        original.plan.identity,
                        operation,
                        resume_command(original),
                        ExecutionBudget.start(60, 8, 0),
                    )
                else:
                    recovered = await service.resume(
                        original.plan.identity,
                        operation,
                        resume_command(original),
                        ExecutionBudget.start(60, 8, 0),
                    )
            except AppError as exc:
                if original.status != OperationStatus.UNKNOWN or exc.status_code != 409:
                    raise
                error = exc.code.value
                recovered = await repo.get(operation)
            except ValueError:
                if operation not in invalid:
                    raise
                error = "SOURCE_QUESTION_CONTRACT_INVALID"
                recovered = await repo.get(operation)
            after = await counts(repo, operation)
            if recovered.plan != original.plan or binding_hash(recovered) != binding_hash(original):
                raise ValueError("Recovery changed the signed plan/authority")
            if recovered.pending_response.versions != original.pending_response.versions:
                raise ValueError("Recovery changed the original runtime/assets")
            if (
                after["effects"] != expected[operation]["effects"]
                or after["execute_calls"] != expected[operation]["execute_calls"]
            ):
                raise ValueError("Historical recovery redispatched or changed an effect")
            state = await service.graph(ExecutionBudget.start(30, 8, 0)).aget_state(
                service.config(recovered)
            )
            if recovered.status in TERMINAL:
                if state.next:
                    raise ValueError("Committed ledger checkpoint gap remains open")
                await service.resume(
                    original.plan.identity,
                    operation,
                    resume_command(original),
                    ExecutionBudget.start(30, 8, 0),
                )
                if await counts(repo, operation) != after:
                    raise ValueError("Terminal replay changed downstream calls/effects")
            if original.status == OperationStatus.PREPARED and operation not in invalid:
                if recovered.status != OperationStatus.CANCELLED:
                    raise ValueError("Expired pending/approved plan must close without execution")
            if operation in invalid:
                if recovered != original or after != expected[operation] or error is None:
                    raise ValueError("Invalid original authority must remain unchanged/rejected")
                outcome = "SOURCE_BINDING_INVALID_RETAINED"
            elif original.status == OperationStatus.UNKNOWN:
                if (
                    recovered.status != OperationStatus.UNKNOWN
                    or after["query_calls"] != expected[operation]["query_calls"] + 1
                ):
                    raise ValueError("Expired unknown must query once and remain recoverable")
                outcome = "ABSENT_REDISPATCH_REJECTED"
            else:
                if after["query_calls"] != expected[operation]["query_calls"]:
                    raise ValueError("Historical terminal/prepared recovery called downstream")
                outcome = "TERMINAL_REPLAY" if original.status in TERMINAL else "EXPIRED_NO_EFFECT"
            results.append(
                {
                    "operation_id": operation,
                    "before_status": original.status.value,
                    "after_status": recovered.status.value,
                    "before_approval": original.approval_status.value,
                    "after_approval": recovered.approval_status.value,
                    "outcome": outcome,
                    "guard_error": error,
                    "before": expected[operation],
                    "after": after,
                    "checkpoint": state.config,
                    "next": list(state.next),
                    "binding_sha256": binding_hash(recovered),
                    "release_manifest": original.pending_response.versions.release_manifest,
                    "plan_and_versions_unchanged": True,
                    "status": "PASS",
                }
            )
            if output is not None:
                atomic_json(
                    output,
                    {
                        "status": "RUNNING",
                        "operations": results,
                        "source_sha256": source.source_hash,
                        "model_calls": 0,
                    },
                )
    return {
        "status": "PASS",
        "source_sha256": source.source_hash,
        "scope": "original expired historical authority; no new effects/model planning",
        "operations": results,
        "model_calls": 0,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.live or args.output.exists():
        raise ValueError("Explicit --live and a new report path are required")
    config = PostgresConfig.read(args.config)
    require_isolated(config)
    source = SnapshotCheckpoints(args.snapshot)
    if args.action == "recover" and (
        args.schema != SCHEMA or not args.port or not args.key or len(args.key.read_bytes()) != 32
    ):
        raise ValueError(
            "Recovery requires the formal schema and explicit synthetic rights port/key"
        )
    async with asyncio.timeout(args.timeout):
        async with official_checkpoints(
            config, schema=args.schema, setup=args.action == "import"
        ) as saver:
            if args.action in {"audit", "recover"}:
                from deephelp_app.adapters.postgres import PostgresPool

                pool = await PostgresPool.open(config)
                try:
                    if args.action == "recover":
                        return await recover_history(
                            source, saver, pool, args.snapshot, args.port, args.key.read_bytes()
                        )
                    return await audit_history(source, saver, pool, args.snapshot)
                finally:
                    await pool.aclose()
            return await convert(source, saver, verify_only=args.action == "verify")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["import", "verify", "audit", "recover"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--schema", default="m26_checkpoint_proof")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("--port", type=int)
    parser.add_argument("--key", type=Path)
    args = parser.parse_args()
    atomic_json(args.output, run_postgres(run(args)))


if __name__ == "__main__":
    main()
