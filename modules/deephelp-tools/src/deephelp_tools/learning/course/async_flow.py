"""Event/Future固定先后关系；不通过毫秒阈值推测调度。"""

import asyncio


async def awaitables_demo() -> dict[str, object]:
    events: list[str] = []
    started = asyncio.Event()
    future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async def lookup() -> str:
        events.append("worker_started")
        started.set()
        result = await future  # 等回调给值；当前Task暂停，loop可调度课程父任务。
        events.append("worker_received:" + result)
        return result

    coroutine = lookup()
    assert not events
    events.append("coroutine_created_not_run")
    task = asyncio.create_task(coroutine, name="course-lookup")
    await started.wait()
    assert not task.done() and not future.done()
    events.append("parent_sets_future")
    future.set_result("order_fact")
    result = await task  # 接收子任务返回值/异常，父任务负责收回自己创建的Task。
    assert result == "order_fact"
    events.append("parent_joined")
    return {"events": events, "result": result, "task_done": task.done()}


async def coordination_demo() -> dict[str, object]:
    active = peak = 0
    release = asyncio.Event()
    ready = [asyncio.Event(), asyncio.Event()]
    events: list[str] = []

    async def io(index: int) -> str:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        ready[index].set()
        try:
            await release.wait()
            return str(index)
        finally:
            active -= 1

    release.set()
    serial = [await io(0), await io(1)]
    serial_peak = peak
    peak = 0
    release.clear()
    for event in ready:
        event.clear()
    async with asyncio.TaskGroup() as group:
        first = group.create_task(io(0))
        second = group.create_task(io(1))
        for event in ready:
            await event.wait()
        assert active == 2
        release.set()
    assert serial == [first.result(), second.result()] and serial_peak == 1 and peak == 2

    started = asyncio.Event()

    async def sibling() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            events.append("group_sibling_cleaned")

    async def fail() -> None:
        await started.wait()
        raise ValueError("synthetic child failure")

    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(sibling())
            group.create_task(fail())
    except* ValueError:
        events.append("exception_group_received")
    assert events == ["group_sibling_cleaned", "exception_group_received"]

    # gather首个错误返回时其他Task仍可能运行；创建者必须明确收回它们。
    started.clear()
    continue_sibling = asyncio.Event()

    async def independent() -> str:
        started.set()
        await continue_sibling.wait()
        return "independent_done"

    other = asyncio.create_task(independent())
    bad = asyncio.create_task(fail())
    try:
        try:
            await asyncio.gather(other, bad)
        except ValueError:
            assert not other.done() and not other.cancelled()
            events.append("gather_sibling_still_pending")
        continue_sibling.set()
        assert await other == "independent_done"
    finally:
        for child in (other, bad):
            if not child.done():
                child.cancel()
        await asyncio.gather(other, bad, return_exceptions=True)

    semaphore = asyncio.Semaphore(1)
    acquired = asyncio.Event()
    unlock = asyncio.Event()
    attempts = [asyncio.Event(), asyncio.Event()]
    admitted = admission_peak = 0

    async def bounded(index: int) -> None:
        nonlocal admitted, admission_peak
        attempts[index].set()
        async with semaphore:  # 限制在执行区内的数量，不代表身份或数据库幂等。
            admitted += 1
            admission_peak = max(admission_peak, admitted)
            acquired.set()
            try:
                await unlock.wait()
            finally:
                admitted -= 1

    async with asyncio.TaskGroup() as group:
        group.create_task(bounded(0))
        group.create_task(bounded(1))
        await acquired.wait()
        for event in attempts:
            await event.wait()
        assert admitted == 1
        unlock.set()
    assert admission_peak == 1

    lock = asyncio.Lock()
    read_started, second_attempt, release_update = asyncio.Event(), asyncio.Event(), asyncio.Event()
    protected_value = readers = 0

    async def increment(index: int) -> None:
        nonlocal protected_value, readers
        if index == 1:
            second_attempt.set()
        async with lock:  # 没有Semaphore挡在前面，第二个更新者确实竞争同一临界区。
            old = protected_value
            readers += 1
            read_started.set()
            await release_update.wait()  # 第一位读后暂停；锁阻止第二位也读到旧值0。
            protected_value = old + 1

    async with asyncio.TaskGroup() as group:
        group.create_task(increment(0))
        await read_started.wait()
        group.create_task(increment(1))
        await second_attempt.wait()
        assert readers == 1 and protected_value == 0
        release_update.set()
    assert protected_value == 2
    events.append("lock_serialized_read_modify_write")
    return {
        "serial_peak": serial_peak,
        "concurrent_peak": peak,
        "events": events,
        "semaphore_peak": admission_peak,
        "locked_value": protected_value,
    }
