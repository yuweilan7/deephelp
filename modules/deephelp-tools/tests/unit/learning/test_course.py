import socket

import pytest

from deephelp_tools.learning.course.__main__ import run_topic
from deephelp_tools.learning.course.basics import syntax_demo, types_demo
from deephelp_tools.learning.course.offline import offline_only
from deephelp_tools.learning.course.request import request_demo

pytestmark = pytest.mark.unit


async def test_course_types_and_syntax_have_observable_results():
    assert (await types_demo())["isolated_lists"] is True
    assert (await syntax_demo())["events"][-1] == "closed"


def test_course_guard_denies_loopback_too_and_restores_owner_patch():
    original = socket.getaddrinfo
    with offline_only(), pytest.raises(RuntimeError, match="课程默认离线"):
        socket.getaddrinfo("127.0.0.1", 80)
    assert socket.getaddrinfo is original


@pytest.mark.integration
async def test_course_reuses_engine_and_fixed_stdio_without_network():
    with offline_only():
        result = await request_demo()
    assert result["remote_model_calls"] == 0
    assert result["evidence_kind"] == "synthetic_mechanism_replay"


@pytest.mark.parametrize("topic", ["awaitables", "coordination", "cancellation", "resources"])
async def test_async_course_barriers_join_every_owned_task(topic):
    import asyncio

    before = set(asyncio.all_tasks())
    result = await run_topic(topic)
    assert result
    assert set(asyncio.all_tasks()) == before


async def test_resource_child_startup_failure_is_joined_and_process_exits(monkeypatch):
    import asyncio

    original = asyncio.create_subprocess_exec
    processes = []

    async def invalid_ready(*args, **kwargs):
        process = await original(
            args[0],
            "-u",
            "-c",
            "import sys; print('wrong', flush=True); sys.stdin.readline()",
            **kwargs,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", invalid_ready)
    before = set(asyncio.all_tasks())
    with pytest.raises(ExceptionGroup):
        await run_topic("resources")
    assert processes and processes[0].returncode == 0
    assert set(asyncio.all_tasks()) == before


@pytest.mark.parametrize("topic", ["retrieval", "memory", "observations", "evaluation"])
async def test_agent_course_runs_only_fixed_offline_evidence(topic):
    result = await run_topic(topic)
    assert result["remote_model_calls"] == 0
    assert str(result["evidence_kind"]).startswith(("synthetic", "frozen_data"))
