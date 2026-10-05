"""Offline integrity boundaries of old typed state conversion."""

from collections import defaultdict
from copy import deepcopy

import pytest
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
    empty_checkpoint,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from deephelp_app.adapters.approval.postgres_checkpoints import PostgresCheckpoints
from deephelp_tools.evaluation.migration import checkpoint_migration as migration


@pytest.mark.parametrize("reserved_first", [True, False])
def test_reserved_writes_fill_ordinal_gap_without_changing_indices(reserved_first):
    rows = [
        (-4, "__resume__", "approved", "wait"),
        (-3, "__interrupt__", [], "wait"),
        (1 if reserved_first else 0, "value", 42, "wait"),
    ]
    writes, path = migration.ordered_writes(rows)
    reconstructed = [
        (WRITES_IDX_MAP.get(channel, i), channel, value, path)
        for i, (channel, value) in enumerate(writes)
    ]
    assert sorted(reconstructed) == sorted(rows)


@pytest.mark.parametrize(
    "rows",
    [
        [(3, "value", 42, "")],
        [(-7, "value", 42, "")],
        [(-4, "__interrupt__", [], "")],
        [(0, "value", 42, "a"), (1, "status", "OK", "b")],
        [(0, "value", 42, ""), (0, "status", "OK", "")],
    ],
)
def test_unrepresentable_task_indices_and_paths_are_rejected(rows):
    with pytest.raises(ValueError):
        migration.ordered_writes(rows)


def test_fingerprint_preserves_tuple_bytes_and_versions_but_ignores_dict_order():
    assert migration.fingerprint({"x": (b"a",), "y": 1}) == migration.fingerprint(
        {"y": 1, "x": (b"a",)}
    )
    assert migration.fingerprint((42,)) != migration.fingerprint([42])
    assert migration.fingerprint({"version": 1}) != migration.fingerprint({"version": "1"})


@pytest.fixture
def source():
    source = object.__new__(migration.SnapshotCheckpoints)
    source.source_hash = "f" * 64
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"value": 41}
    checkpoint["channel_versions"] = {"value": 7}
    checkpoint["versions_seen"] = {"wait": {"value": 6}}
    config = {
        "configurable": {
            "thread_id": "synthetic",
            "checkpoint_ns": "",
            "checkpoint_id": checkpoint["id"],
        }
    }
    item = CheckpointTuple(config, checkpoint, {"source": "loop", "step": 1}, None, [])
    key = ("synthetic", "", checkpoint["id"])
    source.items = {key: item}
    source.writes = defaultdict(lambda: defaultdict(list))
    source.writes[key]["task"] = [(0, "value", 42, "wait")]
    return source


@pytest.fixture
def target(monkeypatch):
    saver = InMemorySaver()
    raw = defaultdict(lambda: defaultdict(list))
    original = saver.aput_writes

    async def write(config, writes, task, path=""):
        await original(config, writes, task, path)
        c = config["configurable"]
        raw[(c["thread_id"], c["checkpoint_ns"], c["checkpoint_id"])][task] = [
            (WRITES_IDX_MAP.get(ch, i), ch, value, path) for i, (ch, value) in enumerate(writes)
        ]

    async def read(saver, item):
        c = item.config["configurable"]
        return dict(raw[(c["thread_id"], c["checkpoint_ns"], c["checkpoint_id"])])

    saver.aput_writes = write
    monkeypatch.setattr(migration, "target_writes", read)
    return saver, raw


async def test_repeated_conversion_keeps_original_ids_versions_and_task_path(source, target):
    saver, _ = target
    first = await migration.convert(source, saver)
    second = await migration.convert(source, saver)
    verified = await migration.convert(source, saver, verify_only=True)
    assert first["new_checkpoints"] == 1 and second["new_checkpoints"] == 0
    assert first["threads"] == second["threads"] == verified["threads"]


async def test_partial_conversion_continues_missing_pending_writes(source, target):
    saver, _ = target
    item = next(iter(source.items.values()))
    await saver.aput(
        {"configurable": {"thread_id": "synthetic", "checkpoint_ns": ""}},
        deepcopy(item.checkpoint),
        item.metadata,
        item.checkpoint["channel_versions"],
    )
    assert (await migration.convert(source, saver))["new_checkpoints"] == 0


async def test_conflicting_target_state_is_not_overwritten(source, target):
    saver, _ = target
    await migration.convert(source, saver)
    item = next(iter(source.items.values()))
    source.items[next(iter(source.items))] = item._replace(
        checkpoint=deepcopy(item.checkpoint) | {"channel_values": {"value": 999}}
    )
    with pytest.raises(ValueError, match="conflict"):
        await migration.convert(source, saver)
    assert (await saver.aget_tuple(item.config)).checkpoint["channel_values"]["value"] == 41


async def test_progressed_target_thread_is_not_rewound(source, target):
    saver, _ = target
    await migration.convert(source, saver)
    item = next(iter(source.items.values()))
    new = empty_checkpoint()
    new["id"] = "zz-next"
    await saver.aput(item.config, new, {"source": "loop", "step": 2}, {})
    with pytest.raises(ValueError, match="progressed"):
        await migration.convert(source, saver)


async def test_conflicting_target_write_is_not_overwritten(source, target):
    saver, raw = target
    await migration.convert(source, saver)
    key = next(iter(source.items))
    raw[key]["task"] = [(0, "value", 99, "foreign")]
    with pytest.raises(ValueError, match="write conflict"):
        await migration.convert(source, saver)


async def test_old_integer_thread_resumes_without_mixing_official_string_versions():
    from typing import TypedDict

    class State(TypedDict):
        value: int

    def wait(state):
        assert interrupt({"value": state["value"]}) == "approved"
        return {"value": state["value"] + 1}

    old = InMemorySaver()
    old.get_next_version = lambda current, channel: BaseCheckpointSaver.get_next_version(
        old, current, channel
    )
    graph = StateGraph(State).add_node("wait", wait)
    graph.add_edge(START, "wait").add_edge("wait", END)
    config = {"configurable": {"thread_id": "old-integer-thread"}}
    await graph.compile(checkpointer=old).ainvoke({"value": 41}, config)
    before = (await old.aget_tuple(config)).checkpoint
    assert all(type(v) is int for v in before["channel_versions"].values())
    old.get_next_version = lambda current, _: str(int(current or 0) + 1)
    bridged = PostgresCheckpoints(old, None)
    restored = graph.compile(checkpointer=bridged)
    result = await restored.ainvoke(Command(resume="approved"), config, durability="sync")
    assert result["value"] == 42 and not (await restored.aget_state(config)).next
    assert all(
        type(v) is int
        for v in (await old.aget_tuple(config)).checkpoint["channel_versions"].values()
    )
    assert bridged.get_next_version(None, None) == "1"
    assert bridged.get_next_version("1", None) == "2"
