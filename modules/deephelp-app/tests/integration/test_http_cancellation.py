import asyncio

import httpx
import pytest
from fastapi import Request

from deephelp_app.app import create_app
from deephelp_app.execution import ExecutionBudget, read_json
from deephelp_app.settings import Settings
from deephelp_app.trace import MemoryTrace, request_context

pytestmark = pytest.mark.integration


async def test_cancel_application_request_releases_real_http_pool_lease():
    """Real httpcore pool, synthetic loopback server; no cloud/model access."""
    partial_sent = asyncio.Event()
    peer_closed = asyncio.Event()
    server_cleaned = asyncio.Event()
    active_server_tasks = set()

    async def handler(reader, writer):
        task = asyncio.current_task()
        active_server_tasks.add(task)
        hanging = False
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            hanging = b"GET /hang " in header
            if hanging:
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 20\r\n\r\n{"
                )
                await writer.drain()
                partial_sent.set()
                assert await reader.read() == b""
                peer_closed.set()
            else:
                body = b'{"ok":true}'
                writer.write(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\n"
                    b"Content-Type: application/json\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            active_server_tasks.discard(task)
            if hanging:
                server_cleaned.set()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    trace = MemoryTrace()
    transport = httpx.AsyncHTTPTransport(
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1)
    )
    app = create_app(
        Settings(mode="test", child_timeout=2, http_connections=1), trace=trace, transport=transport
    )

    @app.get("/_synthetic_read")
    async def synthetic_read(request: Request):
        resources = request.app.state.resources
        return await read_json(
            resources.client, base_url + "/hang", resources.calls, request.state.budget
        )

    try:
        async with server:
            async with app.router.lifespan_context(app):
                pool = app.state.resources.client
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app), base_url="http://local"
                ) as client:
                    task = asyncio.create_task(client.get("/_synthetic_read"))
                    try:
                        async with asyncio.timeout(2):
                            await partial_sent.wait()
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                        async with asyncio.timeout(2):
                            await peer_closed.wait()
                            await server_cleaned.wait()
                        # max_connections=1: this succeeds only after the cancelled lease is freed.
                        result = await read_json(
                            pool,
                            base_url + "/ok",
                            app.state.resources.calls,
                            ExecutionBudget.start(2, 1, 0),
                        )
                        assert result == {"ok": True}
                    finally:
                        if not task.done():
                            task.cancel()
                            await asyncio.gather(task, return_exceptions=True)
            assert pool.is_closed
        assert [event.event for event in trace.events] == ["request_received", "request_cancelled"]
        assert request_context.get() is None
        assert not active_server_tasks
    finally:
        server.close()
        await server.wait_closed()
        for task in list(active_server_tasks):
            task.cancel()
        await asyncio.gather(*active_server_tasks, return_exceptions=True)
