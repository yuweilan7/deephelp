import asyncio
import socket

import httpx
import pytest
import uvicorn

from deephelp_app.app import create_app
from deephelp_app.settings import Settings
from deephelp_app.trace import MemoryTrace

pytestmark = pytest.mark.e2e


async def test_loopback_uvicorn_exits_normally_and_closes_application_pool(message):
    """Real local HTTP server shutdown, synthetic application only."""
    trace = MemoryTrace()
    app = create_app(Settings(mode="test"), trace=trace)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    config = uvicorn.Config(
        app, log_config=None, access_log=False, lifespan="on", timeout_graceful_shutdown=2
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    pytest.fail("Server stopped before startup")
                await asyncio.sleep(0.01)
        pool = app.state.resources.client
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", trust_env=False
        ) as client:
            assert (await client.get("/health")).status_code == 200
            response = await client.post("/converse", json=message)
            assert response.status_code == 501
            assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"
        server.should_exit = True
        async with asyncio.timeout(5):
            await task
        assert pool.is_closed
        assert trace.closed
    finally:
        server.should_exit = True
        if not task.done():
            async with asyncio.timeout(5):
                await task
        listener.close()
