"""Isolated HTTP credentials and cleanup of the SDK's process-lifetime SSE watcher."""

import asyncio
import json
from pathlib import Path

import pytest


@pytest.fixture
def credentials_path(tmp_path):
    # Each test owns its file, even when both HTTP services use the same credentials.
    path = tmp_path / "service.json"
    path.write_text(json.dumps({"bearer": "t" * 64, "delegation_key": "k" * 64}))
    return path


@pytest.fixture(autouse=True)
async def owned_sdk_watcher():
    before = set(asyncio.all_tasks())
    yield
    owned = []
    for task in set(asyncio.all_tasks()) - before:
        coroutine = task.get_coro()
        code = getattr(coroutine, "cr_code", None)
        if (
            getattr(coroutine, "__qualname__", "") == "_shutdown_watcher"
            and code
            and Path(code.co_filename).parent.name == "sse_starlette"
        ):
            owned.append(task)
    for task in owned:
        task.cancel()
    if owned:
        await asyncio.gather(*owned, return_exceptions=True)
