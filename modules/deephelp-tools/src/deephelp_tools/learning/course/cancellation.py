"""取消是请求；用屏障展示finally、shield和工作线程的完成边界。"""

import asyncio
import threading


def state(task: asyncio.Task[None]) -> dict[str, object]:
    return {"done": task.done(), "cancelled": task.cancelled(), "cancelling": task.cancelling()}


async def cancellation_demo() -> dict[str, object]:
    events: list[str] = []
    started, cleaning, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("finally_started")
            cleaning.set()
            await release_cleanup.wait()
            events.append("finally_completed")

    task = asyncio.create_task(worker())
    await started.wait()
    before = state(task)
    task.cancel()
    requested = state(task)
    await cleaning.wait()
    during = state(task)
    assert not task.done()
    release_cleanup.set()
    try:
        await task
    except asyncio.CancelledError:
        events.append("owner_received_cancel")
    after = state(task)
    assert after["cancelled"] and events[-2:] == ["finally_completed", "owner_received_cancel"]

    writer_started, commit, outer_cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    writers: list[asyncio.Task[None]] = []

    async def persist() -> None:
        writer_started.set()
        await commit.wait()
        events.append("writer_committed")

    async def request_owner() -> None:
        writer = asyncio.create_task(persist())
        writers.append(writer)  # 保留强引用，不把清理变为无主后台任务。
        try:
            await asyncio.shield(writer)
        except asyncio.CancelledError:
            events.append("outer_cancelled_writer_alive")
            outer_cancelled.set()
            await writer  # 本实验只有一次取消；二次取消仍可能打断这个普通await。
            raise

    owner = asyncio.create_task(request_owner())
    await writer_started.wait()
    owner.cancel()
    await outer_cancelled.wait()
    assert not writers[0].done() and not writers[0].cancelling()
    commit.set()
    try:
        await owner
    except asyncio.CancelledError:
        events.append("shield_owner_joined")
    assert writers[0].done() and events[-2:] == ["writer_committed", "shield_owner_joined"]

    queued = asyncio.Semaphore(0)
    try:
        async with asyncio.timeout(None) as timeout:
            events.append("queue_wait_started")
            # 已知排队开始后到期，无sleep或调度速度阈值；排队也受总期限约束。
            timeout.reschedule(asyncio.get_running_loop().time())
            await queued.acquire()
    except TimeoutError:
        events.append("queue_deadline_received")
    assert events[-1] == "queue_deadline_received"

    thread_started = asyncio.Event()
    release_thread, finished = threading.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def blocking_work() -> None:
        loop.call_soon_threadsafe(thread_started.set)
        try:
            if not release_thread.wait(5):
                raise TimeoutError("course thread release missing")
        finally:
            finished.set()

    waiting = asyncio.create_task(asyncio.to_thread(blocking_work))
    try:
        await thread_started.wait()
        waiting.cancel()
        try:
            await waiting
        except asyncio.CancelledError:
            assert not finished.is_set()
            events.append("await_cancelled_thread_still_running")
    finally:
        release_thread.set()
        assert await asyncio.to_thread(finished.wait, 5)
    events.append("thread_joined_by_owner")
    return {
        "before": before,
        "requested": requested,
        "during_finally": during,
        "after": after,
        "events": events,
    }
