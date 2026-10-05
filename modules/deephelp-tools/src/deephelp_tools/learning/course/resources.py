"""固定退出栈与受控真实子进程；不连接数据库，不证明持久事务。"""

import asyncio
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager


async def resources_demo() -> dict[str, object]:
    scenarios: dict[str, list[str]] = {}

    async def stack_scenario(mode: str) -> list[str]:
        events: list[str] = []
        entered = asyncio.Event()

        @asynccontextmanager
        async def resource(name: str) -> AsyncIterator[None]:
            if mode == "partial_open" and name == "stream":
                raise ValueError("synthetic opening failure")
            events.append("open:" + name)
            try:
                yield
            finally:
                events.append("close:" + name)

        async def own() -> None:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(resource("pool"))
                await stack.enter_async_context(resource("stream"))
                entered.set()
                if mode == "error":
                    raise ValueError("synthetic body failure")
                if mode == "cancel":
                    await asyncio.Event().wait()

        if mode == "cancel":
            task = asyncio.create_task(own())
            await entered.wait()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                events.append("cancel_received")
        else:
            try:
                await own()
            except ValueError:
                events.append("error_received")
        assert "close:pool" in events
        if "open:stream" in events:
            assert events.index("close:stream") < events.index("close:pool")
        return events

    for mode in ("normal", "error", "partial_open", "cancel"):
        scenarios[mode] = await stack_scenario(mode)

    # 子进程用stdin/stdout握手，不用sleep猜它是否开始；取消Task不会自动停进程。
    started = asyncio.Event()
    events: list[str] = []
    processes: list[asyncio.subprocess.Process] = []

    async def child_owner() -> None:
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-u",
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.readline()",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            creationflags=flags,
        )
        processes.append(process)
        try:
            assert process.stdout and (await process.stdout.readline()).strip() == b"ready"
            events.append("child_ready")
            started.set()
            await asyncio.Event().wait()
        finally:
            assert process.stdin
            process.stdin.write(b"stop\n")
            await process.stdin.drain()
            process.stdin.close()
            await process.wait()  # 进程拥有者显式停止/等待，退出码才是已收回的证据。
            events.append("child_exited")

    # group也拥有启动失败路径：子任务在ready前失败，会唤醒并取消父级等待，而非空等。
    async with asyncio.TaskGroup() as group:
        task = group.create_task(child_owner())
        await started.wait()
        assert processes[0].returncode is None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            events.append("owner_cancel_received")
    assert processes[0].returncode == 0
    assert events == ["child_ready", "child_exited", "owner_cancel_received"]
    return {"stack": scenarios, "subprocess": events, "child_exit_code": processes[0].returncode}
