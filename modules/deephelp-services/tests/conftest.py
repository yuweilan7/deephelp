"""Close the SDK's process-lifetime SSE watcher when each isolated test loop ends."""

import asyncio
from pathlib import Path

import pytest


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
