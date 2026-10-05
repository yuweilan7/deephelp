"""Official saver compatibility gate; run each phase in an independent process."""

# 官方saver兼容门禁，每个阶段由独立进程执行。
#
# 暂停/保存/重开/恢复都读实际checkpoint与原状态，不用内存结果证明持久协议。
# 显式schema/setup不改变当前运行图，checkpoint不是审批效果账本。

import argparse
import asyncio
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from deephelp_app.adapters.approval.postgres_checkpoints import official_checkpoints
from deephelp_app.adapters.persistence.postgres import PostgresConfig, PostgresPool
from deephelp_app.application.dense import atomic_json
from deephelp_tools.probes.migration.postgres_probe import require_isolated


class ProbeState(TypedDict):
    value: int


def waiting(state: ProbeState) -> dict[str, int]:
    value = interrupt({"kind": "synthetic_approval", "value": state["value"]})
    if value != "approved":
        raise ValueError("Invalid probe resume")
    return {"value": state["value"] + 1}


async def run(args: argparse.Namespace) -> None:
    if not args.live or args.output.exists():
        raise ValueError("Explicit --live and a new report are required")
    config = PostgresConfig.read(args.config)
    require_isolated(config)
    async with asyncio.timeout(args.timeout):
        pool = await PostgresPool.open(config)
        try:
            async with official_checkpoints(
                config,
                owner=pool.owner,
                schema=args.schema,
                setup=args.phase in {"pause", "legacy"},
            ) as saver:
                await phase(args, saver)
        finally:
            await pool.aclose()


async def phase(args: argparse.Namespace, saver: Any) -> None:
    graph = StateGraph(ProbeState).add_node("wait", waiting)
    graph.add_edge(START, "wait").add_edge("wait", END)
    workflow = graph.compile(checkpointer=saver)
    config: Any = {"configurable": {"thread_id": args.thread}}
    if args.phase == "legacy":
        from deephelp_tools.evaluation.migration.checkpoint_migration import (
            SnapshotCheckpoints,
            convert,
        )

        source = SnapshotCheckpoints(args.snapshot)
        candidates = [
            item
            for item in source.items.values()
            if item.checkpoint["channel_values"].get("value") == 41
            and any(ch == "__interrupt__" for _, ch, _ in item.pending_writes or [])
        ]
        if not candidates:
            raise ValueError("Snapshot has no genuine old interrupted probe state")
        paused = candidates[0]
        thread = paused.config["configurable"]["thread_id"]
        source.items = {key: item for key, item in source.items.items() if key[0] == thread}
        source.writes = {key: value for key, value in source.writes.items() if key[0] == thread}
        await convert(source, saver)
        state = await workflow.aget_state(paused.config)
        assert state.values["value"] == 41 and state.tasks[0].interrupts
        result = await workflow.ainvoke(
            Command(resume="approved"), paused.config, durability="sync"
        )
        assert result["value"] == 42
        latest: Any = {"configurable": {"thread_id": thread}}
        assert not (await workflow.aget_state(latest)).next
        assert (await workflow.ainvoke(None, latest, durability="sync"))["value"] == 42
    elif args.phase == "pause":
        assert await saver.aget_tuple(config) is None, "Use a new probe thread"
        result = await workflow.ainvoke({"value": 41}, config, durability="sync")
        assert result["__interrupt__"][0].value == {"kind": "synthetic_approval", "value": 41}
    else:
        state = await workflow.aget_state(config)
        assert state.tasks[0].interrupts and state.values["value"] == 41
        result = await workflow.ainvoke(Command(resume="approved"), config, durability="sync")
        assert result["value"] == 42 and not (await workflow.aget_state(config)).next
        replay = await workflow.ainvoke(None, config, durability="sync")
        assert replay["value"] == 42
        checkpoints = [item async for item in saver.alist(config)]
        assert len(checkpoints) >= 3 and checkpoints[0].parent_config
        assert any(item.pending_writes for item in checkpoints)
    atomic_json(args.output, {"status": "PASS", "phase": args.phase, "value": result["value"]})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["pause", "resume", "legacy"])
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--schema", default="m26_checkpoint_proof")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--thread", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=120)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
