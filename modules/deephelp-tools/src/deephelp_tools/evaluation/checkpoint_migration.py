"""Lossless, restartable conversion through official aput/aput_writes only.

Input is a completed, checksum-validated read-only MySQL snapshot from M25.
Run with the destination offline; never import over an executing thread.
"""

import argparse
import asyncio
import copy
import hashlib
from collections import defaultdict
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import WRITES_IDX_MAP, CheckpointTuple
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from deephelp_app.adapters.checkpoints import configuration
from deephelp_app.adapters.postgres import PostgresConfig, run_postgres
from deephelp_app.adapters.postgres_checkpoints import PostgresCheckpoints, official_checkpoints
from deephelp_app.application.dense import atomic_json
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


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.live or args.output.exists():
        raise ValueError("Explicit --live and a new report path are required")
    config = PostgresConfig.read(args.config)
    require_isolated(config)
    source = SnapshotCheckpoints(args.snapshot)
    async with asyncio.timeout(args.timeout):
        async with official_checkpoints(
            config, schema=args.schema, setup=args.action == "import"
        ) as saver:
            return await convert(source, saver, verify_only=args.action == "verify")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["import", "verify"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--schema", default="m26_checkpoint_proof")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=1200)
    args = parser.parse_args()
    atomic_json(args.output, run_postgres(run(args)))


if __name__ == "__main__":
    main()
