"""Tests permit synthetic loopback HTTP only; external DNS/connections fail closed."""

import pytest

# Remote network isolation is installed by root conftest before test collection.


@pytest.fixture
def message():
    return {
        "channel": "local",
        "session_id": "session-1",
        "message_id": "message-1",
        "raw_text": "合成测试：查询订单",
        "occurred_at": "2026-09-13T01:00:00+08:00",
    }
