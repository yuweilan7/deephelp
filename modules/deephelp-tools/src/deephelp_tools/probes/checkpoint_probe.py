"""Minimal real MySQL/locked LangGraph compatibility gate; run each phase in a new process."""

import argparse
import asyncio
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from deephelp_app.adapters.checkpoints import MySQLSaver, migrate_m15
from deephelp_app.adapters.ledger import MySQLLedger
from deephelp_app.application.dense import atomic_json


class ProbeState(TypedDict):
    value: int


def waiting(state: ProbeState) -> dict[str, int]:
    value = interrupt({"kind": "synthetic_approval", "value": state["value"]})
    if value != "approved":
        raise ValueError("Invalid probe resume")
    return {"value": state["value"] + 1}


async def run(args: argparse.Namespace) -> None:
    ledger = await MySQLLedger.open(Path.cwd())
    try:
        await migrate_m15(ledger.pool)
        saver = MySQLSaver(ledger.pool)
        graph = StateGraph(ProbeState).add_node("wait", waiting)
        graph.add_edge(START, "wait").add_edge("wait", END)
        workflow = graph.compile(checkpointer=saver)
        config: Any = {"configurable": {"thread_id": args.thread}}
        if args.phase == "pause":
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
    finally:
        await ledger.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["pause", "resume"])
    parser.add_argument("--thread", required=True)
    parser.add_argument("--output", required=True, type=Path)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
