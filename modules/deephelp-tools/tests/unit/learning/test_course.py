import socket

import pytest

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
